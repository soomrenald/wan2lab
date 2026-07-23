from __future__ import annotations

import json
import unittest
from pathlib import Path

from wan2core.schemas import schema_bundle


class SchemaTests(unittest.TestCase):
    def test_browser_contract_bundle_is_generated_from_python_models(self) -> None:
        bundle = schema_bundle()
        self.assertEqual(
            set(bundle),
            {
                "project",
                "backend_capabilities",
                "gpu_recommendation_catalog",
                "gpu_selection_request",
                "gpu_benchmark_evidence",
                "wan_benchmark_configuration",
                "segment_request",
                "worker_request",
                "worker_event",
            },
        )
        self.assertEqual(bundle["project"]["title"], "Wan2LabProject")
        self.assertIn("$defs", bundle["backend_capabilities"])
        self.assertIn("$defs", bundle["segment_request"])

    def test_checked_in_browser_contract_fixture_is_current(self) -> None:
        fixture = Path(__file__).resolve().parents[1] / "schema" / "wan2core.schema.json"
        self.assertEqual(json.loads(fixture.read_text(encoding="utf-8")), schema_bundle())


if __name__ == "__main__":
    unittest.main()
