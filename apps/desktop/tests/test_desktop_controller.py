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
from wan2core.editing import FrameEditOperation
from wan2core.keyframes import Rectangle
from wan2core.segments import SegmentState
from wan2core.workers import AckEvent, CapabilitiesEvent, ResultEvent, WorkerResult
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

    def test_krea_worker_result_registers_generated_sheet_entry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result_root = root / "krea-results"
            controller = DesktopController(
                asset_base=root / "projects",
                krea_result_root=result_root,
            )
            controller.addCharacter(
                "Avery",
                "stable Avery identity",
                "Travel",
                "blue jacket",
            )
            controller._krea_loaded = True  # noqa: SLF001
            controller._krea_worker.send = Mock(return_value="krea-generate")  # type: ignore[method-assign]  # noqa: SLF001
            controller.generateCharacterSheetEntry("three-quarter", "looking left")
            result = result_root / "three-quarter.png"
            result.parent.mkdir(parents=True)
            Image.new("RGB", (1280, 720), "navy").save(result)

            controller._handle_krea_event(  # noqa: SLF001
                {
                    "command_id": "krea-generate",
                    "state": "complete",
                    "message": "complete",
                    "payload": {
                        "asset_paths": [str(result)],
                        "metadata": {"seed": 1},
                    },
                }
            )

            entry = controller.session.project.character_sheets[0].entries[0]
            self.assertEqual(entry.name, "three-quarter")
            self.assertEqual(entry.source_type.value, "generated")
            self.assertEqual(len(controller.session.project.assets), 1)
            self.assertIn("immutable draft", controller.status.lower())

    def test_sheet_entry_review_rename_and_remove_preserve_asset(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.png"
            Image.new("RGB", (64, 64), "green").save(source)
            controller = DesktopController(asset_base=root / "projects")
            controller.addCharacter("Avery", "Avery identity", "Travel", "green coat")
            controller.importSheetEntry(QUrl.fromLocalFile(str(source)), "front")
            asset_id = controller.session.project.assets[0].asset_id

            controller.reviewSheetEntry(0, 0, "front smiling", "approved")
            entry = controller.session.project.character_sheets[0].entries[0]
            self.assertEqual(entry.name, "front smiling")
            self.assertEqual(entry.approval_state.value, "approved")
            controller.removeSheetEntry(0, 0)

            self.assertEqual(controller.session.project.character_sheets[0].entries, ())
            self.assertEqual(controller.session.project.assets[0].asset_id, asset_id)
            self.assertIn("non-destructively", controller.status.lower())

    def test_krea_style_duplication_runs_sequentially_and_preserves_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result_root = root / "krea-results"
            controller = DesktopController(
                asset_base=root / "projects",
                krea_result_root=result_root,
            )
            controller.addCharacter("Avery", "Avery identity", "Travel", "blue jacket")
            for index, color in enumerate(("blue", "green")):
                source = root / f"source-{index}.png"
                Image.new("RGB", (64, 64), color).save(source)
                controller.importSheetEntry(
                    QUrl.fromLocalFile(str(source)),
                    f"pose-{index}",
                )
            source_asset_ids = tuple(
                item.image_asset_id
                for item in controller.session.project.character_sheets[0].entries
            )
            controller._krea_loaded = True  # noqa: SLF001
            controller._krea_worker.send = Mock(  # type: ignore[method-assign]  # noqa: SLF001
                side_effect=("restyle-1", "restyle-2")
            )
            controller.duplicateSheetAppearance(0, "Formal", "black suit")
            self.assertEqual(controller._krea_worker.send.call_count, 1)  # type: ignore[union-attr]  # noqa: SLF001

            for index, command_id in enumerate(("restyle-1", "restyle-2"), start=1):
                result = result_root / f"restyled-{index}.png"
                result.parent.mkdir(parents=True, exist_ok=True)
                Image.new("RGB", (64, 64), "black").save(result)
                controller._handle_krea_event(  # noqa: SLF001
                    {
                        "command_id": command_id,
                        "state": "complete",
                        "message": "complete",
                        "payload": {"asset_paths": [str(result)], "metadata": {}},
                    }
                )

            project = controller.session.project
            self.assertEqual(controller._krea_worker.send.call_count, 2)  # type: ignore[union-attr]  # noqa: SLF001
            self.assertEqual(len(project.character_sheets), 2)
            self.assertEqual(
                tuple(item.image_asset_id for item in project.character_sheets[0].entries),
                source_asset_ids,
            )
            target_entries = project.character_sheets[1].entries
            self.assertTrue(
                all(item.parent_entry_id is not None for item in target_entries)
            )
            self.assertTrue(all(item.approval_state.value == "draft" for item in target_entries))
            self.assertIn("Restyled sheet saved", controller.status)

    def test_multi_character_regional_keyframe_requires_explicit_approval(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result_root = root / "krea-results"
            controller = DesktopController(
                asset_base=root / "projects",
                krea_result_root=result_root,
            )
            controller.addCharacter("Avery", "Avery identity", "Travel", "blue jacket")
            controller.addCharacter("Blake", "Blake identity", "Formal", "black suit")
            for index, color in enumerate(("blue", "black")):
                source = root / f"source-{index}.png"
                Image.new("RGB", (256, 256), color).save(source)
                controller.importSheetEntryForSheet(
                    index,
                    QUrl.fromLocalFile(str(source)),
                    "front",
                )
            controller._krea_loaded = True  # noqa: SLF001
            controller._krea_worker.send = Mock(return_value="krea-keyframe")  # type: ignore[method-assign]  # noqa: SLF001
            controller.addKeyframeRegion(0, 0, 0, 0, 640, 720, "walking")
            controller.addKeyframeRegion(1, 0, 640, 0, 1280, 720, "turning")
            controller.generateRegionalKeyframe(
                0.0,
                "city street",
                "wet pavement",
                "golden hour",
            )
            request_payload = controller._krea_worker.send.call_args.args[1]  # type: ignore[union-attr]  # noqa: SLF001
            result = result_root / "regional.png"
            result.parent.mkdir(parents=True)
            Image.new("RGB", (1280, 720), "purple").save(result)
            controller._handle_krea_event(  # noqa: SLF001
                {
                    "command_id": "krea-keyframe",
                    "state": "complete",
                    "message": "complete",
                    "payload": {"asset_paths": [str(result)], "metadata": {}},
                }
            )

            keyframe = controller.session.project.keyframes[0]
            self.assertEqual(len(request_payload["request"]["regions"]), 2)
            self.assertEqual(keyframe.environment_prompt, "wet pavement")
            self.assertFalse(keyframe.approved)
            controller.planMockTimeline()
            self.assertIn("not approved", controller.status.lower())
            controller.approveKeyframe(0)
            controller.planMockTimeline()
            self.assertGreater(controller.segmentCount, 0)

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

    def test_completed_frame_modification_creates_a_new_reviewable_revision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            controller = DesktopController(asset_base=root / "projects")
            controller.planMockTimeline()
            controller.generateNextMockSegment()
            source_revision = controller.session.project.segment_revisions[0]
            original = root / "original.png"
            replacement = root / "replacement.png"
            revised = root / "revised.mp4"
            size = (
                source_revision.source_request.width,
                source_revision.source_request.height,
            )
            Image.new("RGB", size, "blue").save(original)
            Image.new("RGB", size, "red").save(replacement)
            revised.write_bytes(b"immutable revised video")
            controller._active_frame_edit = {  # noqa: SLF001
                "segment_id": source_revision.segment_id,
                "revision_id": source_revision.revision_id,
                "source_video_asset_id": source_revision.result_asset_id,
                "frame_index": 0,
                "prompt": "repair the first frame",
                "propagate": True,
            }

            controller._complete_frame_modification(  # noqa: SLF001
                str(original),
                str(replacement),
                str(revised),
            )

            project = controller.session.project
            self.assertEqual(len(project.segment_revisions), 2)
            self.assertEqual(project.segment_revisions[0].review_state.value, "superseded")
            self.assertEqual(project.segment_revisions[1].review_state.value, "ready_for_review")
            self.assertEqual(len(project.frame_edit_records), 1)
            self.assertEqual(
                project.segment_revisions[1].start_frame_asset_id,
                project.frame_edit_records[0].replacement_frame_asset_id,
            )
            self.assertIn("mandatory review", controller.status.lower())

    def test_confirmed_krea_face_result_starts_typed_revision_assembly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result_root = root / "krea-results"
            controller = DesktopController(
                asset_base=root / "projects",
                krea_result_root=result_root,
            )
            controller.planMockTimeline()
            controller.generateNextMockSegment()
            revision = controller.session.project.segment_revisions[0]
            source_file = root / "source.mp4"
            source_file.write_bytes(b"source video")
            stored = controller._asset_store.register_generated(  # noqa: SLF001
                source_file,
                media_type="video/mp4",
            )
            assets = tuple(
                item.model_copy(
                    update={
                        "storage_path": stored.relative_path,
                        "sha256": stored.sha256,
                    }
                )
                if item.asset_id == revision.result_asset_id
                else item
                for item in controller.session.project.assets
            )
            controller.session.project = controller.session.project.model_copy(
                update={"assets": assets}
            )
            region = Rectangle(x0=400, y0=120, x1=880, y1=600)
            frame_context = {
                "segment_index": 0,
                "segment_id": revision.segment_id,
                "revision_id": revision.revision_id,
                "source_video_asset_id": revision.result_asset_id,
                "frame_index": 0,
                "prompt": "repair face",
                "propagate": True,
                "operation_type": FrameEditOperation.FACE_REFINEMENT,
                "region": region,
                "user_confirmed_face_region": True,
            }
            controller._pending_krea_frame_edit = frame_context  # noqa: SLF001
            controller._pending_krea_jobs["face-job"] = {  # noqa: SLF001
                "operation": "frame_edit_replacement",
                "frame_context": frame_context,
                "request": {},
            }
            controller._frame_runner.start = Mock()  # type: ignore[method-assign]  # noqa: SLF001
            result = result_root / "face.png"
            result.parent.mkdir(parents=True)
            Image.new("RGB", (1280, 720), "orange").save(result)

            controller._handle_krea_event(  # noqa: SLF001
                {
                    "command_id": "face-job",
                    "state": "complete",
                    "message": "complete",
                    "payload": {"asset_paths": [str(result)]},
                }
            )

            context = controller._active_frame_edit  # noqa: SLF001
            self.assertIsNotNone(context)
            self.assertEqual(
                context["operation_type"],
                FrameEditOperation.FACE_REFINEMENT,
            )
            self.assertEqual(context["region"], region)
            self.assertTrue(context["user_confirmed_face_region"])
            controller._frame_runner.start.assert_called_once()  # type: ignore[union-attr]  # noqa: SLF001

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
        controller._handle_worker_event(  # noqa: SLF001
            AckEvent(command_id=request.command_id, message="model ready")
        )
        self.assertEqual(request.component_model_ids["vae"], "wan.vae")
        self.assertEqual(request.component_model_ids["text_encoder"], "umt5.safetensors")
        self.assertEqual(
            controller.session.project.project_settings.default_wan_backend_id,
            "comfy-wan",
        )

    def test_local_worker_result_becomes_an_immutable_reviewable_video(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            controller = DesktopController(
                asset_base=root / "projects",
                comfy_input_root=root / "comfy-input",
                comfy_output_root=root / "comfy-output",
            )
            controller._wan_worker.send = Mock()  # type: ignore[method-assign]  # noqa: SLF001
            base = default_mock_capabilities()
            model = base.model_variants[0].model_copy(
                update={
                    "supported_precisions": ("bf16",),
                    "supported_quantizations": ("disabled",),
                    "supported_offload_modes": ("offload_device",),
                }
            )
            capabilities = base.model_copy(
                update={"backend_id": "comfy-wan", "model_variants": (model,)}
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
            load_request = controller._wan_worker.send.call_args.args[0]  # type: ignore[union-attr]  # noqa: SLF001
            controller._handle_worker_event(  # noqa: SLF001
                AckEvent(command_id=load_request.command_id, message="model ready")
            )
            controller.planMockTimeline()
            controller.generateNextMockSegment()
            generate_request = controller._wan_worker.send.call_args.args[0]  # type: ignore[union-attr]  # noqa: SLF001
            output = root / "comfy-output" / "wan2lab" / "revision.mp4"
            output.parent.mkdir(parents=True)
            output.write_bytes(b"typed video output")

            controller._handle_worker_event(  # noqa: SLF001
                ResultEvent(
                    command_id=generate_request.command_id,
                    result=WorkerResult(
                        job_id=generate_request.job_id,
                        result_asset_id="worker-video",
                        metadata={
                            "output_storage_keys": (
                                "output/wan2lab/revision.mp4",
                            ),
                            "resolved_parameters": {},
                        },
                    ),
                )
            )

            revision = controller.session.project.segment_revisions[0]
            result_asset = next(
                item
                for item in controller.session.project.assets
                if item.asset_id == revision.result_asset_id
            )
            self.assertEqual(revision.review_state.value, "ready_for_review")
            self.assertTrue(result_asset.storage_path.startswith("objects/"))
            self.assertFalse(controller.generationRunning)
            self.assertIn("ready for review", controller.status.lower())

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
