from __future__ import annotations

import tomllib
import unittest
from pathlib import Path


class PackagingTests(unittest.TestCase):
    def test_desktop_wheel_vendors_the_matching_wan2core_package(self) -> None:
        repository = Path(__file__).resolve().parents[3]
        configuration = tomllib.loads((repository / "pyproject.toml").read_text(encoding="utf-8"))
        dependencies = configuration["project"]["dependencies"]
        packages = configuration["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"]

        self.assertFalse(any(item.startswith("wan2core") for item in dependencies))
        self.assertTrue(configuration["tool"]["hatch"]["metadata"]["allow-direct-references"])
        self.assertEqual(
            packages,
            ["apps/desktop/src/wan2lab", "packages/wan2core/src/wan2core"],
        )


if __name__ == "__main__":
    unittest.main()
