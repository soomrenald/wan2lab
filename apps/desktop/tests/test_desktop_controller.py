from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from PIL import Image
from PySide6.QtCore import QUrl

from wan2core.backends import (
    BackendCapabilities,
    ParameterDescriptor,
    ParameterGroup,
    ParameterType,
    WanMode,
)
from wan2core.backends.mock import default_mock_capabilities
from wan2core.segments import SegmentState
from wan2core.workers import CapabilitiesEvent
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

    def test_segment_inspector_values_flow_into_generation_request(self) -> None:
        controller = DesktopController()
        controller.planMockTimeline()
        controller.updateSegmentInspector(0, "prompt", "camera orbit", "flicker")
        descriptor = ParameterDescriptor(
            key="steps",
            display_name="Steps",
            parameter_type=ParameterType.INTEGER,
            default=20,
            minimum=1,
            maximum=40,
            applicable_modes=frozenset({WanMode.PROMPT}),
            group=ParameterGroup.COMMON,
            backend_key="steps",
        )
        capability_payload = default_mock_capabilities().model_dump(mode="json")
        capability_payload["parameter_descriptors"] = [descriptor.model_dump(mode="json")]
        controller._handle_worker_event(  # noqa: SLF001 - exercise the Qt event boundary
            CapabilitiesEvent(
                command_id="inspect-test",
                capabilities=capability_payload,
            )
        )
        controller.setSegmentBackendParameter(0, "steps", "28")
        controller.generateNextMockSegment()

        segment = controller.session.project.segments[0]
        revision = controller.session.project.segment_revisions[0]
        self.assertEqual(segment.prompt, "camera orbit")
        self.assertEqual(segment.negative_prompt, "flicker")
        self.assertEqual(segment.parameters["steps"], 28)
        self.assertEqual(revision.source_request.prompt, "camera orbit")
        self.assertEqual(revision.source_request.negative_prompt, "flicker")
        self.assertEqual(revision.source_request.parameters["steps"], 28)
        self.assertTrue(any("prompt" in item for item in controller.timelineBlocks))

    def test_explicit_discovered_components_are_sent_to_isolated_worker(self) -> None:
        controller = DesktopController()
        controller._wan_worker.send = Mock()  # type: ignore[method-assign]  # noqa: SLF001
        base = default_mock_capabilities()
        model = base.model_variants[0].model_copy(
            update={
                "supported_precisions": ("bf16",),
                "supported_quantizations": ("disabled",),
                "supported_offload_modes": ("offload_device",),
            }
        )
        capabilities = BackendCapabilities(
            backend_id="comfy-wan",
            backend_version="1",
            accelerator_vendors=frozenset({"cuda"}),
            model_variants=(model,),
        )
        payload = capabilities.model_dump(mode="json")
        payload["component_models"] = {
            "vae": ["wan.vae"],
            "text_encoder": ["umt5.safetensors"],
        }
        controller._handle_worker_event(  # noqa: SLF001
            CapabilitiesEvent(command_id="inspect", capabilities=payload)
        )

        controller.loadLocalWanModel(
            0,
            "wan.vae",
            "umt5.safetensors",
            "bf16",
            "disabled",
            "offload_device",
        )

        request = controller._wan_worker.send.call_args.args[0]  # type: ignore[union-attr]  # noqa: SLF001
        self.assertEqual(request.component_model_ids["vae"], "wan.vae")
        self.assertEqual(request.component_model_ids["text_encoder"], "umt5.safetensors")
        self.assertEqual(
            controller.session.project.project_settings.default_wan_backend_id,
            "comfy-wan",
        )

    def test_output_fps_and_project_file_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "project.wan2lab.json"
            controller = DesktopController(asset_base=Path(directory) / "assets")
            controller.setOutputFps(30)
            controller.saveProject(str(path))
            opened = DesktopController(asset_base=Path(directory) / "opened-assets")
            opened.openProject(str(path))
            self.assertEqual(opened.outputFps, 30)
            self.assertEqual(opened.session.project.project_settings.output_fps, 30)

    def test_save_as_copies_immutable_assets_to_portable_project_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "portrait.jpg"
            Image.new("RGB", (64, 48), "teal").save(source)
            controller = DesktopController(asset_base=root / "application-assets")
            controller.importKeyframe(QUrl.fromLocalFile(str(source)), 0.0)
            project_path = root / "portable" / "shot.wan2lab.json"

            controller.saveProject(str(project_path))
            opened = DesktopController(asset_base=root / "other-application-assets")
            opened.openProject(str(project_path))

            asset = opened.session.project.assets[0]
            copied = project_path.parent / "assets" / asset.storage_path
            self.assertTrue(copied.is_file())
            self.assertEqual(copied.read_bytes(), source.read_bytes())
            self.assertNotIn("failed", opened.status.lower())

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
