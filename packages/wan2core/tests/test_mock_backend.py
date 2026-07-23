from __future__ import annotations

import unittest

from wan2core.backends import WanMode
from wan2core.backends.mock import (
    CancellationToken,
    MockGenerationCancelled,
    MockWanBackend,
)
from wan2core.segments import SegmentRequest

from helpers import backend_capabilities


def request(**updates) -> SegmentRequest:
    values = {
        "request_id": "request-1",
        "segment_id": "segment-1",
        "mode": WanMode.PROMPT,
        "backend_id": "mock-wan",
        "model_id": "wan-test",
        "start_ms": 0,
        "end_ms": 5_000,
        "width": 1280,
        "height": 720,
        "generation_fps": 16.0,
        "frame_count": 81,
    }
    values.update(updates)
    return SegmentRequest(**values)


class MockBackendTests(unittest.TestCase):
    def test_generation_is_deterministic_and_emits_typed_progress(self) -> None:
        backend = MockWanBackend(backend_capabilities())
        backend.load_model("wan-test")
        events = []
        first = backend.generate_segment(
            request(),
            job_id="job-1",
            progress=events.append,
            cancellation=CancellationToken.create(),
        )
        second = backend.generate_segment(
            request(),
            job_id="job-2",
            progress=lambda _event: None,
            cancellation=CancellationToken.create(),
        )
        self.assertEqual(first.result_asset_id, second.result_asset_id)
        self.assertEqual(len(first.frame_asset_ids), 81)
        self.assertEqual([event.stage for event in events], ["validate", "prepare", "diffusion", "encode"])

    def test_unknown_parameters_are_never_silently_ignored(self) -> None:
        backend = MockWanBackend(backend_capabilities())
        self.assertEqual(
            backend.validate_segment_request(request(parameters={"invented": 1})),
            ("unsupported parameters: invented",),
        )

    def test_cancelled_work_never_returns_a_result(self) -> None:
        backend = MockWanBackend(backend_capabilities())
        backend.load_model("wan-test")
        token = CancellationToken.create()
        token.cancel()
        with self.assertRaises(MockGenerationCancelled):
            backend.generate_segment(
                request(),
                job_id="job-1",
                progress=lambda _event: None,
                cancellation=token,
            )


if __name__ == "__main__":
    unittest.main()

