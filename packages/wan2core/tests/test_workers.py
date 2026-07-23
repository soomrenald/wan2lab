from __future__ import annotations

import unittest

from pydantic import ValidationError

from wan2core.backends import WanMode
from wan2core.workers import (
    GenerateSegmentRequest,
    ProgressEvent,
    WanCommandKind,
    parse_worker_event,
    parse_worker_request,
)


class WorkerContractTests(unittest.TestCase):
    def test_discriminated_request_parses_to_typed_generation_request(self) -> None:
        request = parse_worker_request(
            {
                "kind": WanCommandKind.GENERATE_SEGMENT,
                "command_id": "command-1",
                "job_id": "job-1",
                "seed": 7,
                "asset_inputs": {},
                "output_prefix": "wan2lab/segment-1/revision-1",
                "request": {
                    "request_id": "request-1",
                    "segment_id": "segment-1",
                    "mode": WanMode.PROMPT,
                    "backend_id": "mock-wan",
                    "model_id": "wan-test",
                    "start_ms": 0,
                    "end_ms": 1000,
                    "width": 1280,
                    "height": 720,
                    "generation_fps": 16,
                    "frame_count": 17,
                },
            }
        )
        self.assertIsInstance(request, GenerateSegmentRequest)
        self.assertEqual(request.request.frame_count, 17)

    def test_discriminated_progress_event_round_trips(self) -> None:
        event = parse_worker_event(
            {
                "kind": "progress",
                "command_id": "command-1",
                "progress": {
                    "job_id": "job-1",
                    "segment_id": "segment-1",
                    "stage": "diffusion",
                    "current": 4,
                    "total": 30,
                },
            }
        )
        self.assertIsInstance(event, ProgressEvent)
        self.assertEqual(event.progress.current, 4)

    def test_worker_paths_cannot_escape_workspace(self) -> None:
        with self.assertRaises(ValidationError):
            parse_worker_request(
                {
                    "kind": "generate_wan_segment",
                    "command_id": "command-1",
                    "job_id": "job-1",
                    "seed": 1,
                    "asset_inputs": {"source": "../secret.png"},
                    "output_prefix": "output/revision",
                    "request": {
                        "request_id": "request-1",
                        "segment_id": "segment-1",
                        "mode": "prompt",
                        "backend_id": "mock-wan",
                        "model_id": "wan-test",
                        "start_ms": 0,
                        "end_ms": 1000,
                        "width": 1280,
                        "height": 720,
                        "generation_fps": 16,
                        "frame_count": 17,
                    },
                }
            )

    def test_unknown_worker_command_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            parse_worker_request({"kind": "pretend_success", "command_id": "bad"})


if __name__ == "__main__":
    unittest.main()
