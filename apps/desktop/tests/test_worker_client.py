from __future__ import annotations

import unittest

from wan2core.workers import AckEvent
from wan2lab.worker_client import JsonLineDecoder


class WorkerClientTests(unittest.TestCase):
    def test_json_line_decoder_handles_partial_process_reads(self) -> None:
        first = AckEvent(command_id="command-1", message="one").model_dump_json().encode()
        second = AckEvent(command_id="command-2", message="two").model_dump_json().encode()
        decoder = JsonLineDecoder()
        self.assertEqual(decoder.feed(first[:10]), ())
        events = decoder.feed(first[10:] + b"\n" + second + b"\n")
        self.assertEqual([item.command_id for item in events], ["command-1", "command-2"])


if __name__ == "__main__":
    unittest.main()
