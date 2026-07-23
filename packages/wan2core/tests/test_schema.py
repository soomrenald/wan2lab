from __future__ import annotations

import unittest

from wan2core.schemas import schema_bundle


class SchemaTests(unittest.TestCase):
    def test_browser_contract_bundle_is_generated_from_python_models(self) -> None:
        bundle = schema_bundle()
        self.assertEqual(set(bundle), {"project", "backend_capabilities", "segment_request"})
        self.assertEqual(bundle["project"]["title"], "Wan2LabProject")
        self.assertIn("$defs", bundle["backend_capabilities"])
        self.assertIn("$defs", bundle["segment_request"])


if __name__ == "__main__":
    unittest.main()

