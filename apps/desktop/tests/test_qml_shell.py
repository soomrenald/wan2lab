from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QGuiApplication

from wan2core.backends import (
    ParameterDescriptor,
    ParameterGroup,
    ParameterType,
    WanMode,
)
from wan2core.backends.mock import default_mock_capabilities
from wan2core.workers import CapabilitiesEvent
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

    def test_capability_driven_parameter_editors_load(self) -> None:
        controller = DesktopController()
        controller.planMockTimeline()
        descriptors = (
            ParameterDescriptor(
                key="force_offload",
                display_name="Force offload",
                parameter_type=ParameterType.BOOLEAN,
                default=True,
                applicable_modes=frozenset({WanMode.PROMPT}),
                group=ParameterGroup.COMMON,
                backend_key="sampler.force_offload",
            ),
            ParameterDescriptor(
                key="scheduler",
                display_name="Scheduler",
                parameter_type=ParameterType.ENUM,
                default="unipc",
                choices=("unipc", "dpm++"),
                applicable_modes=frozenset({WanMode.PROMPT}),
                group=ParameterGroup.ADVANCED,
                backend_key="sampler.scheduler",
            ),
        )
        payload = default_mock_capabilities().model_copy(
            update={"parameter_descriptors": descriptors}
        ).model_dump(mode="json")
        controller._handle_worker_event(  # noqa: SLF001
            CapabilitiesEvent(command_id="inspect-qml", capabilities=payload)
        )

        engine = build_engine(controller)

        self.assertTrue(engine.rootObjects())
        self.assertEqual(len(controller.backendCommonParameterDescriptors), 1)
        self.assertEqual(len(controller.backendAdvancedParameterDescriptors), 1)


if __name__ == "__main__":
    unittest.main()
