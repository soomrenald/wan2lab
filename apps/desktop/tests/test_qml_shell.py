from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QGuiApplication

from wan2lab.app import build_engine
from wan2lab.controller import DesktopController


class QmlShellTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QGuiApplication.instance() or QGuiApplication([])

    def test_main_workspace_loads(self) -> None:
        controller = DesktopController()
        engine = build_engine(controller)
        self.assertTrue(engine.rootObjects())
        self.assertIn("Wan2Lab", engine.rootObjects()[0].property("title"))


if __name__ == "__main__":
    unittest.main()

