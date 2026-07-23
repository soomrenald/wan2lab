from __future__ import annotations

import unittest

from wan2core.backends import WanMode
from wan2core.backends.mock import MockWanBackend, default_mock_capabilities
from wan2core.orchestration import ReviewGateBlocked, WanStudioSession
from wan2core.projects import ProjectSettings, Wan2LabProject
from wan2core.segments import SegmentState
from wan2core.timeline import Timeline


class OrchestrationTests(unittest.TestCase):
    def test_mock_end_to_end_stops_at_every_review_gate(self) -> None:
        project = Wan2LabProject(
            project_id="project-1",
            project_settings=ProjectSettings(
                default_wan_backend_id="mock-wan",
                default_wan_model_id="wan-test",
            ),
            timeline=Timeline(duration_ms=11_000, output_fps=24.0),
        )
        capabilities = default_mock_capabilities()
        backend = MockWanBackend(capabilities)
        session = WanStudioSession(project)
        plan = session.plan(capabilities, model_id="wan-test")
        self.assertEqual(len(plan.segments), 3)

        progress = []
        first = session.generate_next_with_mock(backend, seed=1, progress=progress.append)
        self.assertEqual(first.source_request.mode, WanMode.PROMPT)
        with self.assertRaises(ReviewGateBlocked):
            session.generate_next_with_mock(backend, seed=2, progress=progress.append)

        session.approve_current()
        second = session.generate_next_with_mock(backend, seed=2, progress=progress.append)
        self.assertEqual(second.source_request.mode, WanMode.I2V)
        self.assertEqual(
            second.source_request.start_image_asset_id,
            first.end_frame_asset_id,
        )
        session.approve_current()
        session.generate_next_with_mock(backend, seed=3, progress=progress.append)
        session.approve_current()

        self.assertTrue(
            all(
                segment.state is SegmentState.APPROVED_LOCKED
                for segment in session.project.segments
            )
        )
        self.assertEqual(len(progress), 12)
        self.assertEqual(len(session.project.segment_revisions), 3)


if __name__ == "__main__":
    unittest.main()

