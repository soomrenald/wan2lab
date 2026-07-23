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
from wan2core.editing import BatchFrameSelection, FrameEditOperation
from wan2core.editing.faces import FaceProposal, confirm_face_proposal
from wan2core.identity import IdentityDriftWarning, IdentityWarningKind
from wan2core.identity.workflows import (
    propose_checkpoint_from_warnings,
    register_identity_analysis,
)
from wan2core.keyframes import Rectangle
from wan2core.mannequin.workflows import GuideKind
from wan2core.segments import SegmentState
from wan2core.workers import AckEvent, CapabilitiesEvent, ResultEvent, WorkerResult
from wan2core.workers import ReleaseAllModelsRequest, RuntimeStatusRequest
from wan2lab.controller import DesktopController


class DesktopControllerTests(unittest.TestCase):
    def test_detailed_identity_and_appearance_metadata_remain_separate(self) -> None:
        controller = DesktopController()
        controller.addCharacter("Avery", "Avery identity", "Travel", "blue jacket")

        controller.updateCharacterProfile(
            0,
            "same stable person",
            "oval face and medium brown hair",
            "avery_token",
            "arm tattoo, small scar",
            "formal evening look",
            "black suit",
            "hair tied back",
            "silver earrings",
            "arm tattoo",
            "clothed",
        )

        identity = controller.session.project.characters[0]
        appearance = controller.session.project.appearance_profiles[0]
        self.assertEqual(identity.identity_prompt, "same stable person")
        self.assertEqual(identity.permanent_features, ("arm tattoo", "small scar"))
        self.assertEqual(identity.trigger_text, "avery_token")
        self.assertEqual(appearance.clothing_state, "black suit")
        self.assertEqual(appearance.hairstyle_state, "hair tied back")
        self.assertEqual(appearance.nudity_state, "clothed")
        self.assertNotIn("black suit", identity.stable_description)

    def test_runtime_diagnostics_and_explicit_release_use_typed_worker_commands(self) -> None:
        controller = DesktopController()
        controller._wan_worker.send = Mock()  # type: ignore[method-assign]  # noqa: SLF001

        controller.inspectWanRuntimeStatus()
        controller.releaseAllModels()

        commands = [item.args[0] for item in controller._wan_worker.send.call_args_list]  # type: ignore[union-attr]  # noqa: SLF001
        self.assertIsInstance(commands[0], RuntimeStatusRequest)
        self.assertIsInstance(commands[1], ReleaseAllModelsRequest)
        controller._handle_worker_event(  # noqa: SLF001
            AckEvent(command_id=commands[1].command_id, message="released")
        )
        self.assertIn("released", controller.status)

    def test_character_adapters_are_immutable_and_model_family_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            adapter_file = root / "identity.safetensors"
            reference_image = root / "reference.png"
            adapter_file.write_bytes(b"immutable adapter weights")
            Image.new("RGB", (256, 256), "blue").save(reference_image)
            controller = DesktopController(asset_base=root / "projects")
            controller.addCharacter("Avery", "avery person", "Travel", "blue jacket")

            controller.importCharacterAdapter(
                0,
                "identity",
                QUrl.fromLocalFile(str(adapter_file)),
                "krea",
                "lora",
                "krea2",
                "avery_token",
                0.8,
            )

            identity = controller.session.project.characters[0]
            imported = identity.adapter_refs[0]
            asset = next(
                item
                for item in controller.session.project.assets
                if item.asset_id == imported.asset_id
            )
            self.assertEqual(asset.kind.value, "adapter")
            self.assertEqual(imported.trigger, "avery_token")
            self.assertTrue(controller._asset_store.resolve_ref(asset).is_file())  # noqa: SLF001
            self.assertEqual(len(controller.characterAdapterLabels), 1)
            controller.importSheetEntryForSheet(
                0,
                QUrl.fromLocalFile(str(reference_image)),
                "front",
            )
            controller.addKeyframeRegionWithAdapters(
                0,
                0,
                0,
                0,
                640,
                720,
                "walking",
                f"{imported.adapter_id}=0.65",
            )
            self.assertEqual(
                controller._draft_keyframe_regions[0].adapters[0].strength,  # noqa: SLF001
                0.65,
            )
            self.assertEqual(controller.projectWidth, 1280)
            self.assertEqual(controller.projectHeight, 720)
            self.assertEqual(controller.keyframeRegionRectangles[0]["x1"], 640)

            controller.importCharacterAdapter(
                0,
                "appearance",
                QUrl.fromLocalFile(str(adapter_file)),
                "wan",
                "lora",
                "wan2.2",
                "",
                1.0,
            )
            self.assertIn("appearance adapters", controller.status)
            self.assertFalse(controller.session.project.appearance_profiles[0].adapter_refs)

    def test_review_player_properties_resolve_latest_immutable_segment_video(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            controller = DesktopController(asset_base=root / "projects")
            controller.planMockTimeline()
            controller.generateNextMockSegment()
            revision = controller.session.project.segment_revisions[0]
            video = root / "review.mp4"
            video.write_bytes(b"review video placeholder")
            stored = controller._asset_store.register_generated(  # noqa: SLF001
                video,
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

            controller.selectReviewSegment(0)

            self.assertTrue(controller.reviewVideoUrl.isLocalFile())
            self.assertEqual(controller.reviewFrameCount, revision.source_request.frame_count)
            self.assertEqual(len(controller.reviewFrameLabels), controller.reviewFrameCount)
            self.assertIn("Revision 1", controller.reviewMetadata)
            self.assertIn("mock-wan/wan-test", controller.reviewMetadata)
            self.assertIn("seed 1", controller.reviewMetadata)

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

    def test_confirmed_keyframe_face_refinement_creates_review_draft(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result_root = root / "krea-results"
            source = root / "source.png"
            reference = root / "reference.png"
            Image.new("RGB", (128, 128), "blue").save(source)
            Image.new("RGB", (128, 128), "purple").save(reference)
            controller = DesktopController(
                asset_base=root / "projects",
                krea_result_root=result_root,
            )
            controller.addCharacter(
                "Avery",
                "averyface, stable identity",
                "Travel clothes",
                "blue jacket",
            )
            controller.importSheetEntry(
                QUrl.fromLocalFile(str(reference)),
                "front neutral",
            )
            controller.importKeyframe(QUrl.fromLocalFile(str(source)), 2.0)
            original = controller.session.project.keyframes[0]
            controller._krea_loaded = True  # noqa: SLF001
            controller._krea_worker.send = Mock(return_value="refine-keyframe")  # noqa: SLF001

            controller.refineKeyframeFace(0, 0, 0, 10, 12, 80, 90, "natural detail")

            request = controller._krea_worker.send.call_args.args[1]["request"]  # noqa: SLF001
            self.assertEqual(request["operation"], "face_refinement")
            self.assertTrue(request["user_confirmed_face_region"])
            result_root.mkdir(parents=True)
            result = result_root / "refined.png"
            Image.new("RGB", (128, 128), "green").save(result)
            controller._complete_krea_job(  # noqa: SLF001
                "refine-keyframe",
                {"asset_paths": [str(result)]},
            )

            project = controller.session.project
            refined = project.keyframes[0]
            self.assertEqual(refined.parent_keyframe_id, original.keyframe_id)
            self.assertEqual(refined.source_frame_asset_id, original.image_asset_id)
            self.assertEqual(refined.source_type.value, "edited")
            self.assertFalse(refined.approved)
            self.assertIn(original.image_asset_id, {item.asset_id for item in project.assets})

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

    def test_regional_keyframe_uses_explicit_approved_i2i_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference = root / "reference.png"
            source = root / "source.png"
            Image.new("RGB", (256, 256), "blue").save(reference)
            Image.new("RGB", (1280, 720), "green").save(source)
            controller = DesktopController(asset_base=root / "projects")
            controller.addCharacter("Avery", "Avery identity", "Travel", "blue jacket")
            controller.importSheetEntryForSheet(
                0,
                QUrl.fromLocalFile(str(reference)),
                "front",
            )
            controller.addKeyframeRegion(0, 0, 0, 0, 640, 720, "walking")
            controller.importKeyframe(QUrl.fromLocalFile(str(source)), 0.0)
            controller._krea_loaded = True  # noqa: SLF001
            controller._krea_worker.send = Mock(return_value="derived-keyframe")  # type: ignore[method-assign]  # noqa: SLF001

            controller.generateRegionalKeyframeFromSource(
                4.0,
                "city street",
                "wet pavement",
                "golden hour",
                1,
            )

            request_payload = controller._krea_worker.send.call_args.args[1]  # type: ignore[union-attr]  # noqa: SLF001
            self.assertEqual(len(controller.keyframeSourceLabels), 2)
            self.assertEqual(
                request_payload["request"]["source_asset_id"],
                controller.session.project.keyframes[0].image_asset_id,
            )
            self.assertEqual(request_payload["request"]["operation"], "edit_image")

    def test_keyframe_retime_preserves_asset_and_requires_replanning(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.png"
            Image.new("RGB", (1280, 720), "green").save(source)
            controller = DesktopController(asset_base=root / "projects")
            controller.importKeyframe(QUrl.fromLocalFile(str(source)), 3.0)
            original = controller.session.project.keyframes[0]
            controller.planMockTimeline()

            controller.retimeKeyframe(0, 4.25)

            moved = controller.session.project.keyframes[0]
            self.assertEqual(moved.keyframe_id, original.keyframe_id)
            self.assertEqual(moved.image_asset_id, original.image_asset_id)
            self.assertEqual(moved.provenance_id, original.provenance_id)
            self.assertEqual(moved.time_ms, 4_250)
            self.assertIsNone(controller.session.project.segment_plan)
            self.assertIsNone(controller.session.segment_plan)

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

    def test_generated_segment_changes_mark_stale_and_regenerate_as_child(self) -> None:
        controller = DesktopController()
        controller.planMockTimeline()
        controller.generateNextMockSegment()
        original = controller.session.project.segment_revisions[0]

        controller.updateSegmentInspector(0, "prompt", "revised camera orbit", "flicker")

        stale = controller.session.project.segments[0]
        self.assertEqual(stale.state, SegmentState.STALE)
        self.assertEqual(stale.revision_ids, (original.revision_id,))
        self.assertIn("prompt", stale.stale_reason)

        controller.regenerateRejectedMockSegment()

        revisions = controller.session.project.segment_revisions
        self.assertEqual(len(revisions), 2)
        self.assertEqual(revisions[-1].parent_revision_id, original.revision_id)
        self.assertEqual(revisions[-1].source_request.prompt, "revised camera orbit")
        self.assertEqual(
            controller.session.project.segments[0].state,
            SegmentState.READY_FOR_REVIEW,
        )

    def test_review_revision_selector_inspects_preserved_history(self) -> None:
        controller = DesktopController()
        controller.planMockTimeline()
        controller.generateNextMockSegment()
        controller.rejectCurrentSegment("visible flicker")
        controller.regenerateRejectedMockSegment()

        self.assertEqual(len(controller.reviewRevisionLabels), 2)
        self.assertEqual(controller.reviewRevisionIndex, 1)
        self.assertIn("Revision 2", controller.reviewMetadata)

        controller.selectReviewRevision(0)

        self.assertEqual(controller.reviewRevisionIndex, 0)
        self.assertIn("Revision 1", controller.reviewMetadata)
        self.assertIn("rejected", controller.reviewMetadata)

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

    def test_completed_batch_modification_commits_one_reviewable_revision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            controller = DesktopController(asset_base=root / "projects")
            controller.planMockTimeline()
            controller.generateNextMockSegment()
            source_revision = controller.session.project.segment_revisions[0]
            size = (
                source_revision.source_request.width,
                source_revision.source_request.height,
            )
            originals = (root / "original-0.png", root / "original-2.png")
            replacements = (root / "replacement-0.png", root / "replacement-2.png")
            for path in originals:
                Image.new("RGB", size, "blue").save(path)
            for path in replacements:
                Image.new("RGB", size, "red").save(path)
            revised = root / "revised.mp4"
            revised.write_bytes(b"immutable batch-revised video")
            controller._active_batch_frame_edit = {  # noqa: SLF001
                "segment_id": source_revision.segment_id,
                "revision_id": source_revision.revision_id,
                "source_video_asset_id": source_revision.result_asset_id,
                "selection": BatchFrameSelection(frame_indices=(0, 2)),
                "prompt": "repair identity consistency",
            }

            controller._complete_batch_frame_modification(  # noqa: SLF001
                tuple(str(path) for path in originals),
                tuple(str(path) for path in replacements),
                str(revised),
            )

            project = controller.session.project
            self.assertEqual(len(project.segment_revisions), 2)
            self.assertEqual(project.segment_revisions[0].review_state.value, "superseded")
            self.assertEqual(project.segment_revisions[1].review_state.value, "ready_for_review")
            self.assertEqual(len(project.frame_edit_records), 2)
            self.assertEqual(
                {record.frame_index for record in project.frame_edit_records},
                {0, 2},
            )
            self.assertIn("mandatory review", controller.status.lower())

    def test_face_detection_draft_requires_explicit_candidate_or_manual_confirmation(self) -> None:
        controller = DesktopController()
        controller.addCharacter(
            "Avery",
            "averyface, stable identity",
            "Travel clothes",
            "blue jacket",
        )
        controller.planMockTimeline()
        controller.generateNextMockSegment()
        identity = controller.session.project.characters[0]
        revision = controller.session.project.segment_revisions[0]
        segment = controller.session.project.segments[0]
        selection = BatchFrameSelection(frame_indices=(3, 5))
        controller._active_batch_frame_edit = {  # noqa: SLF001
            "mode": "face_detection",
            "selection": selection,
            "identity_id": identity.identity_id,
            "identity_prompt": identity.identity_prompt,
            "revision_id": revision.revision_id,
            "segment_id": segment.segment_id,
            "pending_indices": [],
            "candidates": {},
        }
        controller._pending_krea_jobs["detect-3"] = {  # noqa: SLF001
            "operation": "batch_face_detection",
            "frame_index": 3,
        }

        controller._complete_krea_job(  # noqa: SLF001
            "detect-3",
            {
                "faces": [
                    {"box": {"x0": 4, "y0": 5, "x1": 20, "y1": 30}, "score": 0.7},
                    {"box": {"x0": 30, "y0": 6, "x1": 55, "y1": 35}, "score": 0.95},
                ]
            },
        )

        self.assertEqual(len(controller.faceProposalSummaries), 2)
        self.assertEqual(len(controller.identityWarningSummaries), 2)
        self.assertEqual(len(controller.checkpointProposalSummaries), 1)
        self.assertFalse(controller.faceBatchReady)
        controller.confirmDetectedBatchFace(1)
        self.assertFalse(controller.faceBatchReady)
        controller.confirmManualBatchFace(5, 8, 9, 40, 50)
        self.assertTrue(controller.faceBatchReady)
        confirmed = controller._face_batch_draft["confirmed"]  # noqa: SLF001
        self.assertEqual(confirmed[3].box.x0, 30)
        self.assertTrue(confirmed[5].manually_corrected)
        self.assertTrue(
            all(
                item.association_confirmed
                for item in controller.session.project.identity_warnings
            )
        )

    def test_approved_identity_checkpoint_requires_explicit_apply_before_staling(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            controller = DesktopController(asset_base=root / "projects")
            controller.addCharacter(
                "Avery",
                "averyface, stable identity",
                "Travel clothes",
                "blue jacket",
            )
            controller.planMockTimeline()
            controller.generateNextMockSegment()
            revision = controller.session.project.segment_revisions[0]
            segment = controller.session.project.segments[0]
            warning = IdentityDriftWarning(
                warning_id="warning-checkpoint",
                segment_revision_id=revision.revision_id,
                frame_index=2,
                identity_id=controller.session.project.characters[0].identity_id,
                kind=IdentityWarningKind.DRIFTING,
                score=0.4,
                message="Identity similarity dropped",
            )
            proposal = propose_checkpoint_from_warnings(
                proposal_id="checkpoint-proposal",
                segment_id=segment.segment_id,
                segment_start_ms=segment.start_ms,
                segment_end_ms=segment.end_ms,
                generation_fps=revision.source_request.generation_fps,
                warnings=(warning,),
            )
            controller.session.project = register_identity_analysis(
                controller.session.project,
                warnings=(warning,),
                proposal=proposal,
            )

            controller.approveIdentityCheckpoint(0)

            self.assertTrue(controller.session.project.checkpoint_proposals[0].user_approved)
            self.assertNotEqual(segment.state, SegmentState.STALE)
            checkpoint = root / "checkpoint.png"
            Image.new("RGB", (64, 64), "purple").save(checkpoint)
            controller._pending_checkpoint_application = {  # noqa: SLF001
                "proposal_id": proposal.proposal_id,
                "source_video_asset_id": revision.result_asset_id,
                "frame_index": 2,
            }
            controller._complete_checkpoint_extraction(str(checkpoint))  # noqa: SLF001

            project = controller.session.project
            self.assertEqual(project.segments[0].state, SegmentState.STALE)
            self.assertEqual(project.keyframes[0].source_type.value, "extracted_video")
            self.assertTrue(project.keyframes[0].approved)

    def test_completed_face_batch_records_identity_regions_and_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference_source = root / "reference.png"
            Image.new("RGB", (128, 128), "purple").save(reference_source)
            controller = DesktopController(asset_base=root / "projects")
            controller.addCharacter(
                "Avery",
                "averyface, stable identity",
                "Travel clothes",
                "blue jacket",
            )
            controller.importSheetEntry(
                QUrl.fromLocalFile(str(reference_source)),
                "front neutral",
            )
            controller.planMockTimeline()
            controller.generateNextMockSegment()
            project = controller.session.project
            source_revision = project.segment_revisions[0]
            identity = project.characters[0]
            reference_asset_id = project.character_sheets[0].entries[0].image_asset_id
            size = (
                source_revision.source_request.width,
                source_revision.source_request.height,
            )
            original = root / "original.png"
            replacement = root / "replacement.png"
            revised = root / "revised.mp4"
            Image.new("RGB", size, "blue").save(original)
            Image.new("RGB", size, "red").save(replacement)
            revised.write_bytes(b"immutable face-refined video")
            proposal = confirm_face_proposal(
                FaceProposal(
                    proposal_id="face-0",
                    frame_index=0,
                    identity_id=identity.identity_id,
                    region_id="face-region-0",
                    box=Rectangle(x0=10, y0=12, x1=80, y1=90),
                    score=0.91,
                    prompt=identity.identity_prompt,
                )
            )
            controller._face_batch_draft = {"confirmed": {0: proposal}}  # noqa: SLF001
            controller._active_batch_frame_edit = {  # noqa: SLF001
                "mode": "face_refinement",
                "segment_id": source_revision.segment_id,
                "revision_id": source_revision.revision_id,
                "source_video_asset_id": source_revision.result_asset_id,
                "selection": BatchFrameSelection(frame_indices=(0,)),
                "prompt": identity.identity_prompt,
                "identity_id": identity.identity_id,
                "reference_asset_id": reference_asset_id,
                "adapter_asset_ids": (),
                "adapters": (),
                "regions": {0: proposal},
            }

            controller._complete_batch_frame_modification(  # noqa: SLF001
                (str(original),),
                (str(replacement),),
                str(revised),
            )

            edit = controller.session.project.frame_edit_records[0]
            self.assertEqual(edit.operation_type, FrameEditOperation.FACE_REFINEMENT)
            self.assertEqual(edit.identity_id, identity.identity_id)
            self.assertTrue(edit.user_confirmed_face_region)
            self.assertEqual(edit.region, proposal.box)
            self.assertIsNone(controller._face_batch_draft)  # noqa: SLF001

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
        controller.setSegmentAction(
            0,
            "walk toward the window",
            "pose-start",
            "pose-end",
            "left to right",
            "slow orbit",
            "hand remains on railing",
            "ease in",
            0.7,
        )
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
        self.assertEqual(
            revision.source_request.action_spec.motion_instruction,
            "walk toward the window",
        )
        self.assertEqual(revision.source_request.action_spec_id, segment.action_spec_id)
        self.assertEqual(len(controller.session.project.actions), 1)
        self.assertTrue(any("prompt" in item for item in controller.timelineBlocks))
        self.assertEqual(controller.selectedSegmentMode, "prompt")
        self.assertEqual(controller.selectedSegmentPrompt, "camera orbit")
        self.assertEqual(controller.selectedSegmentNegativePrompt, "flicker")
        self.assertEqual(
            controller.selectedSegmentAction["motion_instruction"],
            "walk toward the window",
        )
        self.assertEqual(
            controller.selectedSegmentAction["starting_pose_ref"],
            "pose-start",
        )
        self.assertEqual(
            controller.selectedSegmentAction["pose_accuracy_preference"],
            0.7,
        )
        self.assertEqual(controller.backendParameterDescriptors[0]["value"], 28)

    def test_mode_specific_assets_and_continuation_flow_into_animate_request(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference = root / "character.png"
            driving = root / "driving.mp4"
            Image.new("RGB", (128, 128), "purple").save(reference)
            driving.write_bytes(b"immutable driving video")
            controller = DesktopController(asset_base=root / "projects")
            controller.planMockTimeline()
            controller.updateSegmentInspector(0, "animate", "turn and wave", "flicker")
            controller.setSegmentContinuationPolicy(0, "corrected_continuation")
            controller.importSegmentAsset(
                0,
                "reference_character",
                QUrl.fromLocalFile(str(reference)),
            )
            controller.importSegmentAsset(
                0,
                "driving_video",
                QUrl.fromLocalFile(str(driving)),
            )

            controller.generateNextMockSegment()

            segment = controller.session.project.segments[0]
            request = controller.session.project.segment_revisions[0].source_request
            self.assertEqual(
                segment.continuation_policy.value,
                "corrected_continuation",
            )
            self.assertEqual(
                request.reference_character_asset_id,
                segment.reference_character_asset_id,
            )
            self.assertEqual(request.driving_video_asset_id, segment.driving_video_asset_id)
            self.assertIn("character=", controller.segmentInputSummary)
            input_ids = {
                segment.reference_character_asset_id,
                segment.driving_video_asset_id,
            }
            self.assertTrue(
                all(
                    item.storage_path.startswith("objects/")
                    for item in controller.session.project.assets
                    if item.asset_id in input_ids
                )
            )

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
            controller.setMannequinArmPose(0.0, 0.0)
            controller.applySavedMannequinPose(0)
            controller.setMannequinCamera(0.5, 1.1, 7.0, 12.0, -4.0, 0.8)
            controller.setMannequinProportions(1.15, 0.9, 1.1)
            controller.setMannequinLight(2.0, 3.0, 4.0, 5.0)
            reference = root / "mannequin-reference.png"
            Image.new("RGB", (256, 256), "blue").save(reference)
            controller.addCharacter("Avery", "Avery identity", "Travel", "blue jacket")
            controller.importSheetEntryForSheet(
                0,
                QUrl.fromLocalFile(str(reference)),
                "front",
            )
            controller.addKeyframeRegion(0, 0, 0, 0, 640, 720, "standing")
            controller.associateMannequinRegion(0)
            controller.addMannequinProp("Chair", 0.0, 0.0, -0.5)
            controller.addMannequinContact("wrist_l", -0.8, 1.2, 0.0)
            controller.renderCurrentMannequinGuides()
            imported_depth = root / "blender-depth.png"
            Image.new("L", (1280, 720), 127).save(imported_depth)
            controller.importMannequinGuide(
                QUrl.fromLocalFile(str(imported_depth)),
                "depth",
            )

            project = controller.session.project
            self.assertEqual(controller.mannequinNames, ["Wave setup"])
            self.assertEqual(controller.mannequinPoseNames, ["Wave"])
            self.assertEqual(len(project.mannequin_scenes[0].guide_asset_ids), 4)
            self.assertEqual(len(controller.mannequinGuideLabels), 4)
            self.assertIn("i2i_scaffold", controller.mannequinConditioningPath)
            self.assertTrue(controller.mannequinPreviewUrl.isLocalFile())
            scene = project.mannequin_scenes[0]
            self.assertEqual(scene.camera.position.x, 0.5)
            self.assertIsNotNone(scene.camera.crop)
            self.assertEqual(scene.instances[0].body_proportions["height_scale"], 1.15)
            self.assertEqual(scene.lights[0].intensity, 2.0)
            self.assertEqual(scene.props[0].name, "Chair")
            self.assertEqual(scene.contact_constraints[0].joint_name, "wrist_l")
            self.assertEqual(
                scene.instances[0].character_region_id,
                controller._draft_keyframe_regions[0].region_id,  # noqa: SLF001
            )
            shoulder = next(
                item for item in scene.instances[0].joints if item.joint_name == "shoulder_l"
            )
            self.assertNotEqual(shoulder.rotation.w, 1.0)
            guide_map = controller._mannequin_guide_assets(scene.scene_id)  # noqa: SLF001
            self.assertEqual(
                guide_map[GuideKind.DEPTH],
                scene.guide_asset_ids[-1],
            )
            controller._krea_load_command_id = "load-depth-capability"  # noqa: SLF001
            controller._handle_krea_event(  # noqa: SLF001
                {
                    "command_id": "load-depth-capability",
                    "state": "ready",
                    "message": "ready",
                    "payload": {
                        "capabilities": {
                            "metadata": {
                                "depth_control_model_ids": ["krea-depth-v1"],
                            }
                        }
                    },
                }
            )
            self.assertIn("depth_control", controller.mannequinConditioningPath)

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
