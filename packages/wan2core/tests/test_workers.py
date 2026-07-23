from __future__ import annotations

import unittest

from pydantic import ValidationError

from wan2core.backends import WanMode
from wan2core.workers import GenerateSegmentRequest, WanCommandKind, parse_worker_request


class WorkerContractTests(unittest.TestCase):
    def test_discriminated_request_parses_to_typed_generation_request(self) -> None:
        request = parse_worker_request(
            {
                "kind": WanCommandKind.GENERATE_SEGMENT,
                "command_id": "command-1",
                "job_id": "job-1",
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

    def test_unknown_worker_command_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            parse_worker_request({"kind": "pretend_success", "command_id": "bad"})


if __name__ == "__main__":
    unittest.main()

