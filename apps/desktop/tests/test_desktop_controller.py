from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QUrl

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

    def test_character_sheet_and_exact_time_keyframe_imports_are_immutable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.png"
            Image.new("RGB", (512, 512), "purple").save(source)
            controller = DesktopController(asset_base=root / "projects")
            controller.addCharacter(
                "Avery",
                "averyface, stable facial identity",
                "Travel clothes",
                "blue jacket",
            )
            controller.importSheetEntry(QUrl.fromLocalFile(str(source)), "front_neutral_full")
            controller.importKeyframe(QUrl.fromLocalFile(str(source)), 3.0)

            project = controller.session.project
            self.assertEqual(controller.characterNames, ["Avery"])
            self.assertEqual(len(project.character_sheets[0].entries), 1)
            self.assertEqual(project.keyframes[0].time_ms, 3_000)
            self.assertEqual(len(project.assets), 2)
            self.assertTrue(all(asset.storage_path.startswith("objects/") for asset in project.assets))
            self.assertEqual(len(tuple((root / "projects").rglob("*.png"))), 2)

    def test_reject_and_regenerate_create_a_new_reviewable_revision(self) -> None:
        controller = DesktopController()
        controller.planMockTimeline()
        controller.generateNextMockSegment()
        controller.rejectCurrentSegment("visible flicker")
        self.assertEqual(controller.session.project.segments[0].state, SegmentState.REJECTED)
        controller.regenerateRejectedMockSegment()
        revisions = controller.session.project.segment_revisions
        self.assertEqual(len(revisions), 2)
        self.assertEqual(revisions[-1].parent_revision_id, revisions[0].revision_id)
        self.assertEqual(
            controller.session.project.segments[0].state,
            SegmentState.READY_FOR_REVIEW,
        )

    def test_integrated_mannequin_pose_guides_and_blender_import(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            controller = DesktopController(asset_base=root / "projects")
            controller.createMannequinScene("Wave setup")
            controller.setMannequinArmPose(65.0, -20.0)
            controller.setMannequinFocalLength(70.0)
            controller.saveCurrentMannequinPose("Wave")
            controller.renderCurrentMannequinGuides()

            project = controller.session.project
            self.assertEqual(controller.mannequinNames, ["Wave setup"])
            self.assertEqual(controller.mannequinPoseNames, ["Wave"])
            self.assertEqual(len(project.mannequin_scenes[0].guide_asset_ids), 3)
            self.assertEqual(len(controller.mannequinGuideLabels), 3)
            self.assertIn("i2i_scaffold", controller.mannequinConditioningPath)
            self.assertTrue(controller.mannequinPreviewUrl.isLocalFile())

            source = root / "blender-scene.json"
            source.write_text(
                project.mannequin_scenes[0]
                .model_copy(update={"guide_asset_ids": ()})
                .model_dump_json(),
                encoding="utf-8",
            )
            imported = DesktopController(asset_base=root / "imported-projects")
            imported.importBlenderScene(QUrl.fromLocalFile(str(source)))
            self.assertEqual(imported.session.project.mannequin_scenes[0].source_type.value, "blender")
            self.assertEqual(len(imported.session.project.assets), 1)


if __name__ == "__main__":
    unittest.main()
