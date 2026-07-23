from __future__ import annotations

import unittest

from wan2core.segments import SegmentState
from wan2lab.controller import DesktopController


class DesktopControllerTests(unittest.TestCase):
    def test_mock_workflow_exposes_review_gate_to_qt(self) -> None:
        controller = DesktopController()
        controller.newProject(11.0)
        controller.planMockTimeline()
        self.assertEqual(controller.segmentCount, 3)
        controller.generateNextMockSegment()
        self.assertIn("ready for review", controller.status.lower())
        controller.generateNextMockSegment()
        self.assertIn("requires review", controller.status.lower())
        controller.approveCurrentSegment()
        self.assertEqual(controller.approvedSegmentCount, 1)
        self.assertEqual(
            controller.session.project.segments[0].state,
            SegmentState.APPROVED_LOCKED,
        )
        self.assertIn("wan2core", controller.runtimeVersions)
        self.assertIn("k2core", controller.runtimeVersions)


if __name__ == "__main__":
    unittest.main()

