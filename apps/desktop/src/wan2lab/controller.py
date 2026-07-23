"""Thin Qt adapter over the authoritative wan2core session."""

from __future__ import annotations

from datetime import UTC, datetime
import json
import math
import mimetypes
from pathlib import Path
import shutil
import tempfile
from uuid import uuid4

from PIL import Image, ImageOps
from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot

from k2core import __version__ as k2core_version
from wan2core.actions import ActionSpec
from wan2core import __version__ as wan2core_version
from wan2core.assets import AssetKind, AssetRef
from wan2core.backends import BackendCapabilities, FrameRounding, WanMode
from wan2core.backends.mock import MockWanBackend, default_mock_capabilities
from wan2core.characters import (
    AdapterFamily,
    AdapterKind,
    AdapterRef,
    AppearanceProfile,
    ApprovalState,
    CharacterIdentity,
    CharacterSheet,
    PoseViewEntry,
    PoseViewSource,
    StyleDuplicationEntry,
)
from wan2core.export import ExportPlan, ExportState, build_export_plan
from wan2core.editing import (
    BatchFrameSelection,
    BoundaryPropagation,
    FrameEditOperation,
    FrameEditRecord,
)
from wan2core.editing.workflows import (
    NormalizedFrameEditRequest,
    commit_frame_edit_revision,
    plan_frame_extraction,
    plan_frame_revision_assembly,
)
from wan2core.editing.faces import FaceProposal, confirm_face_proposal
from wan2core.keyframes import (
    AdapterSelection,
    CharacterRegionAssignment,
    Keyframe,
    KeyframeSource,
    Rectangle,
)
from wan2core.keyframes.composition import (
    KeyframeCompositionRequest,
    compile_keyframe_composition,
)
from wan2core.keyframes.generation import (
    CharacterSheetImageRequest,
    ComposedKeyframeRequest,
    RestyleEntryRequest,
)
from wan2core.keyframes.workflows import (
    add_timeline_keyframe,
    register_pose_view_entry,
    register_style_duplication,
    remove_pose_view_entry,
    replace_pose_view_entry,
    retime_timeline_keyframe,
    revise_timeline_keyframe,
    update_pose_view_entry,
    update_pose_view_metadata,
)
from wan2core.identity import IdentityDriftWarning, IdentityWarningKind
from wan2core.identity.workflows import (
    apply_approved_checkpoint,
    approve_registered_checkpoint,
    confirm_warning_association,
    propose_checkpoint_from_warnings,
    register_identity_analysis,
)
from wan2core.mannequin import (
    ContactConstraint,
    JointPose,
    MannequinInstance,
    Quaternion,
    SceneLight,
    SceneProp,
    Transform,
    Vector3,
)
from wan2core.mannequin.workflows import (
    GuideKind,
    KreaMannequinCapabilities,
    apply_pose,
    attach_rendered_guides,
    default_mannequin_scene,
    import_blender_scene_document,
    plan_krea_conditioning,
    register_blender_scene,
    register_mannequin_pose,
    save_mannequin_scene,
    save_pose_from_instance,
)
from wan2core.orchestration import ReviewGateBlocked, WanStudioSession
from wan2core.projects import (
    ProjectSettings,
    Wan2LabProject,
    load_project,
    save_project,
)
from wan2core.projects.invalidation import change_output_fps, invalidate_segments
from wan2core.segments import ContinuationPolicy, SegmentState
from wan2core.timeline import Timeline
from wan2core.provenance import ProvenanceRecord
from wan2core.workers import (
    AckEvent,
    CancelRequest,
    CapabilitiesEvent,
    ErrorEvent,
    GenerateSegmentRequest,
    InspectCapabilitiesRequest,
    LoadModelRequest,
    ModelsEvent,
    ProgressEvent,
    ReleaseAllModelsRequest,
    ReleaseWanModelRequest,
    ResultEvent,
    RuntimeStatusEvent,
    RuntimeStatusRequest,
)
from wan2lab.backends.comfyui import BACKEND_ID
from wan2lab.assets import LocalAssetStore, LocalComfyAssetBridge, image_media_type
from wan2lab.export_runner import ExportProcessRunner
from wan2lab.frame_runner import (
    BatchFrameModificationProcessRunner,
    FrameExtractionProcessRunner,
    FrameModificationProcessRunner,
)
from wan2lab.krea_worker_client import KreaWorkerProcess
from wan2lab.mannequin import render_mannequin_guides
from wan2lab.worker_client import WanWorkerProcess


class DesktopController(QObject):
    projectChanged = Signal()
    statusChanged = Signal()
    eventLogChanged = Signal()

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        asset_base: Path | None = None,
        comfy_input_root: Path | None = None,
        comfy_output_root: Path | None = None,
        comfyui_root: Path | None = None,
        krea_worker_python: Path | None = None,
        krea_result_root: Path | None = None,
    ) -> None:
        super().__init__(parent)
        self._asset_base = (
            asset_base.expanduser().resolve()
            if asset_base is not None
            else Path("~/.local/share/wan2lab/projects").expanduser().resolve()
        )
        self._capabilities = default_mock_capabilities()
        self._backend = MockWanBackend(self._capabilities)
        self._session = self._new_session(18_000)
        self._asset_store = self._store_for_project(self._session.project.project_id)
        self._comfyui_root = (comfyui_root or Path("~/ComfyUI")).expanduser().resolve()
        self._comfy_assets = LocalComfyAssetBridge(
            comfy_input_root or self._comfyui_root / "input",
            comfy_output_root or self._comfyui_root / "output",
        )
        self._krea_result_root = (
            krea_result_root or Path("~/.cache/wan2lab/krea-results")
        ).expanduser().resolve()
        self._project_name = "Untitled Wan2Lab Project"
        self._review_segment_index = 0
        self._review_revision_id: str | None = None
        self._status = "Ready — plan the timeline to begin"
        self._events: list[str] = []
        self._mannequin_preview_url = QUrl()
        self._mannequin_preview_revision = 0
        self._mannequin_instance_index = 0
        self._preview_keyframe_index = 0
        self._wan_worker = WanWorkerProcess(self)
        self._wan_worker.eventReceived.connect(self._handle_worker_event)
        self._wan_worker.transportError.connect(self._handle_worker_transport_error)
        self._krea_worker = KreaWorkerProcess(
            self,
            comfyui_root=self._comfyui_root,
            worker_python=krea_worker_python,
        )
        self._krea_worker.eventReceived.connect(self._handle_krea_event)
        self._krea_worker.transportError.connect(self._handle_krea_transport_error)
        self._backend_status = "Local ComfyUI backend not inspected"
        self._backend_models: list[str] = []
        self._backend_model_descriptors: list[dict[str, object]] = []
        self._wan_model_control_index = 0
        self._backend_parameters: list[str] = []
        self._backend_parameter_descriptors: list[dict[str, object]] = []
        self._backend_vae_models: list[str] = []
        self._backend_text_encoder_models: list[str] = []
        self._inspected_capabilities: BackendCapabilities | None = None
        self._selected_wan_model_id: str | None = None
        self._pending_model_command_id: str | None = None
        self._pending_wan_model_id: str | None = None
        self._pending_release_command_id: str | None = None
        self._active_wan_commands: dict[str, str] = {}
        self._active_wan_jobs: dict[str, str] = {}
        self._krea_status = "Local Krea worker not inspected"
        self._krea_loaded = False
        self._krea_depth_control_model_ids: tuple[str, ...] = ()
        self._krea_load_command_id: str | None = None
        self._pending_krea_jobs: dict[str, dict[str, object]] = {}
        self._style_duplications: dict[str, dict[str, object]] = {}
        self._draft_keyframe_regions: list[CharacterRegionAssignment] = []
        self._export_runner = ExportProcessRunner(self)
        self._export_runner.progress.connect(self._handle_export_progress)
        self._export_runner.completed.connect(self._complete_export)
        self._export_runner.failed.connect(self._fail_export)
        self._active_export_plan: ExportPlan | None = None
        self._frame_runner = FrameModificationProcessRunner(self)
        self._frame_runner.progress.connect(self._handle_frame_progress)
        self._frame_runner.completed.connect(self._complete_frame_modification)
        self._frame_runner.failed.connect(self._fail_frame_modification)
        self._active_frame_edit: dict[str, object] | None = None
        self._frame_extraction_runner = FrameExtractionProcessRunner(self)
        self._frame_extraction_runner.completed.connect(self._handle_krea_source_extracted)
        self._frame_extraction_runner.failed.connect(
            self._handle_krea_source_extraction_failed
        )
        self._pending_krea_frame_edit: dict[str, object] | None = None
        self._batch_frame_runner = BatchFrameModificationProcessRunner(self)
        self._batch_frame_runner.progress.connect(self._handle_frame_progress)
        self._batch_frame_runner.completed.connect(self._complete_batch_frame_modification)
        self._batch_frame_runner.failed.connect(self._fail_batch_frame_modification)
        self._active_batch_frame_edit: dict[str, object] | None = None
        self._face_batch_draft: dict[str, object] | None = None
        self._pending_checkpoint_application: dict[str, object] | None = None

    @Property(str, notify=projectChanged)
    def projectName(self) -> str:  # noqa: N802 - Qt property naming
        return self._project_name

    @Property(float, notify=projectChanged)
    def durationSeconds(self) -> float:  # noqa: N802
        return self._session.project.timeline.duration_ms / 1000.0

    @Property(int, notify=projectChanged)
    def projectWidth(self) -> int:  # noqa: N802
        return self._session.project.project_settings.width

    @Property(int, notify=projectChanged)
    def projectHeight(self) -> int:  # noqa: N802
        return self._session.project.project_settings.height

    @Property(int, notify=projectChanged)
    def defaultSegmentBudgetMs(self) -> int:  # noqa: N802
        return self._session.project.project_settings.default_segment_duration_ms

    @Property(str, notify=projectChanged)
    def memoryPolicy(self) -> str:  # noqa: N802
        return self._session.project.project_settings.memory_policy

    @Property(str, notify=projectChanged)
    def defaultContinuationPolicy(self) -> str:  # noqa: N802
        return self._session.project.project_settings.default_continuation_policy.value

    @Property(str, notify=projectChanged)
    def defaultKreaBackendId(self) -> str:  # noqa: N802
        return self._session.project.project_settings.default_krea_backend_id

    @Property(str, notify=projectChanged)
    def defaultKreaModelId(self) -> str:  # noqa: N802
        return self._session.project.project_settings.default_krea_model_id

    @Property(str, notify=projectChanged)
    def ffmpegExecutable(self) -> str:  # noqa: N802
        return self._session.project.project_settings.ffmpeg_executable

    @Property(int, notify=projectChanged)
    def segmentCount(self) -> int:  # noqa: N802
        return len(self._session.project.segments)

    @Property(int, notify=projectChanged)
    def approvedSegmentCount(self) -> int:  # noqa: N802
        return sum(
            segment.state is SegmentState.APPROVED_LOCKED
            for segment in self._session.project.segments
        )

    @Property(float, notify=projectChanged)
    def outputFps(self) -> float:  # noqa: N802
        return self._session.project.timeline.output_fps

    @Property(bool, notify=statusChanged)
    def exportRunning(self) -> bool:  # noqa: N802
        return self._export_runner.running

    @Property(bool, notify=statusChanged)
    def generationRunning(self) -> bool:  # noqa: N802
        return bool(self._active_wan_commands)

    @Property(bool, notify=statusChanged)
    def frameModificationRunning(self) -> bool:  # noqa: N802
        return (
            self._frame_runner.running
            or self._frame_extraction_runner.running
            or self._pending_krea_frame_edit is not None
            or self._batch_frame_runner.running
            or self._active_batch_frame_edit is not None
        )

    @Property(str, notify=statusChanged)
    def generationBackendLabel(self) -> str:  # noqa: N802
        return "Local ComfyUI Wan worker" if self._selected_wan_model_id else "Mock backend"

    @Property(str, notify=statusChanged)
    def status(self) -> str:
        return self._status

    @Property(str, constant=True)
    def runtimeVersions(self) -> str:  # noqa: N802
        return f"wan2core {wan2core_version} · k2core {k2core_version} · mock-wan 1.0"

    @Property("QStringList", notify=eventLogChanged)
    def eventLog(self) -> list[str]:  # noqa: N802
        return list(self._events)

    @Property("QStringList", notify=projectChanged)
    def characterNames(self) -> list[str]:  # noqa: N802
        return [item.name for item in self._session.project.characters]

    @Property("QStringList", notify=projectChanged)
    def characterAdapterLabels(self) -> list[str]:  # noqa: N802
        identities = {
            item.identity_id: item.name for item in self._session.project.characters
        }
        labels = [
            f"{adapter.adapter_id} · {identity.name} · identity · {adapter.kind.value} · "
            f"{adapter.model_family} · {adapter.default_strength:g}"
            for identity in self._session.project.characters
            for adapter in identity.adapter_refs
        ]
        labels.extend(
            f"{adapter.adapter_id} · {identities[appearance.identity_id]} / "
            f"{appearance.name} · appearance · "
            f"{adapter.kind.value} · {adapter.model_family} · {adapter.default_strength:g}"
            for appearance in self._session.project.appearance_profiles
            for adapter in appearance.adapter_refs
        )
        return labels

    @Property("QStringList", notify=projectChanged)
    def faceProposalSummaries(self) -> list[str]:  # noqa: N802
        draft = self._face_batch_draft
        if draft is None:
            return []
        confirmed = draft["confirmed"]
        return [
            (
                f"Frame {proposal.frame_index} · candidate {candidate_index + 1} · "
                f"score {proposal.score:.2f} · "
                f"{proposal.box.x0:.0f},{proposal.box.y0:.0f}–"
                f"{proposal.box.x1:.0f},{proposal.box.y1:.0f}"
                f"{' · confirmed' if proposal.frame_index in confirmed else ''}"
            )
            for proposal, candidate_index in draft["candidate_order"]
        ]

    @Property("QStringList", notify=projectChanged)
    def confirmedFaceFrames(self) -> list[str]:  # noqa: N802
        draft = self._face_batch_draft
        if draft is None:
            return []
        confirmed = draft["confirmed"]
        selection = draft["selection"]
        return [
            (
                f"Frame {index}: confirmed"
                if index in confirmed
                else f"Frame {index}: confirmation required"
            )
            for index in selection.frame_indices
        ]

    @Property(bool, notify=projectChanged)
    def faceBatchReady(self) -> bool:  # noqa: N802
        draft = self._face_batch_draft
        if draft is None:
            return False
        return set(draft["selection"].frame_indices) == set(draft["confirmed"])

    @Property("QStringList", notify=projectChanged)
    def identityWarningSummaries(self) -> list[str]:  # noqa: N802
        return [
            (
                f"Frame {item.frame_index} · {item.kind.value} · {item.message}"
                f"{' · association confirmed' if item.association_confirmed else ''}"
            )
            for item in self._session.project.identity_warnings
        ]

    @Property("QStringList", notify=projectChanged)
    def checkpointProposalSummaries(self) -> list[str]:  # noqa: N802
        return [
            (
                f"{item.time_ms / 1000:g}s · {item.reason} · "
                f"{'approved' if item.user_approved else 'approval required'}"
            )
            for item in self._session.project.checkpoint_proposals
        ]

    @Property("QStringList", notify=projectChanged)
    def sheetEntryNames(self) -> list[str]:  # noqa: N802
        return [
            f"{sheet.name} · {entry.name}"
            for sheet in self._session.project.character_sheets
            for entry in sheet.entries
        ]

    @Property("QVariantList", notify=projectChanged)
    def sheetEntryCards(self) -> list[dict[str, object]]:  # noqa: N802
        assets = {item.asset_id: item for item in self._session.project.assets}
        mannequin_indexes = {
            item.scene_id: index
            for index, item in enumerate(self._session.project.mannequin_scenes)
        }
        cards = []
        for sheet_index, sheet in enumerate(self._session.project.character_sheets):
            for entry_index, entry in enumerate(sheet.entries):
                try:
                    url = QUrl.fromLocalFile(
                        str(self._asset_store.resolve_ref(assets[entry.image_asset_id]))
                    ).toString()
                except (KeyError, FileNotFoundError, ValueError):
                    url = ""
                metadata = " · ".join(
                    item
                    for item in (
                        entry.view_label,
                        entry.pose_label,
                        entry.framing_label,
                        entry.expression_label,
                    )
                    if item
                )
                cards.append(
                    {
                        "sheet_index": sheet_index,
                        "entry_index": entry_index,
                        "name": entry.name,
                        "image_url": url,
                        "metadata": metadata or "metadata not set",
                        "approval_state": entry.approval_state.value,
                        "view_label": entry.view_label,
                        "pose_label": entry.pose_label,
                        "framing_label": entry.framing_label,
                        "expression_label": entry.expression_label,
                        "mannequin_scene_index": mannequin_indexes.get(
                            entry.mannequin_scene_id,
                            -1,
                        ),
                    }
                )
        return cards

    @Property("QStringList", notify=projectChanged)
    def keyframeLabels(self) -> list[str]:  # noqa: N802
        return [
            f"{keyframe.time_ms / 1000:g}s · {keyframe.source_type.value}"
            for keyframe in self._session.project.keyframes
        ]

    @Property("QStringList", notify=projectChanged)
    def keyframeSourceLabels(self) -> list[str]:  # noqa: N802
        return [label for label, _asset_id in self._keyframe_i2i_sources()]

    @Property("QStringList", notify=projectChanged)
    def keyframeRegionLabels(self) -> list[str]:  # noqa: N802
        return [
            f"{item.name}: {item.rectangle.x0:g},{item.rectangle.y0:g}–"
            f"{item.rectangle.x1:g},{item.rectangle.y1:g}"
            for item in self._draft_keyframe_regions
        ]

    @Property("QVariantList", notify=projectChanged)
    def keyframeRegionRectangles(self) -> list[dict[str, object]]:  # noqa: N802
        return [
            {
                "name": item.name,
                "x0": item.rectangle.x0,
                "y0": item.rectangle.y0,
                "x1": item.rectangle.x1,
                "y1": item.rectangle.y1,
            }
            for item in self._draft_keyframe_regions
        ]

    @Property("QStringList", notify=projectChanged)
    def mannequinNames(self) -> list[str]:  # noqa: N802
        return [item.name for item in self._session.project.mannequin_scenes]

    @Property("QStringList", notify=projectChanged)
    def mannequinPoseNames(self) -> list[str]:  # noqa: N802
        return [item.name for item in self._session.project.mannequin_poses]

    @Property("QStringList", notify=projectChanged)
    def mannequinInstanceNames(self) -> list[str]:  # noqa: N802
        if not self._session.project.mannequin_scenes:
            return []
        return [
            item.name for item in self._session.project.mannequin_scenes[-1].instances
        ]

    @Property("QStringList", notify=projectChanged)
    def mannequinJointNames(self) -> list[str]:  # noqa: N802
        if not self._session.project.mannequin_scenes:
            return []
        scene = self._session.project.mannequin_scenes[-1]
        _index, instance = self._selected_mannequin_instance(scene)
        return [item.joint_name for item in instance.joints]

    @Property("QStringList", notify=projectChanged)
    def mannequinGuideLabels(self) -> list[str]:  # noqa: N802
        asset_ids = {
            asset.asset_id: asset.storage_path for asset in self._session.project.assets
        }
        return [
            asset_ids[asset_id]
            for scene in self._session.project.mannequin_scenes
            for asset_id in scene.guide_asset_ids
            if asset_id in asset_ids
        ]

    @Property(QUrl, notify=projectChanged)
    def mannequinPreviewUrl(self) -> QUrl:  # noqa: N802
        return self._mannequin_preview_url

    @Property(QUrl, notify=projectChanged)
    def keyframePreviewUrl(self) -> QUrl:  # noqa: N802
        if not self._session.project.keyframes:
            return QUrl()
        keyframe = self._session.project.keyframes[
            min(self._preview_keyframe_index, len(self._session.project.keyframes) - 1)
        ]
        try:
            asset = next(
                item
                for item in self._session.project.assets
                if item.asset_id == keyframe.image_asset_id
            )
            path = self._asset_store.resolve_ref(asset)
        except (KeyError, FileNotFoundError, StopIteration, ValueError):
            return QUrl()
        return QUrl.fromLocalFile(str(path))

    @Property(str, notify=projectChanged)
    def keyframePreviewMetadata(self) -> str:  # noqa: N802
        if not self._session.project.keyframes:
            return "No keyframe selected"
        keyframe = self._session.project.keyframes[
            min(self._preview_keyframe_index, len(self._session.project.keyframes) - 1)
        ]
        state = "approved / locked" if keyframe.approved and keyframe.locked else "draft review"
        asset = next(
            (
                item
                for item in self._session.project.assets
                if item.asset_id == keyframe.image_asset_id
            ),
            None,
        )
        dimensions = (
            f"{asset.width}x{asset.height}"
            if asset is not None and asset.width is not None and asset.height is not None
            else "dimensions unavailable"
        )
        return (
            f"{keyframe.time_ms / 1000:g}s · {keyframe.source_type.value} · {state} · "
            f"{dimensions} · {len(keyframe.region_assignments)} region(s)"
        )

    @Property(QUrl, notify=projectChanged)
    def reviewVideoUrl(self) -> QUrl:  # noqa: N802
        revision = self._selected_review_revision()
        if revision is None or revision.result_asset_id is None:
            return QUrl()
        try:
            asset = next(
                item
                for item in self._session.project.assets
                if item.asset_id == revision.result_asset_id
            )
            path = self._asset_store.resolve_ref(asset)
        except (KeyError, FileNotFoundError, StopIteration, ValueError):
            return QUrl()
        return QUrl.fromLocalFile(str(path))

    @Property(int, notify=projectChanged)
    def reviewFrameCount(self) -> int:  # noqa: N802
        revision = self._selected_review_revision()
        return revision.source_request.frame_count if revision is not None else 0

    @Property(float, notify=projectChanged)
    def reviewGenerationFps(self) -> float:  # noqa: N802
        revision = self._selected_review_revision()
        return revision.source_request.generation_fps if revision is not None else 1.0

    @Property("QStringList", notify=projectChanged)
    def reviewFrameLabels(self) -> list[str]:  # noqa: N802
        return [str(index) for index in range(self.reviewFrameCount)]

    @Property("QStringList", notify=projectChanged)
    def reviewRevisionLabels(self) -> list[str]:  # noqa: N802
        if not self._session.project.segments:
            return []
        segment = self._session.project.segments[
            min(self._review_segment_index, len(self._session.project.segments) - 1)
        ]
        revisions = {
            item.revision_id: item for item in self._session.project.segment_revisions
        }
        return [
            f"Revision {revisions[revision_id].revision_number} · "
            f"{revisions[revision_id].review_state.value}"
            for revision_id in segment.revision_ids
            if revision_id in revisions
        ]

    @Property(int, notify=projectChanged)
    def reviewRevisionIndex(self) -> int:  # noqa: N802
        if not self._session.project.segments:
            return -1
        segment = self._session.project.segments[
            min(self._review_segment_index, len(self._session.project.segments) - 1)
        ]
        if not segment.revision_ids:
            return -1
        if self._review_revision_id in segment.revision_ids:
            return segment.revision_ids.index(self._review_revision_id)
        return len(segment.revision_ids) - 1

    @Property(str, notify=projectChanged)
    def reviewMetadata(self) -> str:  # noqa: N802
        revision = self._selected_review_revision()
        if revision is None:
            return "No generated revision selected"
        request = revision.source_request
        inputs = ", ".join(
            label
            for label, asset_id in (
                ("start", request.start_image_asset_id),
                ("end", request.end_image_asset_id),
                ("character", request.reference_character_asset_id),
                ("driving", request.driving_video_asset_id),
                ("source", request.source_video_asset_id),
                ("mask", request.mask_asset_id),
            )
            if asset_id is not None
        ) or "prompt-only"
        parameters = ", ".join(
            f"{key}={value}" for key, value in sorted(revision.resolved_parameters.items())
        ) or "defaults"
        action = request.action_spec
        action_summary = (
            "; ".join(
                item
                for item in (
                    action.motion_instruction,
                    action.character_trajectory,
                    action.camera_trajectory,
                    action.speed_easing,
                )
                if item
            )
            if action is not None
            else "none"
        ) or "none"
        runtime = ", ".join(
            f"{key}={value}"
            for key, value in revision.generation_metadata.items()
            if key
            in {
                "template_id",
                "template_version",
                "precision",
                "quantization",
                "load_device",
            }
        ) or "not reported"
        return (
            f"Revision {revision.revision_number} · {revision.review_state.value} · "
            f"{request.mode.value} · {request.backend_id}/{request.model_id} · "
            f"seed {revision.seed} · {request.frame_count} frames at "
            f"{request.generation_fps:g} fps · "
            f"{(request.end_ms - request.start_ms) / 1000:g}s · inputs: {inputs}\n"
            f"Prompt: {request.prompt or '—'}\n"
            f"Action: {action_summary}\n"
            f"Parameters: {parameters}\n"
            f"Runtime: {runtime} · provenance {revision.provenance_id or 'pending'}"
        )

    @Property(str, notify=projectChanged)
    def segmentInputSummary(self) -> str:  # noqa: N802
        if not self._session.project.segments:
            return "No segment selected"
        segment = self._session.project.segments[
            min(self._review_segment_index, len(self._session.project.segments) - 1)
        ]
        values = (
            ("start", segment.start_image_asset_id),
            ("end", segment.end_image_asset_id),
            ("character", segment.reference_character_asset_id),
            ("driving", segment.driving_video_asset_id),
            ("source", segment.source_video_asset_id),
            ("mask", segment.mask_asset_id),
        )
        assigned = ", ".join(
            f"{label}={asset_id}" for label, asset_id in values if asset_id is not None
        ) or "no explicit overrides"
        return f"{segment.continuation_policy.value} · {assigned}"

    def _selected_segment(self):
        if not self._session.project.segments:
            return None
        return self._session.project.segments[
            min(self._review_segment_index, len(self._session.project.segments) - 1)
        ]

    @Property(str, notify=projectChanged)
    def selectedSegmentMode(self) -> str:  # noqa: N802
        segment = self._selected_segment()
        return segment.mode.value if segment is not None else "prompt"

    @Property(str, notify=projectChanged)
    def selectedSegmentPrompt(self) -> str:  # noqa: N802
        segment = self._selected_segment()
        return segment.prompt if segment is not None else ""

    @Property(str, notify=projectChanged)
    def selectedSegmentNegativePrompt(self) -> str:  # noqa: N802
        segment = self._selected_segment()
        return segment.negative_prompt if segment is not None else ""

    @Property(str, notify=projectChanged)
    def selectedSegmentContinuationPolicy(self) -> str:  # noqa: N802
        segment = self._selected_segment()
        return (
            segment.continuation_policy.value
            if segment is not None
            else ContinuationPolicy.AUTHORED_ANCHOR.value
        )

    @Property(float, notify=projectChanged)
    def selectedSegmentGenerationFps(self) -> float:  # noqa: N802
        segment = self._selected_segment()
        if segment is None:
            return 0.0
        if segment.generation_fps is not None:
            return segment.generation_fps
        return self._capabilities.model(segment.model_id).default_generation_fps

    @Property(int, notify=projectChanged)
    def selectedSegmentFrameCount(self) -> int:  # noqa: N802
        segment = self._selected_segment()
        return segment.frame_count or 0 if segment is not None else 0

    @Property(str, notify=projectChanged)
    def selectedSegmentFrameRounding(self) -> str:  # noqa: N802
        segment = self._selected_segment()
        return segment.frame_rounding.value if segment is not None else FrameRounding.NEAREST.value

    @Property("QVariantMap", notify=projectChanged)
    def selectedSegmentAction(self) -> dict[str, object]:  # noqa: N802
        segment = self._selected_segment()
        action = next(
            (
                item
                for item in self._session.project.actions
                if segment is not None and item.action_id == segment.action_spec_id
            ),
            None,
        )
        if action is None:
            return {
                "motion_instruction": "",
                "starting_pose_ref": "",
                "ending_pose_ref": "",
                "character_trajectory": "",
                "camera_trajectory": "",
                "contact_constraints": "",
                "speed_easing": "",
                "pose_accuracy_preference": 0.5,
            }
        return {
            "motion_instruction": action.motion_instruction,
            "starting_pose_ref": action.starting_pose_ref or "",
            "ending_pose_ref": action.ending_pose_ref or "",
            "character_trajectory": action.character_trajectory,
            "camera_trajectory": action.camera_trajectory,
            "contact_constraints": ", ".join(action.contact_constraints),
            "speed_easing": action.speed_easing,
            "pose_accuracy_preference": action.pose_accuracy_preference,
        }

    @Property("QVariantList", notify=projectChanged)
    def selectedSegmentCharacterAssignments(self) -> list[dict[str, object]]:  # noqa: N802
        segment = self._selected_segment()
        selected = set(segment.character_identity_ids) if segment is not None else set()
        return [
            {
                "identity_index": index,
                "identity_id": identity.identity_id,
                "name": identity.name,
                "assigned": identity.identity_id in selected,
            }
            for index, identity in enumerate(self._session.project.characters)
        ]

    @Property(str, notify=projectChanged)
    def mannequinConditioningPath(self) -> str:  # noqa: N802
        if not self._session.project.mannequin_scenes:
            return "No mannequin scene"
        scene = self._session.project.mannequin_scenes[-1]
        by_kind = self._mannequin_guide_assets(scene.scene_id)
        try:
            plan = plan_krea_conditioning(
                scene=scene,
                capabilities=KreaMannequinCapabilities(
                    depth_control_model_ids=self._krea_depth_control_model_ids,
                    supports_i2i=True,
                ),
                guide_assets=by_kind,
            )
        except ValueError:
            return "Render guides to enable Krea conditioning"
        return f"{plan.path.value}: {plan.explanation}"

    @Property(str, notify=projectChanged)
    def mannequinSceneSummary(self) -> str:  # noqa: N802
        if not self._session.project.mannequin_scenes:
            return "No mannequin scene"
        scene = self._session.project.mannequin_scenes[-1]
        _instance_index, instance = self._selected_mannequin_instance(scene)
        region = instance.character_region_id or "unassigned"
        return (
            f"{scene.name} · {len(scene.instances)} mannequin(s) · "
            f"{len(scene.lights)} light(s) · region {region}"
        )

    @Property(str, notify=statusChanged)
    def backendStatus(self) -> str:  # noqa: N802
        return self._backend_status

    @Property(str, notify=statusChanged)
    def kreaStatus(self) -> str:  # noqa: N802
        return self._krea_status

    @Property(bool, notify=statusChanged)
    def kreaLoaded(self) -> bool:  # noqa: N802
        return self._krea_loaded

    @Property("QStringList", notify=statusChanged)
    def backendModels(self) -> list[str]:  # noqa: N802
        return list(self._backend_models)

    @Property("QStringList", notify=statusChanged)
    def backendVaeModels(self) -> list[str]:  # noqa: N802
        return list(self._backend_vae_models)

    @Property("QStringList", notify=statusChanged)
    def backendTextEncoderModels(self) -> list[str]:  # noqa: N802
        return list(self._backend_text_encoder_models)

    @Property("QStringList", notify=statusChanged)
    def backendParameters(self) -> list[str]:  # noqa: N802
        return list(self._backend_parameters)

    def _wan_model_control(self):
        capabilities = self._inspected_capabilities
        if capabilities is None or not capabilities.model_variants:
            return None
        index = min(self._wan_model_control_index, len(capabilities.model_variants) - 1)
        return capabilities.model_variants[index]

    @Property("QStringList", notify=projectChanged)
    def wanPrecisionOptions(self) -> list[str]:  # noqa: N802
        model = self._wan_model_control()
        return list(model.supported_precisions) if model is not None else []

    @Property("QStringList", notify=projectChanged)
    def wanQuantizationOptions(self) -> list[str]:  # noqa: N802
        model = self._wan_model_control()
        return list(model.supported_quantizations) if model is not None else []

    @Property("QStringList", notify=projectChanged)
    def wanOffloadOptions(self) -> list[str]:  # noqa: N802
        model = self._wan_model_control()
        return list(model.supported_offload_modes) if model is not None else []

    @Property(str, notify=projectChanged)
    def wanModelCompatibility(self) -> str:  # noqa: N802
        model = self._wan_model_control()
        if model is None or self._inspected_capabilities is None:
            return "Inspect the backend to see hardware-specific model controls."
        accelerator = ", ".join(sorted(self._inspected_capabilities.accelerator_vendors))
        resolutions = ", ".join(
            f"{item.width}x{item.height}" for item in model.supported_resolutions
        )
        return (
            f"{accelerator or 'unknown accelerator'} · "
            f"{'/'.join(sorted(item.value for item in model.supported_modes))} · "
            f"{resolutions}"
        )

    @Property("QVariantList", notify=projectChanged)
    def backendParameterDescriptors(self) -> list[dict[str, object]]:  # noqa: N802
        segment = self._selected_segment()
        return [
            {
                **item,
                "value": (
                    segment.parameters.get(str(item.get("key")), item.get("default"))
                    if segment is not None
                    else item.get("default")
                ),
            }
            for item in self._backend_parameter_descriptors
            if segment is None
            or segment.mode.value
            in {str(mode) for mode in item.get("applicable_modes", ())}
        ]

    @Property("QStringList", notify=projectChanged)
    def timelineBlocks(self) -> list[str]:  # noqa: N802
        blocks = [
            (item.time_ms, f"K {item.time_ms / 1000:g}s · {item.source_type.value}")
            for item in self._session.project.keyframes
        ]
        blocks.extend(
            (
                item.start_ms,
                f"S {item.start_ms / 1000:g}–{item.end_ms / 1000:g}s · "
                f"{item.mode.value} · {item.state.value}",
            )
            for item in self._session.project.segments
        )
        return [label for _time, label in sorted(blocks)]

    @Slot(float)
    def newProject(self, duration_seconds: float = 18.0) -> None:  # noqa: N802
        if self._active_wan_commands or self.frameModificationRunning:
            self._set_status("Cancel active generation or modification before creating a project")
            return
        duration_ms = max(1_000, round(duration_seconds * 1000))
        self._session = self._new_session(duration_ms)
        self._asset_store = self._store_for_project(self._session.project.project_id)
        self._project_name = "Untitled Wan2Lab Project"
        self._review_segment_index = 0
        self._review_revision_id = None
        self._events.clear()
        self._draft_keyframe_regions.clear()
        self._face_batch_draft = None
        self._pending_checkpoint_application = None
        self._mannequin_preview_url = QUrl()
        self._preview_keyframe_index = 0
        self._mannequin_instance_index = 0
        self._set_status("New project ready")
        self.projectChanged.emit()
        self.eventLogChanged.emit()

    @Slot()
    def inspectLocalWanBackend(self) -> None:  # noqa: N802
        self._backend_status = "Inspecting local ComfyUI worker…"
        self.statusChanged.emit()
        self._wan_worker.send(
            InspectCapabilitiesRequest(
                command_id=f"inspect-{uuid4().hex}",
                backend_id=BACKEND_ID,
            )
        )

    @Slot()
    def inspectWanRuntimeStatus(self) -> None:  # noqa: N802
        self._wan_worker.send(
            RuntimeStatusRequest(command_id=f"runtime-status-{uuid4().hex}")
        )
        self._set_status("Refreshing accelerator and model-residency diagnostics…")

    @Slot()
    def releaseAllModels(self) -> None:  # noqa: N802
        if self._active_wan_commands or self.frameModificationRunning:
            self._set_status("Cancel active generation or editing before releasing models")
            return
        command_id = f"release-all-{uuid4().hex}"
        self._pending_release_command_id = command_id
        self._wan_worker.send(ReleaseAllModelsRequest(command_id=command_id))
        if self._krea_loaded or self._krea_load_command_id is not None:
            self._krea_worker.send("shutdown")
            self._krea_loaded = False
            self._krea_load_command_id = None
        self._set_status("Releasing Wan and Krea accelerator residency…")

    @Slot()
    def inspectLocalKreaBackend(self) -> None:  # noqa: N802
        self._krea_status = "Inspecting isolated Krea runtime…"
        self._krea_worker.send(
            "probe",
            {"comfyui_root": str(self._comfyui_root)},
        )
        self.statusChanged.emit()

    @Slot()
    def loadLocalKreaBackend(self) -> None:  # noqa: N802
        if self._active_wan_commands:
            self._set_status("Finish or cancel Wan generation before loading Krea")
            return
        if self._selected_wan_model_id is not None:
            self._wan_worker.send(
                ReleaseWanModelRequest(
                    command_id=f"release-for-krea-{uuid4().hex}",
                    backend_id=BACKEND_ID,
                    model_id=self._selected_wan_model_id,
                )
            )
            self._selected_wan_model_id = None
        self._krea_load_command_id = self._krea_worker.send(
            "load_model",
            {
                "comfyui_root": str(self._comfyui_root),
                "memory_policy": self._session.project.project_settings.memory_policy,
            },
        )
        self._krea_status = "Loading Krea through the accelerator worker…"
        self._set_status("Switching model residency to Krea")

    @Slot(str, str)
    def generateCharacterSheetEntry(  # noqa: N802
        self,
        entry_name: str,
        pose_prompt: str,
    ) -> None:
        self.generateCharacterSheetEntryForSheet(0, entry_name, pose_prompt)

    @Slot(int, str, str)
    def generateCharacterSheetEntryForSheet(  # noqa: N802
        self,
        sheet_index: int,
        entry_name: str,
        pose_prompt: str,
    ) -> None:
        if not self._krea_loaded:
            self._set_status("Load the local Krea backend before generating a sheet entry")
            return
        if not self._session.project.character_sheets:
            self._set_status("Create a character before generating a sheet entry")
            return
        if not 0 <= sheet_index < len(self._session.project.character_sheets):
            self._set_status("Select an existing character sheet")
            return
        sheet = self._session.project.character_sheets[sheet_index]
        identity = next(
            item
            for item in self._session.project.characters
            if item.identity_id == sheet.identity_id
        )
        appearance = next(
            item
            for item in self._session.project.appearance_profiles
            if item.appearance_id == sheet.appearance_id
        )
        try:
            request = CharacterSheetImageRequest(
                identity_id=identity.identity_id,
                appearance_id=appearance.appearance_id,
                entry_name=entry_name.strip() or "generated pose",
                identity_prompt=identity.identity_prompt,
                appearance_prompt=appearance.style_prompt,
                pose_prompt=pose_prompt.strip(),
                width=self._session.project.project_settings.width,
                height=self._session.project.project_settings.height,
                seed=len(sheet.entries) + 1,
            )
        except Exception as error:
            self._set_status(f"Character-sheet generation failed: {error}")
            return
        command_id = self._krea_worker.send(
            "generate_baseline",
            {"request": request.to_k2_request()},
        )
        self._pending_krea_jobs[command_id] = {
            "operation": "character_sheet_entry",
            "sheet_id": sheet.sheet_id,
            "entry_name": request.entry_name,
            "identity_id": identity.identity_id,
            "appearance_id": appearance.appearance_id,
            "request": request.model_dump(mode="json"),
        }
        self._set_status("Generating immutable character-sheet entry with Krea…")

    @Slot(int, str, str)
    def duplicateSheetAppearance(  # noqa: N802
        self,
        sheet_index: int,
        appearance_name: str,
        style_prompt: str,
    ) -> None:
        if not self._krea_loaded:
            self._set_status("Load Krea before duplicating a sheet appearance")
            return
        try:
            source = self._session.project.character_sheets[sheet_index]
            if not source.entries:
                raise ValueError("source sheet has no pose/view entries")
            if not appearance_name.strip() or not style_prompt.strip():
                raise ValueError("appearance name and style prompt are required")
            identity = next(
                item
                for item in self._session.project.characters
                if item.identity_id == source.identity_id
            )
            target_profile = AppearanceProfile(
                appearance_id=f"appearance-{uuid4().hex}",
                identity_id=source.identity_id,
                name=appearance_name.strip(),
                style_prompt=style_prompt.strip(),
            )
            assets = {item.asset_id: item for item in self._session.project.assets}
            jobs = []
            for entry in source.entries:
                request = RestyleEntryRequest(
                    source_entry_id=entry.entry_id,
                    source_asset_id=entry.image_asset_id,
                    identity_prompt=identity.identity_prompt,
                    target_appearance_prompt=target_profile.style_prompt,
                    seed=len(jobs) + 1,
                )
                jobs.append(
                    {
                        "entry": entry,
                        "request": request,
                        "asset_path": str(
                            self._asset_store.resolve_ref(assets[entry.image_asset_id])
                        ),
                    }
                )
        except Exception as error:
            self._set_status(f"Appearance duplication failed: {error}")
            return
        group_id = f"style-duplication-{uuid4().hex}"
        self._style_duplications[group_id] = {
            "source_sheet_id": source.sheet_id,
            "target_sheet_id": f"sheet-{uuid4().hex}",
            "target_name": f"{identity.name} — {target_profile.name}",
            "target_profile": target_profile,
            "jobs": jobs,
            "replacements": [],
            "assets": [],
            "provenance": [],
        }
        self._start_next_style_duplication(group_id)
        self._set_status(
            f"Restyling {len(source.entries)} immutable pose/view entries with Krea…"
        )

    def _start_next_style_duplication(self, group_id: str) -> None:
        group = self._style_duplications[group_id]
        jobs = group["jobs"]
        if not isinstance(jobs, list) or not jobs:
            self._finish_style_duplication(group_id)
            return
        job = jobs.pop(0)
        request = job["request"]
        entry = job["entry"]
        command_id = self._krea_worker.send(
            "edit_image",
            {
                "request": request.to_k2_request(),
                "asset_paths": {entry.image_asset_id: job["asset_path"]},
            },
        )
        self._pending_krea_jobs[command_id] = {
            "operation": "style_duplication_entry",
            "group_id": group_id,
            "source_entry_id": entry.entry_id,
            "source_asset_id": entry.image_asset_id,
            "request": request.model_dump(mode="json"),
            "input_asset_ids": (entry.image_asset_id,),
        }

    @Slot(int, str, str, str, str, str)
    def loadLocalWanModel(  # noqa: N802
        self,
        model_index: int,
        vae: str,
        text_encoder: str,
        precision: str,
        quantization: str,
        offload_mode: str,
    ) -> None:
        if self._inspected_capabilities is None:
            self._set_status("Inspect the local Wan backend before loading a model")
            return
        if not 0 <= model_index < len(self._inspected_capabilities.model_variants):
            self._set_status("Select a discovered Wan model")
            return
        try:
            model = self._inspected_capabilities.model_variants[model_index]
            settings = self._session.project.project_settings
            if not model.supports_resolution(settings.width, settings.height):
                supported = ", ".join(
                    f"{item.width}x{item.height}" for item in model.supported_resolutions
                )
                raise ValueError(
                    f"project canvas {settings.width}x{settings.height} is unsupported; "
                    f"choose one of {supported}"
                )
            if vae not in self._backend_vae_models:
                raise ValueError("select an installed Wan VAE")
            if text_encoder not in self._backend_text_encoder_models:
                raise ValueError("select an installed Wan text encoder")
            if precision not in model.supported_precisions:
                raise ValueError("selected precision is unsupported")
            if quantization not in model.supported_quantizations:
                raise ValueError("selected quantization is unsupported")
            if offload_mode not in model.supported_offload_modes:
                raise ValueError("selected offload mode is unsupported")
            request = LoadModelRequest(
                command_id=f"load-{uuid4().hex}",
                backend_id=self._inspected_capabilities.backend_id,
                model_id=model.model_id,
                precision=precision,
                quantization=quantization,
                offload_mode=offload_mode,
                component_model_ids={"vae": vae, "text_encoder": text_encoder},
            )
        except Exception as error:
            self._set_status(f"Wan model selection failed: {error}")
            return
        self._pending_model_command_id = request.command_id
        self._pending_wan_model_id = model.model_id
        if self._krea_loaded:
            self._krea_worker.send("shutdown")
            self._krea_loaded = False
        self._wan_worker.send(request)
        self._set_status(f"Loading {model.display_name} through the isolated worker…")

    @Slot(int)
    def selectWanModel(self, model_index: int) -> None:  # noqa: N802
        if (
            self._inspected_capabilities is None
            or not 0 <= model_index < len(self._inspected_capabilities.model_variants)
        ):
            self._set_status("Select a discovered Wan model")
            return
        self._wan_model_control_index = model_index
        model = self._inspected_capabilities.model_variants[model_index]
        self._set_status(f"Selected {model.display_name}; compatible controls updated")
        self.projectChanged.emit()

    @Slot()
    def closeWorker(self) -> None:  # noqa: N802
        self._wan_worker.close()
        self._krea_worker.close()
        self._export_runner.cancel()
        self._frame_runner.cancel()
        self._batch_frame_runner.cancel()
        self._frame_extraction_runner.cancel()

    @Slot(str)
    def createMannequinScene(self, name: str) -> None:  # noqa: N802
        scene = default_mannequin_scene(
            scene_id=f"mannequin-scene-{uuid4().hex}",
            name=name.strip() or "Untitled pose",
            width=self._session.project.project_settings.width,
            height=self._session.project.project_settings.height,
        )
        self._session.project = save_mannequin_scene(self._session.project, scene)
        self._mannequin_instance_index = 0
        self._refresh_mannequin_preview()
        self._append_event(f"Created integrated mannequin scene {scene.name}")
        self._set_status("Adjust pose and camera, then render reproducible guides")
        self.projectChanged.emit()

    @Slot(float, float)
    def setMannequinArmPose(self, left_degrees: float, right_degrees: float) -> None:  # noqa: N802
        if not self._session.project.mannequin_scenes:
            self._set_status("Create a mannequin scene first")
            return
        scene = self._session.project.mannequin_scenes[-1]
        instance_index, instance = self._selected_mannequin_instance(scene)
        rotations = {
            "shoulder_l": self._z_rotation(left_degrees),
            "shoulder_r": self._z_rotation(right_degrees),
        }
        joints = tuple(
            JointPose(
                joint_name=joint.joint_name,
                rotation=rotations.get(joint.joint_name, joint.rotation),
            )
            for joint in instance.joints
        )
        changed_instance = instance.model_copy(update={"joints": joints})
        changed_scene = scene.model_copy(
            update={
                "instances": tuple(
                    changed_instance if index == instance_index else item
                    for index, item in enumerate(scene.instances)
                )
            }
        )
        self._session.project = save_mannequin_scene(self._session.project, changed_scene)
        self._refresh_mannequin_preview()
        self.projectChanged.emit()

    @Slot(str)
    def addMannequinInstance(self, name: str) -> None:  # noqa: N802
        if not self._session.project.mannequin_scenes:
            self._set_status("Create a mannequin scene first")
            return
        try:
            scene = self._session.project.mannequin_scenes[-1]
            template = scene.instances[0]
            instance = MannequinInstance(
                instance_id=f"{scene.scene_id}-mannequin-{uuid4().hex}",
                name=name.strip() or f"Mannequin {len(scene.instances) + 1}",
                skeleton_id=template.skeleton_id,
                skeleton=template.skeleton,
                joints=tuple(
                    JointPose(joint_name=item.joint_name) for item in template.joints
                ),
                body_proportions=dict(template.body_proportions),
                world_transform=Transform(
                    translation=Vector3(x=0.8 * len(scene.instances))
                ),
            )
            changed = scene.model_copy(
                update={"instances": (*scene.instances, instance)}
            )
            self._session.project = save_mannequin_scene(
                self._session.project,
                changed,
            )
            self._mannequin_instance_index = len(changed.instances) - 1
        except Exception as error:
            self._set_status(f"Mannequin could not be added: {error}")
            return
        self._refresh_mannequin_preview()
        self._set_status(f"Added and selected {instance.name}")
        self.projectChanged.emit()

    @Slot(int)
    def selectMannequinInstance(self, instance_index: int) -> None:  # noqa: N802
        if not self._session.project.mannequin_scenes:
            self._set_status("Create a mannequin scene first")
            return
        scene = self._session.project.mannequin_scenes[-1]
        if not 0 <= instance_index < len(scene.instances):
            self._set_status("Select an existing mannequin")
            return
        self._mannequin_instance_index = instance_index
        self._set_status(f"Selected {scene.instances[instance_index].name}")
        self.projectChanged.emit()

    @Slot(int, float, float, float)
    def setMannequinJointRotation(  # noqa: N802
        self,
        joint_index: int,
        x_degrees: float,
        y_degrees: float,
        z_degrees: float,
    ) -> None:
        if not self._session.project.mannequin_scenes:
            self._set_status("Create a mannequin scene first")
            return
        try:
            if any(
                not -180.0 <= value <= 180.0
                for value in (x_degrees, y_degrees, z_degrees)
            ):
                raise ValueError("joint rotations must be between -180 and 180 degrees")
            scene = self._session.project.mannequin_scenes[-1]
            instance_index, instance = self._selected_mannequin_instance(scene)
            joint = instance.joints[joint_index]
            changed_joint = joint.model_copy(
                update={
                    "rotation": self._euler_rotation(
                        x_degrees,
                        y_degrees,
                        z_degrees,
                    )
                }
            )
            joints = tuple(
                changed_joint if index == joint_index else item
                for index, item in enumerate(instance.joints)
            )
            changed_instance = instance.model_copy(update={"joints": joints})
            changed_scene = scene.model_copy(
                update={
                    "instances": tuple(
                        changed_instance if index == instance_index else item
                        for index, item in enumerate(scene.instances)
                    )
                }
            )
            self._session.project = save_mannequin_scene(
                self._session.project,
                changed_scene,
            )
        except Exception as error:
            self._set_status(f"Mannequin joint update failed: {error}")
            return
        self._refresh_mannequin_preview()
        self._set_status(f"Updated {joint.joint_name} rotation")
        self.projectChanged.emit()

    @Slot(float)
    def setMannequinFocalLength(self, focal_length_mm: float) -> None:  # noqa: N802
        if not self._session.project.mannequin_scenes:
            return
        scene = self._session.project.mannequin_scenes[-1]
        camera = scene.camera.model_copy(
            update={"focal_length_mm": max(12.0, min(200.0, focal_length_mm))}
        )
        self._session.project = save_mannequin_scene(
            self._session.project, scene.model_copy(update={"camera": camera})
        )
        self._refresh_mannequin_preview()
        self.projectChanged.emit()

    @Slot(float, float, float, float, float, float)
    def setMannequinCamera(  # noqa: N802
        self,
        x: float,
        y: float,
        z: float,
        yaw_degrees: float,
        pitch_degrees: float,
        framing_scale: float,
    ) -> None:
        if not self._session.project.mannequin_scenes:
            self._set_status("Create a mannequin scene first")
            return
        try:
            if not 0.2 <= framing_scale <= 1.0:
                raise ValueError("camera framing scale must be between 0.2 and 1.0")
            scene = self._session.project.mannequin_scenes[-1]
            margin = (1.0 - framing_scale) / 2
            camera = scene.camera.model_copy(
                update={
                    "position": Vector3(x=x, y=y, z=z),
                    "orientation": self._camera_rotation(yaw_degrees, pitch_degrees),
                    "crop": (
                        None
                        if framing_scale >= 0.999
                        else (margin, margin, 1.0 - margin, 1.0 - margin)
                    ),
                }
            )
            self._session.project = save_mannequin_scene(
                self._session.project,
                scene.model_copy(update={"camera": camera}),
            )
        except Exception as error:
            self._set_status(f"Mannequin camera update failed: {error}")
            return
        self._refresh_mannequin_preview()
        self._set_status("Mannequin camera angle, position, and framing updated")
        self.projectChanged.emit()

    @Slot(float, float, float)
    def setMannequinProportions(  # noqa: N802
        self,
        height_scale: float,
        width_scale: float,
        limb_scale: float,
    ) -> None:
        if not self._session.project.mannequin_scenes:
            self._set_status("Create a mannequin scene first")
            return
        try:
            if any(not 0.5 <= value <= 1.75 for value in (height_scale, width_scale, limb_scale)):
                raise ValueError("mannequin proportions must be between 0.5 and 1.75")
            scene = self._session.project.mannequin_scenes[-1]
            instance_index, selected_instance = self._selected_mannequin_instance(scene)
            instance = selected_instance.model_copy(
                update={
                    "body_proportions": {
                        **selected_instance.body_proportions,
                        "height_scale": height_scale,
                        "width_scale": width_scale,
                        "limb_scale": limb_scale,
                    }
                }
            )
            changed = scene.model_copy(
                update={
                    "instances": tuple(
                        instance if index == instance_index else item
                        for index, item in enumerate(scene.instances)
                    )
                }
            )
            self._session.project = save_mannequin_scene(self._session.project, changed)
        except Exception as error:
            self._set_status(f"Mannequin proportion update failed: {error}")
            return
        self._refresh_mannequin_preview()
        self._set_status("Mannequin proportions updated")
        self.projectChanged.emit()

    @Slot(float, float, float, float)
    def setMannequinLight(  # noqa: N802
        self,
        intensity: float,
        x: float,
        y: float,
        z: float,
    ) -> None:
        if not self._session.project.mannequin_scenes:
            self._set_status("Create a mannequin scene first")
            return
        try:
            scene = self._session.project.mannequin_scenes[-1]
            light = SceneLight(
                light_id=f"{scene.scene_id}-key-light",
                kind="point",
                transform=Transform(translation=Vector3(x=x, y=y, z=z)),
                intensity=intensity,
            )
            self._session.project = save_mannequin_scene(
                self._session.project,
                scene.model_copy(update={"lights": (light,)}),
            )
        except Exception as error:
            self._set_status(f"Mannequin light update failed: {error}")
            return
        self._refresh_mannequin_preview()
        self._set_status("Mannequin guide light updated")
        self.projectChanged.emit()

    @Slot(int)
    def applySavedMannequinPose(self, pose_index: int) -> None:  # noqa: N802
        if not self._session.project.mannequin_scenes:
            self._set_status("Create a mannequin scene first")
            return
        try:
            pose = self._session.project.mannequin_poses[pose_index]
            scene = self._session.project.mannequin_scenes[-1]
            instance_index, selected_instance = self._selected_mannequin_instance(
                scene
            )
            instance = apply_pose(selected_instance, pose)
            self._session.project = save_mannequin_scene(
                self._session.project,
                scene.model_copy(
                    update={
                        "instances": tuple(
                            instance if index == instance_index else item
                            for index, item in enumerate(scene.instances)
                        )
                    }
                ),
            )
        except Exception as error:
            self._set_status(f"Saved pose could not be applied: {error}")
            return
        self._refresh_mannequin_preview()
        self._set_status(f"Applied reusable pose {pose.name}")
        self.projectChanged.emit()

    @Slot(int)
    def associateMannequinRegion(self, region_index: int) -> None:  # noqa: N802
        if not self._session.project.mannequin_scenes:
            self._set_status("Create a mannequin scene first")
            return
        try:
            region = self._draft_keyframe_regions[region_index]
            scene = self._session.project.mannequin_scenes[-1]
            instance_index, selected_instance = self._selected_mannequin_instance(
                scene
            )
            instance = selected_instance.model_copy(
                update={"character_region_id": region.region_id}
            )
            self._session.project = save_mannequin_scene(
                self._session.project,
                scene.model_copy(
                    update={
                        "instances": tuple(
                            instance if index == instance_index else item
                            for index, item in enumerate(scene.instances)
                        )
                    }
                ),
            )
        except Exception as error:
            self._set_status(f"Mannequin region association failed: {error}")
            return
        self._set_status(f"Mannequin associated with {region.name}")
        self.projectChanged.emit()

    @Slot(str, float, float, float)
    def addMannequinProp(  # noqa: N802
        self,
        name: str,
        x: float,
        y: float,
        z: float,
    ) -> None:
        if not self._session.project.mannequin_scenes:
            self._set_status("Create a mannequin scene first")
            return
        try:
            if not name.strip():
                raise ValueError("prop name is required")
            scene = self._session.project.mannequin_scenes[-1]
            prop = SceneProp(
                prop_id=f"mannequin-prop-{uuid4().hex}",
                name=name.strip(),
                transform=Transform(translation=Vector3(x=x, y=y, z=z)),
            )
            self._session.project = save_mannequin_scene(
                self._session.project,
                scene.model_copy(update={"props": (*scene.props, prop)}),
            )
        except Exception as error:
            self._set_status(f"Mannequin prop could not be added: {error}")
            return
        self._set_status(f"Added mannequin scene prop {prop.name}")
        self.projectChanged.emit()

    @Slot(str, float, float, float)
    def addMannequinContact(  # noqa: N802
        self,
        joint_name: str,
        x: float,
        y: float,
        z: float,
    ) -> None:
        if not self._session.project.mannequin_scenes:
            self._set_status("Create a mannequin scene first")
            return
        try:
            scene = self._session.project.mannequin_scenes[-1]
            _instance_index, instance = self._selected_mannequin_instance(scene)
            known_joints = {item.joint_name for item in instance.joints}
            if joint_name.strip() not in known_joints:
                raise ValueError("contact joint is not present on the mannequin")
            contact = ContactConstraint(
                instance_id=instance.instance_id,
                joint_name=joint_name.strip(),
                target=Vector3(x=x, y=y, z=z),
            )
            self._session.project = save_mannequin_scene(
                self._session.project,
                scene.model_copy(
                    update={
                        "contact_constraints": (*scene.contact_constraints, contact)
                    }
                ),
            )
        except Exception as error:
            self._set_status(f"Mannequin contact could not be added: {error}")
            return
        self._set_status(f"Added contact constraint for {contact.joint_name}")
        self.projectChanged.emit()

    @Slot(QUrl, str)
    def importMannequinGuide(self, source_url: QUrl, guide_kind: str) -> None:  # noqa: N802
        if not self._session.project.mannequin_scenes:
            self._set_status("Create a mannequin scene first")
            return
        try:
            kind = GuideKind(guide_kind)
            source = Path(source_url.toLocalFile()).expanduser().resolve()
            record = self._asset_store.register_imported(
                source,
                media_type=image_media_type(source),
                metadata={
                    "operation": "import_mannequin_guide",
                    "guide_kind": kind.value,
                },
            )
            asset = self._wan_asset(record, AssetKind.MANNEQUIN_GUIDE)
            scene = self._session.project.mannequin_scenes[-1]
            provenance = ProvenanceRecord(
                provenance_id=f"provenance-{uuid4().hex}",
                operation="import_mannequin_guide",
                created_at=datetime.now(UTC),
                output_asset_ids=(asset.asset_id,),
                parameters={"scene_id": scene.scene_id, "guide_kind": kind.value},
            )
            self._session.project = attach_rendered_guides(
                self._session.project,
                scene_id=scene.scene_id,
                assets=(asset,),
                provenance=(provenance,),
            )
        except Exception as error:
            self._set_status(f"Mannequin guide import failed: {error}")
            return
        self._append_event(f"Imported immutable {kind.value} mannequin guide")
        self._set_status("Mannequin guide imported for capability-gated conditioning")
        self.projectChanged.emit()

    @Slot(str)
    def saveCurrentMannequinPose(self, name: str) -> None:  # noqa: N802
        if not self._session.project.mannequin_scenes:
            self._set_status("Create a mannequin scene first")
            return
        scene = self._session.project.mannequin_scenes[-1]
        _instance_index, instance = self._selected_mannequin_instance(scene)
        pose = save_pose_from_instance(
            instance,
            pose_id=f"mannequin-pose-{uuid4().hex}",
            name=name.strip() or scene.name,
        )
        self._session.project = register_mannequin_pose(self._session.project, pose)
        self._append_event(f"Saved reusable mannequin pose {pose.name}")
        self._set_status("Pose saved in the project library")
        self.projectChanged.emit()

    @Slot()
    def renderCurrentMannequinGuides(self) -> None:  # noqa: N802
        if not self._session.project.mannequin_scenes:
            self._set_status("Create a mannequin scene first")
            return
        scene = self._session.project.mannequin_scenes[-1]
        assets = []
        records = []
        try:
            with tempfile.TemporaryDirectory(prefix="wan2lab-guides-") as directory:
                for guide in render_mannequin_guides(scene, Path(directory)):
                    record = self._asset_store.register_generated(
                        guide.path,
                        media_type="image/png",
                        metadata={"guide_kind": guide.kind.value, "scene_id": scene.scene_id},
                    )
                    records.append((guide.kind, record))
            for kind, record in records:
                assets.append(self._wan_asset(record, AssetKind.MANNEQUIN_GUIDE))
            provenance = tuple(
                ProvenanceRecord(
                    provenance_id=f"provenance-{uuid4().hex}",
                    operation=f"render_mannequin_{kind.value}_guide",
                    created_at=datetime.now(UTC),
                    output_asset_ids=(asset.asset_id,),
                    parameters={"scene_id": scene.scene_id, "guide_kind": kind.value},
                )
                for (kind, _record), asset in zip(records, assets, strict=True)
            )
            self._session.project = attach_rendered_guides(
                self._session.project,
                scene_id=scene.scene_id,
                assets=tuple(assets),
                provenance=provenance,
            )
        except Exception as error:
            self._set_status(f"Guide render failed: {error}")
            return
        self._append_event("Rendered shaded, silhouette, and depth mannequin guides")
        self._set_status("Guides saved; Krea i2i fallback is available")
        self.projectChanged.emit()

    @Slot(QUrl)
    def importBlenderScene(self, source_url: QUrl) -> None:  # noqa: N802
        source = Path(source_url.toLocalFile())
        try:
            record = self._asset_store.register_imported(
                source, media_type="application/vnd.wan2lab.mannequin+json"
            )
            asset = self._wan_asset(record, AssetKind.PROJECT)
            scene = import_blender_scene_document(
                source.read_bytes(), imported_asset_id=asset.asset_id
            )
            provenance = ProvenanceRecord(
                provenance_id=f"provenance-{uuid4().hex}",
                operation="import_blender_mannequin_scene",
                created_at=datetime.now(UTC),
                output_asset_ids=(asset.asset_id,),
                parameters={"source_format": "wan2lab-mannequin-json"},
            )
            self._session.project = register_blender_scene(
                self._session.project,
                scene=scene,
                source_asset=asset,
                provenance=provenance,
            )
        except Exception as error:
            self._set_status(f"Blender scene import failed: {error}")
            return
        self._refresh_mannequin_preview()
        self._append_event(f"Imported Blender mannequin scene {scene.name}")
        self._set_status("Blender scene imported into the renderer-neutral project model")
        self.projectChanged.emit()

    @Slot(str, str, str, str)
    def addCharacter(
        self,
        name: str,
        identity_prompt: str,
        appearance_name: str,
        style_prompt: str,
    ) -> None:  # noqa: N802
        if not name.strip() or not identity_prompt.strip() or not appearance_name.strip():
            self._set_status("Character name, identity prompt, and appearance name are required")
            return
        identity_id = f"character-{uuid4().hex}"
        appearance_id = f"appearance-{uuid4().hex}"
        sheet_id = f"sheet-{uuid4().hex}"
        identity = CharacterIdentity(
            identity_id=identity_id,
            name=name.strip(),
            identity_prompt=identity_prompt.strip(),
            character_sheet_ids=(sheet_id,),
        )
        appearance = AppearanceProfile(
            appearance_id=appearance_id,
            identity_id=identity_id,
            name=appearance_name.strip(),
            style_prompt=style_prompt.strip(),
        )
        sheet = CharacterSheet(
            sheet_id=sheet_id,
            name=f"{name.strip()} — {appearance_name.strip()}",
            identity_id=identity_id,
            appearance_id=appearance_id,
        )
        project = self._session.project.model_copy(
            update={
                "characters": (*self._session.project.characters, identity),
                "appearance_profiles": (
                    *self._session.project.appearance_profiles,
                    appearance,
                ),
                "character_sheets": (*self._session.project.character_sheets, sheet),
            }
        )
        self._session.project = Wan2LabProject.model_validate(project.model_dump())
        self._append_event(f"Created character {identity.name} with appearance {appearance.name}")
        self._set_status("Character sheet ready for imported or generated entries")
        self.projectChanged.emit()

    @Slot(int, str, str, str, str, str, str, str, str, str, str)
    def updateCharacterProfile(  # noqa: N802
        self,
        sheet_index: int,
        identity_prompt: str,
        stable_description: str,
        trigger_text: str,
        permanent_features: str,
        style_prompt: str,
        clothing_state: str,
        hairstyle_state: str,
        makeup_accessory_state: str,
        visible_features: str,
        nudity_state: str,
    ) -> None:
        try:
            sheet = self._session.project.character_sheets[sheet_index]
            normalized_identity_prompt = identity_prompt.strip()
            if not normalized_identity_prompt:
                raise ValueError("stable identity prompt is required")
            features = tuple(
                item.strip() for item in permanent_features.split(",") if item.strip()
            )
            visible = tuple(
                item.strip() for item in visible_features.split(",") if item.strip()
            )
            characters = tuple(
                item.model_copy(
                    update={
                        "identity_prompt": normalized_identity_prompt,
                        "stable_description": stable_description.strip(),
                        "trigger_text": trigger_text.strip(),
                        "permanent_features": features,
                    }
                )
                if item.identity_id == sheet.identity_id
                else item
                for item in self._session.project.characters
            )
            appearances = tuple(
                item.model_copy(
                    update={
                        "style_prompt": style_prompt.strip(),
                        "clothing_state": clothing_state.strip(),
                        "hairstyle_state": hairstyle_state.strip(),
                        "makeup_accessory_state": makeup_accessory_state.strip(),
                        "visible_features": visible,
                        "nudity_state": nudity_state.strip() or None,
                    }
                )
                if item.appearance_id == sheet.appearance_id
                else item
                for item in self._session.project.appearance_profiles
            )
            self._session.project = Wan2LabProject.model_validate(
                self._session.project.model_copy(
                    update={
                        "characters": characters,
                        "appearance_profiles": appearances,
                    }
                ).model_dump()
            )
        except Exception as error:
            self._set_status(f"Character profile update failed: {error}")
            return
        self._append_event(f"Updated identity and appearance metadata for {sheet.name}")
        self._set_status("Character identity and appearance profile updated")
        self.projectChanged.emit()

    @Slot(int, str, QUrl, str, str, str, str, float)
    def importCharacterAdapter(  # noqa: N802
        self,
        sheet_index: int,
        target_scope: str,
        source_url: QUrl,
        family: str,
        kind: str,
        model_family: str,
        trigger: str,
        strength: float,
    ) -> None:
        try:
            sheet = self._session.project.character_sheets[sheet_index]
            selected_family = AdapterFamily(family)
            selected_kind = AdapterKind(kind)
            if target_scope not in {"identity", "appearance"}:
                raise ValueError("adapter target must be identity or appearance")
            if target_scope == "appearance" and selected_family is not AdapterFamily.KREA:
                raise ValueError("appearance adapters must target the Krea image model")
            adapter = AdapterRef(
                adapter_id=f"adapter-{uuid4().hex}",
                asset_id="pending-adapter-asset",
                family=selected_family,
                kind=selected_kind,
                model_family=model_family.strip(),
                trigger=trigger.strip(),
                default_strength=strength,
            )
            source = Path(source_url.toLocalFile()).expanduser().resolve()
            record = self._asset_store.register_imported(
                source,
                media_type="application/octet-stream",
                metadata={
                    "operation": "import_character_adapter",
                    "target_scope": target_scope,
                    "family": selected_family.value,
                    "kind": selected_kind.value,
                    "model_family": model_family.strip(),
                },
            )
            provenance_id = f"provenance-{uuid4().hex}"
            asset = self._wan_asset(record, AssetKind.ADAPTER).model_copy(
                update={"creation_operation_id": provenance_id}
            )
            adapter = adapter.model_copy(update={"asset_id": asset.asset_id})
            provenance = ProvenanceRecord(
                provenance_id=provenance_id,
                operation="import_character_adapter",
                created_at=datetime.now(UTC),
                input_asset_ids=(),
                output_asset_ids=(asset.asset_id,),
                parameters={
                    "adapter_id": adapter.adapter_id,
                    "target_scope": target_scope,
                    "identity_id": sheet.identity_id,
                    "appearance_id": (
                        sheet.appearance_id if target_scope == "appearance" else None
                    ),
                },
            )
            characters = self._session.project.characters
            appearances = self._session.project.appearance_profiles
            if target_scope == "identity":
                characters = tuple(
                    item.model_copy(update={"adapter_refs": (*item.adapter_refs, adapter)})
                    if item.identity_id == sheet.identity_id
                    else item
                    for item in characters
                )
            else:
                appearances = tuple(
                    item.model_copy(update={"adapter_refs": (*item.adapter_refs, adapter)})
                    if item.appearance_id == sheet.appearance_id
                    else item
                    for item in appearances
                )
            self._session.project = Wan2LabProject.model_validate(
                self._session.project.model_copy(
                    update={
                        "assets": (*self._session.project.assets, asset),
                        "characters": characters,
                        "appearance_profiles": appearances,
                        "generation_records": (
                            *self._session.project.generation_records,
                            provenance,
                        ),
                    }
                ).model_dump()
            )
        except Exception as error:
            self._set_status(f"Adapter import failed: {error}")
            return
        self._append_event(
            f"Imported immutable {selected_family.value} {selected_kind.value} "
            f"for {target_scope} {sheet.name}"
        )
        self._set_status("Character adapter imported with explicit model compatibility")
        self.projectChanged.emit()

    @Slot(QUrl, str)
    def importSheetEntry(self, source_url: QUrl, name: str) -> None:  # noqa: N802
        self.importSheetEntryForSheet(0, source_url, name)

    @Slot(int, QUrl, str)
    def importSheetEntryForSheet(  # noqa: N802
        self,
        sheet_index: int,
        source_url: QUrl,
        name: str,
    ) -> None:
        if not self._session.project.character_sheets:
            self._set_status("Create a character before importing a sheet entry")
            return
        if not 0 <= sheet_index < len(self._session.project.character_sheets):
            self._set_status("Select an existing character sheet")
            return
        try:
            source = Path(source_url.toLocalFile())
            record = self._asset_store.register_imported(
                source, media_type=image_media_type(source)
            )
            provenance_id = f"provenance-{uuid4().hex}"
            asset = self._wan_asset(record, AssetKind.IMAGE).model_copy(
                update={"creation_operation_id": provenance_id}
            )
            sheet = self._session.project.character_sheets[sheet_index]
            provenance = ProvenanceRecord(
                provenance_id=provenance_id,
                operation="import_character_sheet_entry",
                created_at=datetime.now(UTC),
                output_asset_ids=(asset.asset_id,),
            )
            entry = PoseViewEntry(
                entry_id=f"entry-{uuid4().hex}",
                name=name.strip() or Path(source_url.toLocalFile()).stem,
                image_asset_id=asset.asset_id,
                identity_id=sheet.identity_id,
                appearance_id=sheet.appearance_id,
                source_type=PoseViewSource.IMPORTED,
                provenance_id=provenance.provenance_id,
            )
            self._session.project = register_pose_view_entry(
                self._session.project,
                sheet_id=sheet.sheet_id,
                entry=entry,
                asset=asset,
                provenance=provenance,
            )
        except Exception as error:
            self._set_status(f"Sheet import failed: {error}")
            return
        self._append_event(f"Imported character-sheet entry {entry.name}")
        self._set_status("Character-sheet entry imported as an immutable asset")
        self.projectChanged.emit()

    @Slot(int, int, str, str)
    def reviewSheetEntry(  # noqa: N802
        self,
        sheet_index: int,
        entry_index: int,
        name: str,
        approval_state: str,
    ) -> None:
        try:
            sheet = self._session.project.character_sheets[sheet_index]
            entry = sheet.entries[entry_index]
            self._session.project = update_pose_view_entry(
                self._session.project,
                sheet_id=sheet.sheet_id,
                entry_id=entry.entry_id,
                name=name.strip() or entry.name,
                approval_state=ApprovalState(approval_state),
            )
        except Exception as error:
            self._set_status(f"Sheet-entry review failed: {error}")
            return
        self._append_event(
            f"Reviewed {entry.name} as {approval_state}; immutable image preserved"
        )
        self._set_status("Character-sheet entry review saved")
        self.projectChanged.emit()

    @Slot(int, int, str, str, str, str, str, int)
    def updateSheetEntryMetadata(  # noqa: N802
        self,
        sheet_index: int,
        entry_index: int,
        name: str,
        view_label: str,
        pose_label: str,
        framing_label: str,
        expression_label: str,
        mannequin_scene_index: int,
    ) -> None:
        try:
            sheet = self._session.project.character_sheets[sheet_index]
            entry = sheet.entries[entry_index]
            mannequin_scene_id = (
                self._session.project.mannequin_scenes[mannequin_scene_index].scene_id
                if mannequin_scene_index >= 0
                else None
            )
            self._session.project = update_pose_view_metadata(
                self._session.project,
                sheet_id=sheet.sheet_id,
                entry_id=entry.entry_id,
                name=name.strip() or entry.name,
                view_label=view_label,
                pose_label=pose_label,
                framing_label=framing_label,
                expression_label=expression_label,
                mannequin_scene_id=mannequin_scene_id,
            )
        except Exception as error:
            self._set_status(f"Sheet-entry metadata update failed: {error}")
            return
        self._set_status("Pose/view metadata and mannequin link saved")
        self.projectChanged.emit()

    @Slot(int, int, QUrl)
    def replaceSheetEntry(  # noqa: N802
        self,
        sheet_index: int,
        entry_index: int,
        source_url: QUrl,
    ) -> None:
        try:
            sheet = self._session.project.character_sheets[sheet_index]
            source_entry = sheet.entries[entry_index]
            source = Path(source_url.toLocalFile()).expanduser().resolve()
            record = self._asset_store.create_derived(
                source,
                parent_asset_ids=(source_entry.image_asset_id,),
                media_type=image_media_type(source),
                metadata={
                    "operation": "replace_character_sheet_entry",
                    "source_entry_id": source_entry.entry_id,
                },
            )
            provenance_id = f"provenance-{uuid4().hex}"
            asset = self._wan_asset(record, AssetKind.IMAGE).model_copy(
                update={
                    "creation_operation_id": provenance_id,
                    "immutable_source": False,
                }
            )
            provenance = ProvenanceRecord(
                provenance_id=provenance_id,
                operation="replace_character_sheet_entry",
                created_at=datetime.now(UTC),
                input_asset_ids=(source_entry.image_asset_id,),
                output_asset_ids=(asset.asset_id,),
                parameters={"source_entry_id": source_entry.entry_id},
            )
            replacement = source_entry.model_copy(
                update={
                    "entry_id": f"entry-{uuid4().hex}",
                    "image_asset_id": asset.asset_id,
                    "source_type": PoseViewSource.EDITED,
                    "parent_entry_id": source_entry.entry_id,
                    "provenance_id": provenance.provenance_id,
                    "approval_state": ApprovalState.DRAFT,
                }
            )
            self._session.project = replace_pose_view_entry(
                self._session.project,
                sheet_id=sheet.sheet_id,
                source_entry_id=source_entry.entry_id,
                replacement=replacement,
                asset=asset,
                provenance=provenance,
            )
        except Exception as error:
            self._set_status(f"Sheet-entry replacement failed: {error}")
            return
        self._append_event(
            f"Replaced {source_entry.name} non-destructively; source asset retained"
        )
        self._set_status("Replacement entry saved as an immutable review draft")
        self.projectChanged.emit()

    @Slot(int, int)
    def removeSheetEntry(self, sheet_index: int, entry_index: int) -> None:  # noqa: N802
        try:
            sheet = self._session.project.character_sheets[sheet_index]
            entry = sheet.entries[entry_index]
            self._session.project = remove_pose_view_entry(
                self._session.project,
                sheet_id=sheet.sheet_id,
                entry_id=entry.entry_id,
            )
        except Exception as error:
            self._set_status(f"Sheet-entry removal failed: {error}")
            return
        self._append_event(
            f"Removed {entry.name} from the sheet; immutable asset history was retained"
        )
        self._set_status("Sheet entry removed non-destructively")
        self.projectChanged.emit()

    @Slot(QUrl, float)
    def importKeyframe(self, source_url: QUrl, time_seconds: float) -> None:  # noqa: N802
        try:
            source = Path(source_url.toLocalFile())
            record = self._asset_store.register_imported(
                source, media_type=image_media_type(source)
            )
            provenance_id = f"provenance-{uuid4().hex}"
            asset = self._wan_asset(record, AssetKind.IMAGE).model_copy(
                update={"creation_operation_id": provenance_id}
            )
            provenance = ProvenanceRecord(
                provenance_id=provenance_id,
                operation="import_keyframe",
                created_at=datetime.now(UTC),
                output_asset_ids=(asset.asset_id,),
            )
            keyframe = Keyframe(
                keyframe_id=f"keyframe-{uuid4().hex}",
                time_ms=round(time_seconds * 1000),
                image_asset_id=asset.asset_id,
                source_type=KeyframeSource.IMPORTED,
                provenance_id=provenance.provenance_id,
                approved=True,
                locked=True,
            )
            self._session.project = add_timeline_keyframe(
                self._session.project,
                keyframe=keyframe,
                asset=asset,
                provenance=provenance,
            )
        except Exception as error:
            self._set_status(f"Keyframe import failed: {error}")
            return
        self._append_event(f"Imported keyframe at {time_seconds:g}s")
        self._set_status("Keyframe imported and placed at exact timeline time")
        self.projectChanged.emit()

    @Slot(int, int, float, float, float, float, str)
    def addKeyframeRegion(  # noqa: N802
        self,
        sheet_index: int,
        entry_index: int,
        x0: float,
        y0: float,
        x1: float,
        y1: float,
        prompt: str,
    ) -> None:
        self._add_keyframe_region(
            sheet_index,
            entry_index,
            x0,
            y0,
            x1,
            y1,
            prompt,
            "",
        )

    @Slot(int, int, float, float, float, float, str, str)
    def addKeyframeRegionWithAdapters(  # noqa: N802
        self,
        sheet_index: int,
        entry_index: int,
        x0: float,
        y0: float,
        x1: float,
        y1: float,
        prompt: str,
        adapter_spec: str,
    ) -> None:
        self._add_keyframe_region(
            sheet_index,
            entry_index,
            x0,
            y0,
            x1,
            y1,
            prompt,
            adapter_spec,
        )

    def _add_keyframe_region(
        self,
        sheet_index: int,
        entry_index: int,
        x0: float,
        y0: float,
        x1: float,
        y1: float,
        prompt: str,
        adapter_spec: str,
    ) -> None:
        if not 0 <= sheet_index < len(self._session.project.character_sheets):
            self._set_status("Select an existing character sheet")
            return
        sheet = self._session.project.character_sheets[sheet_index]
        if not 0 <= entry_index < len(sheet.entries):
            self._set_status("Select an existing pose/view entry")
            return
        try:
            rectangle = Rectangle(x0=x0, y0=y0, x1=x1, y1=y1)
            settings = self._session.project.project_settings
            if rectangle.x1 > settings.width or rectangle.y1 > settings.height:
                raise ValueError("region rectangle exceeds the project canvas")
            entry = sheet.entries[entry_index]
            identity = next(
                item
                for item in self._session.project.characters
                if item.identity_id == sheet.identity_id
            )
            appearance = next(
                item
                for item in self._session.project.appearance_profiles
                if item.appearance_id == sheet.appearance_id
            )
            available_adapters = {
                item.adapter_id: item
                for item in (*identity.adapter_refs, *appearance.adapter_refs)
            }
            selections = []
            for raw_selection in adapter_spec.split(","):
                raw_selection = raw_selection.strip()
                if not raw_selection:
                    continue
                adapter_id, separator, raw_strength = raw_selection.partition("=")
                adapter_id = adapter_id.strip()
                adapter = available_adapters.get(adapter_id)
                if adapter is None:
                    raise ValueError(f"adapter {adapter_id} is not assigned to this character")
                if adapter.family is not AdapterFamily.KREA:
                    raise ValueError(f"adapter {adapter_id} is not compatible with Krea")
                strength = float(raw_strength.strip()) if separator else adapter.default_strength
                selections.append(AdapterSelection(adapter_id=adapter_id, strength=strength))
            assignment = CharacterRegionAssignment(
                region_id=f"keyframe-region-{uuid4().hex}",
                name=f"{sheet.name} · {entry.name}",
                rectangle=rectangle,
                identity_id=sheet.identity_id,
                appearance_id=sheet.appearance_id,
                pose_view_entry_id=entry.entry_id,
                prompt=prompt.strip(),
                adapters=tuple(selections),
                priority=len(self._draft_keyframe_regions),
            )
        except Exception as error:
            self._set_status(f"Keyframe region failed: {error}")
            return
        self._draft_keyframe_regions.append(assignment)
        self._set_status("Character region added to the draft keyframe")
        self.projectChanged.emit()

    @Slot()
    def clearKeyframeRegions(self) -> None:  # noqa: N802
        self._draft_keyframe_regions.clear()
        self._set_status("Draft keyframe regions cleared")
        self.projectChanged.emit()

    @Slot(float, str, str, str)
    def generateRegionalKeyframe(  # noqa: N802
        self,
        time_seconds: float,
        scene_prompt: str,
        environment_prompt: str,
        lighting_prompt: str,
    ) -> None:
        self._generate_regional_keyframe(
            time_seconds,
            scene_prompt,
            environment_prompt,
            lighting_prompt,
            0,
        )

    @Slot(float, str, str, str, int)
    def generateRegionalKeyframeFromSource(  # noqa: N802
        self,
        time_seconds: float,
        scene_prompt: str,
        environment_prompt: str,
        lighting_prompt: str,
        source_index: int,
    ) -> None:
        self._generate_regional_keyframe(
            time_seconds,
            scene_prompt,
            environment_prompt,
            lighting_prompt,
            source_index,
        )

    def _generate_regional_keyframe(
        self,
        time_seconds: float,
        scene_prompt: str,
        environment_prompt: str,
        lighting_prompt: str,
        source_index: int,
    ) -> None:
        if not self._krea_loaded:
            self._set_status("Load the local Krea backend before generating a keyframe")
            return
        if not self._draft_keyframe_regions:
            self._set_status("Add at least one character region before generating a keyframe")
            return
        settings = self._session.project.project_settings
        try:
            composition_request = KeyframeCompositionRequest(
                width=settings.width,
                height=settings.height,
                scene_prompt=scene_prompt.strip(),
                environment_prompt=environment_prompt.strip(),
                lighting_prompt=lighting_prompt.strip(),
                region_assignments=tuple(self._draft_keyframe_regions),
                mannequin_scene_id=(
                    self._session.project.mannequin_scenes[-1].scene_id
                    if self._session.project.mannequin_scenes
                    else None
                ),
            )
            composition = compile_keyframe_composition(
                self._session.project,
                composition_request,
            )
            source_options = self._keyframe_i2i_sources()
            if not 0 <= source_index < len(source_options):
                raise ValueError("select an available approved i2i source")
            source_asset_id = source_options[source_index][1]
            mannequin_guide_asset_id = None
            conditioning_path = None
            if self._session.project.mannequin_scenes:
                scene = self._session.project.mannequin_scenes[-1]
                guides = self._mannequin_guide_assets(scene.scene_id)
                if guides:
                    conditioning = plan_krea_conditioning(
                        scene=scene,
                        capabilities=KreaMannequinCapabilities(
                            depth_control_model_ids=self._krea_depth_control_model_ids,
                            supports_i2i=True,
                        ),
                        guide_assets=guides,
                    )
                    mannequin_guide_asset_id = conditioning.guide_asset_id
                    if source_asset_id is None:
                        source_asset_id = mannequin_guide_asset_id
                    conditioning_path = conditioning.path.value
            request = ComposedKeyframeRequest(
                composition=composition,
                seed=len(self._session.project.keyframes) + 1,
                source_asset_id=source_asset_id,
                mannequin_guide_asset_id=mannequin_guide_asset_id,
                conditioning_path=conditioning_path,
            )
            required_asset_ids = {
                *(item.asset_id for item in composition.adapter_routes),
                *((source_asset_id,) if source_asset_id is not None else ()),
                *((mannequin_guide_asset_id,) if mannequin_guide_asset_id is not None else ()),
            }
            assets = {item.asset_id: item for item in self._session.project.assets}
            asset_paths = {
                asset_id: str(self._asset_store.resolve_ref(assets[asset_id]))
                for asset_id in required_asset_ids
            }
            time_ms = round(time_seconds * 1000)
            if not 0 <= time_ms <= self._session.project.timeline.duration_ms:
                raise ValueError("keyframe time is outside the timeline")
        except Exception as error:
            self._set_status(f"Keyframe generation failed: {error}")
            return
        command_id = self._krea_worker.send(
            "generate_baseline",
            {
                "request": request.to_k2_request(),
                "asset_paths": asset_paths,
            },
        )
        self._pending_krea_jobs[command_id] = {
            "operation": "regional_keyframe",
            "time_ms": time_ms,
            "scene_prompt": composition_request.scene_prompt,
            "environment_prompt": composition_request.environment_prompt,
            "lighting_prompt": composition_request.lighting_prompt,
            "region_assignments": tuple(self._draft_keyframe_regions),
            "mannequin_scene_id": composition_request.mannequin_scene_id,
            "request": request.model_dump(mode="json"),
            "input_asset_ids": tuple(sorted(required_asset_ids)),
        }
        self._set_status("Generating regional keyframe with Krea…")

    def _keyframe_i2i_sources(self) -> list[tuple[str, str | None]]:
        sources: list[tuple[str, str | None]] = [("Automatic / mannequin guidance", None)]
        sources.extend(
            (
                f"Sheet: {sheet.name} / {entry.name}",
                entry.image_asset_id,
            )
            for sheet in self._session.project.character_sheets
            for entry in sheet.entries
            if entry.approval_state is ApprovalState.APPROVED
        )
        sources.extend(
            (
                f"Keyframe: {keyframe.time_ms / 1000:g}s / {keyframe.source_type.value}",
                keyframe.image_asset_id,
            )
            for keyframe in self._session.project.keyframes
            if keyframe.approved
        )
        return sources

    @Slot(int)
    def approveKeyframe(self, keyframe_index: int) -> None:  # noqa: N802
        if not 0 <= keyframe_index < len(self._session.project.keyframes):
            self._set_status("Select an existing keyframe")
            return
        keyframes = tuple(
            item.model_copy(update={"approved": True, "locked": True})
            if index == keyframe_index
            else item
            for index, item in enumerate(self._session.project.keyframes)
        )
        self._session.project = Wan2LabProject.model_validate(
            self._session.project.model_copy(update={"keyframes": keyframes}).model_dump()
        )
        self._set_status("Keyframe approved and locked for Wan planning")
        self.projectChanged.emit()

    @Slot(int)
    def fitKeyframeToCanvas(self, keyframe_index: int) -> None:  # noqa: N802
        try:
            source_keyframe = self._session.project.keyframes[keyframe_index]
            source_asset = next(
                item
                for item in self._session.project.assets
                if item.asset_id == source_keyframe.image_asset_id
            )
            settings = self._session.project.project_settings
            target_size = (settings.width, settings.height)
            if (source_asset.width, source_asset.height) == target_size:
                self._set_status("Keyframe already matches the project canvas")
                return
            source_path = self._asset_store.resolve_ref(source_asset)
            provenance_id = f"provenance-{uuid4().hex}"
            with tempfile.TemporaryDirectory(prefix="wan2lab-keyframe-fit-") as directory:
                output_path = Path(directory) / "canvas-fit.png"
                with Image.open(source_path) as source_image:
                    normalized = ImageOps.exif_transpose(source_image).convert("RGB")
                    fitted = ImageOps.pad(
                        normalized,
                        target_size,
                        method=Image.Resampling.LANCZOS,
                        color=(0, 0, 0),
                        centering=(0.5, 0.5),
                    )
                    fitted.save(output_path, format="PNG", optimize=False)
                record = self._asset_store.create_derived(
                    output_path,
                    parent_asset_ids=(source_asset.asset_id,),
                    media_type="image/png",
                    metadata={
                        "operation": "fit_keyframe_to_canvas",
                        "fit_mode": "contain_pad",
                        "pad_color": "#000000",
                    },
                )
            asset = self._wan_asset(record, AssetKind.IMAGE).model_copy(
                update={
                    "creation_operation_id": provenance_id,
                    "immutable_source": False,
                }
            )
            provenance = ProvenanceRecord(
                provenance_id=provenance_id,
                operation="fit_keyframe_to_canvas",
                created_at=datetime.now(UTC),
                parameters={
                    "source_dimensions": {
                        "width": source_asset.width,
                        "height": source_asset.height,
                    },
                    "target_dimensions": {
                        "width": settings.width,
                        "height": settings.height,
                    },
                    "fit_mode": "contain_pad",
                    "pad_color": "#000000",
                    "resampling": "lanczos",
                },
                input_asset_ids=(source_asset.asset_id,),
                output_asset_ids=(asset.asset_id,),
                parent_provenance_ids=(source_keyframe.provenance_id,),
            )
            revised_keyframe = source_keyframe.model_copy(
                update={
                    "keyframe_id": f"keyframe-{uuid4().hex}",
                    "image_asset_id": asset.asset_id,
                    "source_type": KeyframeSource.EDITED,
                    "provenance_id": provenance.provenance_id,
                    "approved": False,
                    "locked": False,
                    "parent_keyframe_id": source_keyframe.keyframe_id,
                    "source_frame_asset_id": source_asset.asset_id,
                }
            )
            self._session.project = revise_timeline_keyframe(
                self._session.project,
                source_keyframe_id=source_keyframe.keyframe_id,
                revised_keyframe=revised_keyframe,
                asset=asset,
                provenance=provenance,
            )
            self._session.segment_plan = None
        except Exception as error:
            self._set_status(f"Keyframe canvas fit failed: {error}")
            return
        self._append_event(
            f"Derived {settings.width}x{settings.height} keyframe; source asset preserved"
        )
        self._set_status("Canvas-fitted keyframe is a draft; approve and replan")
        self.projectChanged.emit()

    @Slot(int, float)
    def retimeKeyframe(self, keyframe_index: int, time_seconds: float) -> None:  # noqa: N802
        try:
            keyframe = self._session.project.keyframes[keyframe_index]
            self._session.project = retime_timeline_keyframe(
                self._session.project,
                keyframe_id=keyframe.keyframe_id,
                time_ms=round(time_seconds * 1000),
            )
            self._session.segment_plan = None
        except Exception as error:
            self._set_status(f"Keyframe retime failed: {error}")
            return
        self._append_event(
            f"Moved keyframe {keyframe.keyframe_id} to {time_seconds:g}s; asset preserved"
        )
        self._set_status("Keyframe timing updated; replan before further generation")
        self.projectChanged.emit()

    @Slot(int, int, int, float, float, float, float, str)
    def refineKeyframeFace(  # noqa: N802
        self,
        keyframe_index: int,
        identity_index: int,
        sheet_entry_index: int,
        x0: float,
        y0: float,
        x1: float,
        y1: float,
        prompt: str,
    ) -> None:
        if not self._krea_loaded:
            self._set_status("Load Krea before refining a keyframe face")
            return
        try:
            keyframe = self._session.project.keyframes[keyframe_index]
            identity = self._session.project.characters[identity_index]
            entries = [
                entry
                for sheet in self._session.project.character_sheets
                for entry in sheet.entries
            ]
            reference = entries[sheet_entry_index]
            if reference.identity_id != identity.identity_id:
                raise ValueError("selected sheet reference belongs to another identity")
            assets = {item.asset_id: item for item in self._session.project.assets}
            source_asset = assets[keyframe.image_asset_id]
            reference_asset = assets[reference.image_asset_id]
            region = Rectangle(x0=x0, y0=y0, x1=x1, y1=y1)
            adapter_refs = tuple(
                item for item in identity.adapter_refs if item.family.value == "krea"
            )
            request = NormalizedFrameEditRequest(
                source_frame_asset_id=source_asset.asset_id,
                operation_type=FrameEditOperation.FACE_REFINEMENT,
                prompt=", ".join(
                    item
                    for item in (identity.identity_prompt, prompt.strip())
                    if item
                ),
                settings={"reference_asset_id": reference_asset.asset_id},
                region=region,
                identity_id=identity.identity_id,
                adapters=tuple(
                    AdapterSelection(
                        adapter_id=item.adapter_id,
                        strength=item.default_strength,
                    )
                    for item in adapter_refs
                ),
                user_confirmed_face_region=True,
            )
            asset_paths = {
                source_asset.asset_id: str(self._asset_store.resolve_ref(source_asset)),
                "identity-reference": str(self._asset_store.resolve_ref(reference_asset)),
                **{
                    item.adapter_id: str(self._asset_store.resolve_ref(assets[item.asset_id]))
                    for item in adapter_refs
                },
            }
        except Exception as error:
            self._set_status(f"Keyframe face refinement could not start: {error}")
            return
        command_id = self._krea_worker.send(
            "refine_faces",
            {
                "request": request.to_k2_request(),
                "asset_paths": asset_paths,
            },
        )
        self._pending_krea_jobs[command_id] = {
            "operation": "keyframe_face_refinement",
            "source_keyframe_id": keyframe.keyframe_id,
            "request": request.model_dump(mode="json"),
            "input_asset_ids": (
                source_asset.asset_id,
                reference_asset.asset_id,
                *(item.asset_id for item in adapter_refs),
            ),
        }
        self._set_status("Refining the explicitly confirmed keyframe face region…")

    @Slot()
    def planMockTimeline(self) -> None:  # noqa: N802
        if self._active_wan_commands:
            self._set_status("Cancel the active Wan generation before replanning")
            return
        try:
            capabilities = self._inspected_capabilities or self._capabilities
            model_id = self._selected_wan_model_id or "wan-test"
            if self._inspected_capabilities is not None and self._selected_wan_model_id is None:
                raise ValueError("load an explicitly selected Wan model before planning")
            plan = self._session.plan(capabilities, model_id=model_id)
        except Exception as error:
            self._set_status(f"Plan failed: {error}")
            return
        self._append_event(f"Planned {len(plan.segments)} review-gated segment(s)")
        self._set_status("Timeline planned — first segment is ready to generate")
        self.projectChanged.emit()

    @Slot()
    def generateNextMockSegment(self) -> None:  # noqa: N802
        if self._session.project.segments and any(
            item.backend_id != self._backend.backend_id
            for item in self._session.project.segments
        ):
            self._queue_worker_generation(regenerate=False)
            return
        try:
            revision = self._session.generate_next_with_mock(
                self._backend,
                seed=len(self._session.project.segment_revisions) + 1,
                progress=lambda event: self._append_event(
                    f"{event.segment_id}: {event.stage} {event.current}/{event.total}"
                ),
            )
        except (ReviewGateBlocked, StopIteration, RuntimeError, ValueError) as error:
            self._set_status(str(error))
            return
        self._append_event(f"{revision.segment_id} revision {revision.revision_number} ready")
        self._set_status("Segment ready for review — approve before continuing")
        self.projectChanged.emit()

    @Slot()
    def approveCurrentSegment(self) -> None:  # noqa: N802
        try:
            revision = self._session.approve_current()
        except ReviewGateBlocked as error:
            self._set_status(str(error))
            return
        self._append_event(f"Approved {revision.segment_id} revision {revision.revision_number}")
        if self.approvedSegmentCount == self.segmentCount:
            self._set_status("All planned segments approved")
        else:
            self._set_status("Approved — next segment may be generated")
        self.projectChanged.emit()

    @Slot(str)
    def rejectCurrentSegment(self, reason: str) -> None:  # noqa: N802
        try:
            revision = self._session.reject_current(reason)
        except (ReviewGateBlocked, ValueError) as error:
            self._set_status(str(error))
            return
        self._append_event(
            f"Rejected {revision.segment_id} revision {revision.revision_number}: {reason.strip()}"
        )
        self._set_status("Revision preserved; regenerate the rejected segment when ready")
        self.projectChanged.emit()

    @Slot()
    def regenerateRejectedMockSegment(self) -> None:  # noqa: N802
        if any(
            item.state
            in {
                SegmentState.REJECTED,
                SegmentState.ERROR,
                SegmentState.CANCELLED,
                SegmentState.STALE,
            }
            and item.backend_id != self._backend.backend_id
            for item in self._session.project.segments
        ):
            self._queue_worker_generation(regenerate=True)
            return
        try:
            revision = self._session.regenerate_rejected_with_mock(
                self._backend,
                seed=len(self._session.project.segment_revisions) + 1,
                progress=lambda event: self._append_event(
                    f"{event.segment_id}: {event.stage} {event.current}/{event.total}"
                ),
            )
        except (ReviewGateBlocked, RuntimeError, ValueError) as error:
            self._set_status(str(error))
            return
        self._append_event(
            f"Regenerated {revision.segment_id} as immutable revision {revision.revision_number}"
        )
        self._set_status("Regenerated revision ready for mandatory review")
        self.projectChanged.emit()

    @Slot()
    def cancelGeneration(self) -> None:  # noqa: N802
        if not self._active_wan_jobs:
            self._set_status("No local Wan generation is active")
            return
        job_id = next(iter(self._active_wan_jobs))
        self._wan_worker.send(
            CancelRequest(
                command_id=f"cancel-{uuid4().hex}",
                job_id=job_id,
            )
        )
        self._set_status(f"Cancelling {job_id}…")

    def _queue_worker_generation(self, *, regenerate: bool) -> None:
        if self._selected_wan_model_id is None or self._inspected_capabilities is None:
            self._set_status("Inspect and load the planned Wan model before generation")
            return
        seed = len(self._session.project.segment_revisions) + 1
        revision = None
        try:
            if regenerate:
                job_id, revision = self._session.queue_rejected_generation(seed=seed)
            else:
                job_id, revision = self._session.queue_next_generation(seed=seed)
            request = revision.source_request
            asset_ids = {
                item
                for item in (
                    request.start_image_asset_id,
                    request.end_image_asset_id,
                    request.reference_character_asset_id,
                    request.driving_video_asset_id,
                    request.source_video_asset_id,
                    request.mask_asset_id,
                )
                if item is not None
            }
            assets = {item.asset_id: item for item in self._session.project.assets}
            missing = asset_ids - set(assets)
            if missing:
                raise ValueError(f"generation inputs are missing: {', '.join(sorted(missing))}")
            asset_inputs = {
                asset_id: self._comfy_assets.stage_input(self._asset_store, assets[asset_id])
                for asset_id in asset_ids
            }
            command_id = f"generate-{uuid4().hex}"
            command = GenerateSegmentRequest(
                command_id=command_id,
                job_id=job_id,
                request=request,
                seed=seed,
                asset_inputs=asset_inputs,
                output_prefix=(
                    f"wan2lab/{self._session.project.project_id}/"
                    f"{revision.segment_id}/{revision.revision_id}"
                ),
            )
        except Exception as error:
            if revision is not None:
                try:
                    self._session.fail_worker_generation(
                        revision_id=revision.revision_id,
                        message=str(error),
                    )
                except Exception:
                    pass
            self._set_status(f"Wan generation could not start: {error}")
            self.projectChanged.emit()
            return
        self._active_wan_commands[command_id] = revision.revision_id
        self._active_wan_jobs[job_id] = command_id
        self._wan_worker.send(command)
        self._append_event(
            f"Queued {revision.segment_id} revision {revision.revision_number} on local ComfyUI"
        )
        self._set_status("Local Wan generation started; review remains mandatory")
        self.projectChanged.emit()

    @Slot(int, int, QUrl, str, bool)
    def modifyFrame(  # noqa: N802
        self,
        segment_index: int,
        frame_index: int,
        replacement_url: QUrl,
        prompt: str,
        propagate_boundary: bool,
    ) -> None:
        if self._frame_runner.running:
            self._set_status("A frame modification is already running")
            return
        if not 0 <= segment_index < len(self._session.project.segments):
            self._set_status("Select an existing segment to modify")
            return
        segment = self._session.project.segments[segment_index]
        if segment.state is not SegmentState.READY_FOR_REVIEW or not segment.revision_ids:
            self._set_status("Only a reviewable segment revision can be modified")
            return
        revision = next(
            item
            for item in self._session.project.segment_revisions
            if item.revision_id == segment.revision_ids[-1]
        )
        if not 0 <= frame_index < revision.source_request.frame_count:
            self._set_status("Frame index is outside the selected revision")
            return
        if (
            propagate_boundary
            and frame_index not in {0, revision.source_request.frame_count - 1}
        ):
            self._set_status("Only the first or last frame can propagate as an anchor")
            return
        try:
            source_asset = next(
                item
                for item in self._session.project.assets
                if item.asset_id == revision.result_asset_id
            )
            source_video = self._asset_store.resolve_ref(source_asset)
            replacement_source = Path(replacement_url.toLocalFile()).expanduser().resolve()
            with Image.open(replacement_source) as replacement_image:
                if replacement_image.size != (
                    revision.source_request.width,
                    revision.source_request.height,
                ):
                    raise ValueError(
                        "replacement frame dimensions must match the generated segment"
                    )
            work = (
                self._asset_base
                / self._session.project.project_id
                / ".frame-work"
                / uuid4().hex
            )
            original_path = work / f"frame-{frame_index:08d}-original.png"
            staged_replacement = work / f"frame-{frame_index:08d}-replacement.png"
            revised_path = work / f"{revision.revision_id}-modified.mp4"
            extraction = plan_frame_extraction(
                ffmpeg_executable=self._session.project.project_settings.ffmpeg_executable,
                source_video_path=str(source_video),
                frame_index=frame_index,
                frame_count=revision.source_request.frame_count,
                output_path=str(original_path),
            )
            assembly = plan_frame_revision_assembly(
                ffmpeg_executable=self._session.project.project_settings.ffmpeg_executable,
                source_video_path=str(source_video),
                replacement_paths={frame_index: str(staged_replacement)},
                generation_fps=revision.source_request.generation_fps,
                frame_count=revision.source_request.frame_count,
                output_path=str(revised_path),
                work_directory=str(work / "sequence"),
            )
            self._active_frame_edit = {
                "segment_id": segment.segment_id,
                "revision_id": revision.revision_id,
                "source_video_asset_id": source_asset.asset_id,
                "frame_index": frame_index,
                "prompt": prompt.strip(),
                "propagate": propagate_boundary,
                "operation_type": FrameEditOperation.IMAGE_EDIT,
                "region": None,
                "user_confirmed_face_region": False,
            }
            self._frame_runner.start(
                extraction,
                assembly,
                replacement_source=replacement_source,
            )
        except Exception as error:
            self._active_frame_edit = None
            self._set_status(f"Frame modification could not start: {error}")
            return
        self._append_event(
            f"Started immutable frame {frame_index} modification for {segment.segment_id}"
        )
        self._set_status("Extracting and rebuilding the modified segment…")

    @Slot(int, int, str, float, float, float, float, bool, bool)
    def generateFrameEditWithKrea(  # noqa: N802
        self,
        segment_index: int,
        frame_index: int,
        prompt: str,
        x0: float,
        y0: float,
        x1: float,
        y1: float,
        face_refinement: bool,
        propagate_boundary: bool,
    ) -> None:
        if not self._krea_loaded:
            self._set_status("Load Krea before generating a frame replacement")
            return
        if self.frameModificationRunning:
            self._set_status("A frame modification is already running")
            return
        try:
            segment = self._session.project.segments[segment_index]
            if segment.state is not SegmentState.READY_FOR_REVIEW or not segment.revision_ids:
                raise ValueError("only a reviewable segment can be modified")
            revision = next(
                item
                for item in self._session.project.segment_revisions
                if item.revision_id == segment.revision_ids[-1]
            )
            if not 0 <= frame_index < revision.source_request.frame_count:
                raise ValueError("frame index is outside the selected revision")
            if (
                propagate_boundary
                and frame_index not in {0, revision.source_request.frame_count - 1}
            ):
                raise ValueError("only first or last frame edits can propagate")
            region = Rectangle(x0=x0, y0=y0, x1=x1, y1=y1) if face_refinement else None
            operation = (
                FrameEditOperation.FACE_REFINEMENT
                if face_refinement
                else FrameEditOperation.IMAGE_EDIT
            )
            request = NormalizedFrameEditRequest(
                source_frame_asset_id="krea-source-frame",
                operation_type=operation,
                prompt=prompt.strip(),
                region=region,
                user_confirmed_face_region=face_refinement,
            )
            source_asset = next(
                item
                for item in self._session.project.assets
                if item.asset_id == revision.result_asset_id
            )
            source_video = self._asset_store.resolve_ref(source_asset)
            work = (
                self._asset_base
                / self._session.project.project_id
                / ".frame-work"
                / uuid4().hex
            )
            extraction = plan_frame_extraction(
                ffmpeg_executable=self._session.project.project_settings.ffmpeg_executable,
                source_video_path=str(source_video),
                frame_index=frame_index,
                frame_count=revision.source_request.frame_count,
                output_path=str(work / "krea-source.png"),
            )
            self._pending_krea_frame_edit = {
                "segment_index": segment_index,
                "segment_id": segment.segment_id,
                "revision_id": revision.revision_id,
                "source_video_asset_id": source_asset.asset_id,
                "frame_index": frame_index,
                "prompt": prompt.strip(),
                "propagate": propagate_boundary,
                "operation_type": operation,
                "region": region,
                "user_confirmed_face_region": face_refinement,
                "request": request,
            }
            self._frame_extraction_runner.start(extraction)
        except Exception as error:
            self._pending_krea_frame_edit = None
            self._set_status(f"Krea frame edit could not start: {error}")
            return
        self._set_status("Extracting exact source frame for Krea…")

    @Slot(str)
    def _handle_krea_source_extracted(self, source_path: str) -> None:
        if self._pending_checkpoint_application is not None:
            self._complete_checkpoint_extraction(source_path)
        elif self._active_batch_frame_edit is not None:
            if self._active_batch_frame_edit.get("mode") == "face_detection":
                self._start_batch_face_detection(source_path)
            else:
                self._start_batch_krea_edit(source_path)
        else:
            self._start_krea_frame_edit(source_path)

    @Slot(str)
    def _handle_krea_source_extraction_failed(self, message: str) -> None:
        if self._pending_checkpoint_application is not None:
            self._pending_checkpoint_application = None
            self._append_event(message)
            self._set_status(message)
        elif self._active_batch_frame_edit is not None:
            self._fail_batch_frame_modification(message)
        else:
            self._fail_krea_frame_edit(message)

    def _start_krea_frame_edit(self, source_path: str) -> None:
        context = self._pending_krea_frame_edit
        if context is None:
            self._set_status("Krea source frame extracted without an active edit")
            return
        request = context["request"]
        command_kind = (
            "refine_faces"
            if context["operation_type"] is FrameEditOperation.FACE_REFINEMENT
            else "edit_image"
        )
        command_id = self._krea_worker.send(
            command_kind,
            {
                "request": request.to_k2_request(),
                "asset_paths": {"krea-source-frame": source_path},
            },
        )
        context["command_id"] = command_id
        self._pending_krea_jobs[command_id] = {
            "operation": "frame_edit_replacement",
            "frame_context": context,
            "request": request.model_dump(mode="json"),
            "input_asset_ids": (),
        }
        self._set_status("Krea is generating the immutable replacement frame…")

    @Slot(int, str, str, bool)
    def generateBatchFrameEditsWithKrea(  # noqa: N802
        self,
        segment_index: int,
        frame_indices: str,
        prompt: str,
        propagate_boundary: bool,
    ) -> None:
        if not self._krea_loaded:
            self._set_status("Load Krea before generating batch frame repairs")
            return
        if self.frameModificationRunning:
            self._set_status("A frame modification is already running")
            return
        try:
            values = tuple(
                sorted(
                    {
                        int(item.strip())
                        for item in frame_indices.split(",")
                        if item.strip()
                    }
                )
            )
            selection = BatchFrameSelection(frame_indices=values)
            segment = self._session.project.segments[segment_index]
            if segment.state is not SegmentState.READY_FOR_REVIEW or not segment.revision_ids:
                raise ValueError("only a reviewable segment can be batch modified")
            revision = next(
                item
                for item in self._session.project.segment_revisions
                if item.revision_id == segment.revision_ids[-1]
            )
            if any(item >= revision.source_request.frame_count for item in values):
                raise ValueError("a selected frame index is outside the revision")
            source_asset = next(
                item
                for item in self._session.project.assets
                if item.asset_id == revision.result_asset_id
            )
            source_video = self._asset_store.resolve_ref(source_asset)
        except Exception as error:
            self._set_status(f"Batch frame edit could not start: {error}")
            return
        self._active_batch_frame_edit = {
            "mode": "image_edit",
            "segment_id": segment.segment_id,
            "segment_index": segment_index,
            "revision_id": revision.revision_id,
            "source_video_asset_id": source_asset.asset_id,
            "source_video": source_video,
            "selection": selection,
            "pending_indices": list(selection.frame_indices),
            "replacement_paths": {},
            "prompt": prompt.strip(),
            "propagate": propagate_boundary,
            "work": (
                self._asset_base
                / self._session.project.project_id
                / ".frame-work"
                / uuid4().hex
            ),
        }
        self._start_next_batch_krea_edit()
        self._set_status(f"Starting Krea batch repair for {len(values)} frame(s)…")

    @Slot(int, str, int)
    def detectBatchFaces(  # noqa: N802
        self,
        segment_index: int,
        frame_indices: str,
        identity_index: int,
    ) -> None:
        if not self._krea_loaded:
            self._set_status("Load Krea before detecting batch faces")
            return
        if self.frameModificationRunning:
            self._set_status("A frame modification is already running")
            return
        try:
            values = tuple(
                sorted(
                    {
                        int(item.strip())
                        for item in frame_indices.split(",")
                        if item.strip()
                    }
                )
            )
            selection = BatchFrameSelection(frame_indices=values)
            identity = self._session.project.characters[identity_index]
            segment = self._session.project.segments[segment_index]
            if segment.state is not SegmentState.READY_FOR_REVIEW or not segment.revision_ids:
                raise ValueError("only a reviewable segment can be analyzed")
            revision = next(
                item
                for item in self._session.project.segment_revisions
                if item.revision_id == segment.revision_ids[-1]
            )
            if any(item >= revision.source_request.frame_count for item in values):
                raise ValueError("a selected frame index is outside the revision")
            source_asset = next(
                item
                for item in self._session.project.assets
                if item.asset_id == revision.result_asset_id
            )
            source_video = self._asset_store.resolve_ref(source_asset)
        except Exception as error:
            self._set_status(f"Batch face detection could not start: {error}")
            return
        self._face_batch_draft = None
        self._active_batch_frame_edit = {
            "mode": "face_detection",
            "segment_id": segment.segment_id,
            "segment_index": segment_index,
            "revision_id": revision.revision_id,
            "source_video_asset_id": source_asset.asset_id,
            "source_video": source_video,
            "selection": selection,
            "identity_id": identity.identity_id,
            "identity_prompt": identity.identity_prompt,
            "pending_indices": list(selection.frame_indices),
            "candidates": {},
            "work": (
                self._asset_base
                / self._session.project.project_id
                / ".frame-work"
                / uuid4().hex
            ),
        }
        self.projectChanged.emit()
        self._start_next_batch_krea_edit()
        self._set_status(f"Detecting faces in {len(values)} selected frame(s)…")

    def _start_next_batch_krea_edit(self) -> None:
        context = self._active_batch_frame_edit
        if context is None:
            return
        pending = context["pending_indices"]
        if not isinstance(pending, list):
            self._fail_batch_frame_modification("Invalid batch frame queue")
            return
        if not pending:
            if context.get("mode") == "face_detection":
                self._finish_batch_face_detection()
            else:
                self._start_batch_frame_assembly()
            return
        frame_index = int(pending.pop(0))
        context["current_index"] = frame_index
        revision = next(
            item
            for item in self._session.project.segment_revisions
            if item.revision_id == context["revision_id"]
        )
        extraction = plan_frame_extraction(
            ffmpeg_executable=self._session.project.project_settings.ffmpeg_executable,
            source_video_path=str(context["source_video"]),
            frame_index=frame_index,
            frame_count=revision.source_request.frame_count,
            output_path=str(Path(context["work"]) / f"krea-source-{frame_index:08d}.png"),
        )
        self._frame_extraction_runner.start(extraction)

    def _start_batch_face_detection(self, source_path: str) -> None:
        context = self._active_batch_frame_edit
        if context is None:
            return
        frame_index = int(context["current_index"])
        command_id = self._krea_worker.send(
            "detect_faces",
            {
                "request": {
                    "source_asset_id": "krea-source-frame",
                    "threshold": 0.4,
                    "provider": "auto",
                },
                "asset_paths": {"krea-source-frame": source_path},
            },
        )
        self._pending_krea_jobs[command_id] = {
            "operation": "batch_face_detection",
            "frame_index": frame_index,
        }
        self._set_status(f"Detecting candidate faces in frame {frame_index}…")

    def _finish_batch_face_detection(self) -> None:
        context = self._active_batch_frame_edit
        self._active_batch_frame_edit = None
        if context is None:
            return
        candidates = context["candidates"]
        candidate_order = [
            (proposal, candidate_index)
            for frame_index in context["selection"].frame_indices
            for candidate_index, proposal in enumerate(candidates.get(frame_index, ()))
        ]
        self._face_batch_draft = {
            **context,
            "candidate_order": candidate_order,
            "confirmed": {},
        }
        warnings = []
        for frame_index in context["selection"].frame_indices:
            frame_candidates = candidates.get(frame_index, ())
            if not frame_candidates:
                warning_kind = IdentityWarningKind.MISSING
                score = None
                message = "No candidate face detected; manual association is required"
                proposed_region = None
            elif len(frame_candidates) > 1:
                warning_kind = IdentityWarningKind.UNCERTAIN
                score = max(item.score for item in frame_candidates)
                message = "Multiple faces require an explicit character association"
                proposed_region = None
            elif frame_candidates[0].score < 0.65:
                warning_kind = IdentityWarningKind.UNCERTAIN
                score = frame_candidates[0].score
                message = "Low-confidence face detection requires review"
                proposed_region = frame_candidates[0].box
            else:
                continue
            warnings.append(
                IdentityDriftWarning(
                    warning_id=f"identity-warning-{uuid4().hex}",
                    segment_revision_id=str(context["revision_id"]),
                    frame_index=frame_index,
                    identity_id=str(context["identity_id"]),
                    kind=warning_kind,
                    score=score,
                    message=message,
                    proposed_region=proposed_region,
                )
            )
        if warnings:
            segment = next(
                item
                for item in self._session.project.segments
                if item.segment_id == context["segment_id"]
            )
            revision = next(
                item
                for item in self._session.project.segment_revisions
                if item.revision_id == context["revision_id"]
            )
            proposal = propose_checkpoint_from_warnings(
                proposal_id=f"checkpoint-proposal-{uuid4().hex}",
                segment_id=segment.segment_id,
                segment_start_ms=segment.start_ms,
                segment_end_ms=segment.end_ms,
                generation_fps=revision.source_request.generation_fps,
                warnings=tuple(warnings),
            )
            self._session.project = register_identity_analysis(
                self._session.project,
                warnings=tuple(warnings),
                proposal=proposal,
            )
        missing = sum(
            not candidates.get(index) for index in context["selection"].frame_indices
        )
        self._append_event(
            f"Face detection proposed {len(candidate_order)} candidate(s) across "
            f"{len(context['selection'].frame_indices)} frame(s)"
        )
        self._set_status(
            "Confirm one face per frame or draw a manual region"
            + (f"; {missing} frame(s) need a manual region" if missing else "")
            + (
                f"; {len(warnings)} identity warning(s) need review"
                if warnings
                else ""
            )
        )
        self.projectChanged.emit()

    @Slot(int)
    def confirmDetectedBatchFace(self, proposal_index: int) -> None:  # noqa: N802
        draft = self._face_batch_draft
        try:
            if draft is None:
                raise ValueError("run batch face detection first")
            proposal, _candidate_index = draft["candidate_order"][proposal_index]
            confirmed = confirm_face_proposal(proposal)
            draft["confirmed"][proposal.frame_index] = confirmed
            self._session.project = confirm_warning_association(
                self._session.project,
                segment_revision_id=str(draft["revision_id"]),
                identity_id=str(draft["identity_id"]),
                frame_index=proposal.frame_index,
                region=confirmed.box,
            )
        except Exception as error:
            self._set_status(f"Face confirmation failed: {error}")
            return
        self._set_status(f"Confirmed detected face for frame {proposal.frame_index}")
        self.projectChanged.emit()

    @Slot(int, float, float, float, float)
    def confirmManualBatchFace(  # noqa: N802
        self,
        frame_index: int,
        x0: float,
        y0: float,
        x1: float,
        y1: float,
    ) -> None:
        draft = self._face_batch_draft
        try:
            if draft is None:
                raise ValueError("run batch face detection first")
            if frame_index not in draft["selection"].frame_indices:
                raise ValueError("manual region frame is outside the analyzed selection")
            manual_box = Rectangle(x0=x0, y0=y0, x1=x1, y1=y1)
            proposal = FaceProposal(
                proposal_id=f"manual-face-{frame_index}-{uuid4().hex}",
                frame_index=frame_index,
                identity_id=str(draft["identity_id"]),
                region_id=f"manual-face-{frame_index}",
                box=manual_box,
                score=0.0,
                prompt=str(draft["identity_prompt"]),
            )
            confirmed = confirm_face_proposal(
                proposal,
                manual_box=manual_box,
            )
            draft["confirmed"][frame_index] = confirmed
            self._session.project = confirm_warning_association(
                self._session.project,
                segment_revision_id=str(draft["revision_id"]),
                identity_id=str(draft["identity_id"]),
                frame_index=frame_index,
                region=confirmed.box,
            )
        except Exception as error:
            self._set_status(f"Manual face confirmation failed: {error}")
            return
        self._set_status(f"Confirmed manual face region for frame {frame_index}")
        self.projectChanged.emit()

    @Slot(str, int, bool)
    def refineConfirmedFaceBatch(  # noqa: N802
        self,
        prompt: str,
        sheet_entry_index: int,
        propagate_boundary: bool,
    ) -> None:
        draft = self._face_batch_draft
        try:
            if draft is None or not self.faceBatchReady:
                raise ValueError("confirm exactly one face region for every selected frame")
            entries = [
                entry
                for sheet in self._session.project.character_sheets
                for entry in sheet.entries
            ]
            reference = entries[sheet_entry_index]
            if reference.identity_id != draft["identity_id"]:
                raise ValueError("selected sheet entry belongs to another identity")
            reference_asset = next(
                item
                for item in self._session.project.assets
                if item.asset_id == reference.image_asset_id
            )
            identity = next(
                item
                for item in self._session.project.characters
                if item.identity_id == draft["identity_id"]
            )
            adapter_refs = tuple(
                item for item in identity.adapter_refs if item.family.value == "krea"
            )
            project_assets = {item.asset_id: item for item in self._session.project.assets}
            adapter_paths = {
                item.adapter_id: str(self._asset_store.resolve_ref(project_assets[item.asset_id]))
                for item in adapter_refs
            }
        except Exception as error:
            self._set_status(f"Batch face refinement could not start: {error}")
            return
        combined_prompt = ", ".join(
            item for item in (str(draft["identity_prompt"]), prompt.strip()) if item
        )
        self._active_batch_frame_edit = {
            **draft,
            "mode": "face_refinement",
            "prompt": combined_prompt,
            "pending_indices": list(draft["selection"].frame_indices),
            "replacement_paths": {},
            "regions": dict(draft["confirmed"]),
            "reference_asset_id": reference_asset.asset_id,
            "reference_path": str(self._asset_store.resolve_ref(reference_asset)),
            "adapters": tuple(
                AdapterSelection(
                    adapter_id=item.adapter_id,
                    strength=item.default_strength,
                )
                for item in adapter_refs
            ),
            "adapter_paths": adapter_paths,
            "adapter_asset_ids": tuple(item.asset_id for item in adapter_refs),
            "propagate": propagate_boundary,
            "work": (
                self._asset_base
                / self._session.project.project_id
                / ".frame-work"
                / uuid4().hex
            ),
        }
        self._start_next_batch_krea_edit()
        self._set_status("Starting confirmed batch identity refinement…")

    @Slot(int)
    def approveIdentityCheckpoint(self, proposal_index: int) -> None:  # noqa: N802
        try:
            proposal = self._session.project.checkpoint_proposals[proposal_index]
            self._session.project = approve_registered_checkpoint(
                self._session.project,
                proposal.proposal_id,
            )
        except Exception as error:
            self._set_status(f"Checkpoint approval failed: {error}")
            return
        self._append_event(f"Approved identity checkpoint at {proposal.time_ms / 1000:g}s")
        self._set_status("Checkpoint approved; apply it explicitly to replan the timeline")
        self.projectChanged.emit()

    @Slot(int)
    def applyIdentityCheckpoint(self, proposal_index: int) -> None:  # noqa: N802
        if self.frameModificationRunning:
            self._set_status("Finish the active frame operation before applying a checkpoint")
            return
        try:
            proposal = self._session.project.checkpoint_proposals[proposal_index]
            if not proposal.user_approved:
                raise ValueError("approve the checkpoint before applying it")
            warnings = tuple(
                item
                for item in self._session.project.identity_warnings
                if item.warning_id in proposal.warning_ids
            )
            if not warnings:
                raise ValueError("checkpoint has no registered warning frames")
            revision_id = warnings[0].segment_revision_id
            revision = next(
                item
                for item in self._session.project.segment_revisions
                if item.revision_id == revision_id
            )
            segment = next(
                item
                for item in self._session.project.segments
                if item.segment_id == proposal.segment_id
            )
            frame_index = min(
                revision.source_request.frame_count - 1,
                max(
                    0,
                    round(
                        (proposal.time_ms - segment.start_ms)
                        * revision.source_request.generation_fps
                        / 1000
                    ),
                ),
            )
            source_asset = next(
                item
                for item in self._session.project.assets
                if item.asset_id == revision.result_asset_id
            )
            source_video = self._asset_store.resolve_ref(source_asset)
            output = (
                self._asset_base
                / self._session.project.project_id
                / ".frame-work"
                / uuid4().hex
                / f"checkpoint-{frame_index:08d}.png"
            )
            extraction = plan_frame_extraction(
                ffmpeg_executable=self._session.project.project_settings.ffmpeg_executable,
                source_video_path=str(source_video),
                frame_index=frame_index,
                frame_count=revision.source_request.frame_count,
                output_path=str(output),
            )
        except Exception as error:
            self._set_status(f"Checkpoint application could not start: {error}")
            return
        self._pending_checkpoint_application = {
            "proposal_id": proposal.proposal_id,
            "source_video_asset_id": source_asset.asset_id,
            "frame_index": frame_index,
        }
        self._frame_extraction_runner.start(extraction)
        self._set_status("Extracting the exact user-approved checkpoint frame…")

    def _complete_checkpoint_extraction(self, source_path: str) -> None:
        context = self._pending_checkpoint_application
        self._pending_checkpoint_application = None
        if context is None:
            return
        try:
            record = self._asset_store.register_generated(
                Path(source_path),
                media_type="image/png",
                parent_asset_ids=(str(context["source_video_asset_id"]),),
                metadata={
                    "operation": "extract_identity_checkpoint",
                    "frame_index": int(context["frame_index"]),
                },
            )
            extraction_provenance_id = f"provenance-{uuid4().hex}"
            checkpoint_asset = self._wan_asset(record, AssetKind.IMAGE).model_copy(
                update={
                    "creation_operation_id": extraction_provenance_id,
                    "immutable_source": False,
                }
            )
            extraction_provenance = ProvenanceRecord(
                provenance_id=extraction_provenance_id,
                operation="extract_identity_checkpoint",
                created_at=datetime.now(UTC),
                input_asset_ids=(str(context["source_video_asset_id"]),),
                output_asset_ids=(checkpoint_asset.asset_id,),
                parameters={"frame_index": int(context["frame_index"])},
                runtime={
                    "ffmpeg": self._session.project.project_settings.ffmpeg_executable
                },
            )
            project_with_asset = Wan2LabProject.model_validate(
                self._session.project.model_copy(
                    update={
                        "assets": (*self._session.project.assets, checkpoint_asset),
                        "generation_records": (
                            *self._session.project.generation_records,
                            extraction_provenance,
                        ),
                    }
                ).model_dump()
            )
            link_provenance = ProvenanceRecord(
                provenance_id=f"provenance-{uuid4().hex}",
                operation="link_identity_checkpoint",
                created_at=datetime.now(UTC),
                input_asset_ids=(checkpoint_asset.asset_id,),
                parameters={"user_approved": True},
            )
            self._session.project = apply_approved_checkpoint(
                project_with_asset,
                proposal_id=str(context["proposal_id"]),
                keyframe_id=f"identity-checkpoint-{uuid4().hex}",
                source_frame_asset_id=checkpoint_asset.asset_id,
                provenance=link_provenance,
            )
            self._session.segment_plan = None
        except Exception as error:
            self._set_status(f"Checkpoint application failed: {error}")
            return
        self._append_event("Applied approved identity checkpoint and marked work for replanning")
        self._set_status("Identity checkpoint added; stale segments require user-controlled replanning")
        self.projectChanged.emit()

    def _start_batch_krea_edit(self, source_path: str) -> None:
        context = self._active_batch_frame_edit
        if context is None:
            return
        frame_index = int(context["current_index"])
        is_face_refinement = context.get("mode") == "face_refinement"
        proposal = (
            context["regions"][frame_index] if is_face_refinement else None
        )
        request = NormalizedFrameEditRequest(
            source_frame_asset_id="krea-source-frame",
            operation_type=(
                FrameEditOperation.FACE_REFINEMENT
                if is_face_refinement
                else FrameEditOperation.IMAGE_EDIT
            ),
            prompt=str(context["prompt"]),
            settings=(
                {"reference_asset_id": str(context["reference_asset_id"])}
                if is_face_refinement
                else {}
            ),
            region=proposal.box if proposal is not None else None,
            identity_id=(str(context["identity_id"]) if is_face_refinement else None),
            adapters=(context["adapters"] if is_face_refinement else ()),
            user_confirmed_face_region=is_face_refinement,
        )
        command_id = self._krea_worker.send(
            "refine_faces" if is_face_refinement else "edit_image",
            {
                "request": request.to_k2_request(),
                "asset_paths": {
                    "krea-source-frame": source_path,
                    **(
                        {
                            "identity-reference": str(context["reference_path"]),
                            **context["adapter_paths"],
                        }
                        if is_face_refinement
                        else {}
                    ),
                },
            },
        )
        self._pending_krea_jobs[command_id] = {
            "operation": "batch_frame_edit_replacement",
            "frame_index": frame_index,
            "request": request.model_dump(mode="json"),
        }
        self._set_status(
            f"Krea {'identity refinement' if is_face_refinement else 'batch repair'}: "
            f"frame {frame_index}"
        )

    def _start_batch_frame_assembly(self) -> None:
        context = self._active_batch_frame_edit
        if context is None:
            return
        try:
            revision = next(
                item
                for item in self._session.project.segment_revisions
                if item.revision_id == context["revision_id"]
            )
            selection = context["selection"]
            replacements = context["replacement_paths"]
            work = Path(context["work"])
            extraction_plans = tuple(
                plan_frame_extraction(
                    ffmpeg_executable=self._session.project.project_settings.ffmpeg_executable,
                    source_video_path=str(context["source_video"]),
                    frame_index=index,
                    frame_count=revision.source_request.frame_count,
                    output_path=str(work / f"original-{index:08d}.png"),
                )
                for index in selection.frame_indices
            )
            staged_paths = {
                index: str(work / f"replacement-{index:08d}.png")
                for index in selection.frame_indices
            }
            assembly = plan_frame_revision_assembly(
                ffmpeg_executable=self._session.project.project_settings.ffmpeg_executable,
                source_video_path=str(context["source_video"]),
                replacement_paths=staged_paths,
                generation_fps=revision.source_request.generation_fps,
                frame_count=revision.source_request.frame_count,
                output_path=str(work / f"{revision.revision_id}-batch-modified.mp4"),
                work_directory=str(work / "sequence"),
            )
            self._batch_frame_runner.start(
                extraction_plans,
                assembly,
                replacement_sources=tuple(
                    Path(replacements[index]) for index in selection.frame_indices
                ),
            )
        except Exception as error:
            self._fail_batch_frame_modification(f"Batch assembly could not start: {error}")

    @Slot(str)
    def _fail_krea_frame_edit(self, message: str) -> None:
        self._pending_krea_frame_edit = None
        self._append_event(message)
        self._set_status(message)

    @Slot()
    def cancelFrameModification(self) -> None:  # noqa: N802
        if self._frame_extraction_runner.running:
            self._frame_extraction_runner.cancel()
        elif self._pending_krea_frame_edit is not None:
            command_id = self._pending_krea_frame_edit.get("command_id")
            if command_id is not None:
                self._krea_worker.send("cancel", {"command_id": str(command_id)})
                self._set_status("Cancelling Krea frame edit…")
        elif self._active_batch_frame_edit is not None:
            active_command = next(
                (
                    command_id
                    for command_id, context in self._pending_krea_jobs.items()
                    if context.get("operation")
                    in {"batch_frame_edit_replacement", "batch_face_detection"}
                ),
                None,
            )
            if active_command is not None:
                self._krea_worker.send("cancel", {"command_id": active_command})
                self._set_status("Cancelling Krea batch frame edit…")
            else:
                self._batch_frame_runner.cancel()
        else:
            self._frame_runner.cancel()

    @Slot(str)
    def saveProject(self, path: str) -> None:  # noqa: N802
        project_path = Path(path).expanduser().resolve()
        try:
            physical_assets = tuple(
                asset
                for asset in self._session.project.assets
                if asset.storage_path.startswith("objects/")
            )
            target_store = self._asset_store.copy_to(
                project_path.parent / self._session.project.project_settings.asset_root,
                physical_assets,
            )
            save_project(self._session.project, project_path)
        except Exception as error:
            self._set_status(f"Save failed: {error}")
            return
        self._asset_store = target_store
        self._project_name = project_path.stem
        self._set_status(f"Saved {project_path}")
        self.projectChanged.emit()

    @Slot(QUrl)
    def saveProjectFile(self, url: QUrl) -> None:  # noqa: N802
        path = Path(url.toLocalFile()).expanduser()
        if not path.suffix:
            path = path.with_suffix(".wan2lab.json")
        self.saveProject(str(path))

    @Slot(QUrl)
    def openProjectFile(self, url: QUrl) -> None:  # noqa: N802
        self.openProject(str(Path(url.toLocalFile()).expanduser()))

    @Slot(float)
    def setOutputFps(self, output_fps: float) -> None:  # noqa: N802
        try:
            self._session.project = Wan2LabProject.model_validate(
                change_output_fps(self._session.project, output_fps).model_dump()
            )
        except Exception as error:
            self._set_status(f"Output FPS change failed: {error}")
            return
        self._append_event(f"Set duration-preserving output FPS to {output_fps:g}")
        self._set_status("Output FPS updated; generation timing is unchanged")
        self.projectChanged.emit()

    @Slot(int, int, int, str, str, str, str, str)
    def updateProjectSettings(  # noqa: N802
        self,
        width: int,
        height: int,
        segment_budget_ms: int,
        krea_backend_id: str,
        krea_model_id: str,
        memory_policy: str,
        continuation_policy: str,
        ffmpeg_executable: str,
    ) -> None:
        try:
            current = self._session.project.project_settings
            canvas_changed = width != current.width or height != current.height
            if canvas_changed and (
                self._session.project.assets
                or self._session.project.keyframes
                or self._session.project.mannequin_scenes
            ):
                raise ValueError(
                    "canvas size cannot change after visual assets exist; start a new project"
                )
            selected_continuation = ContinuationPolicy(continuation_policy)
            settings = current.model_copy(
                update={
                    "width": width,
                    "height": height,
                    "default_segment_duration_ms": segment_budget_ms,
                    "default_krea_backend_id": krea_backend_id.strip(),
                    "default_krea_model_id": krea_model_id.strip(),
                    "memory_policy": memory_policy.strip(),
                    "default_continuation_policy": selected_continuation,
                    "ffmpeg_executable": ffmpeg_executable.strip(),
                }
            )
            plan_changed = (
                canvas_changed
                or segment_budget_ms != current.default_segment_duration_ms
                or selected_continuation is not current.default_continuation_policy
            )
            updated = Wan2LabProject.model_validate(
                self._session.project.model_copy(
                    update={
                        "project_settings": settings,
                        "segment_plan": (
                            None if plan_changed else self._session.project.segment_plan
                        ),
                    }
                ).model_dump()
            )
            if plan_changed:
                generated_ids = tuple(
                    item.segment_id for item in updated.segments if item.revision_ids
                )
                updated = Wan2LabProject.model_validate(
                    invalidate_segments(
                        updated,
                        generated_ids,
                        reason="project generation settings changed and require replanning",
                    ).model_dump()
                )
                self._session.segment_plan = None
            self._session.project = updated
        except Exception as error:
            self._set_status(f"Project settings update failed: {error}")
            return
        self._append_event("Updated validated project, runtime, and export settings")
        self._set_status(
            "Project settings updated"
            + ("; replan before generation" if plan_changed else "")
        )
        self.projectChanged.emit()

    @Slot(int, str, str, str)
    def updateSegmentInspector(
        self,
        segment_index: int,
        mode: str,
        prompt: str,
        negative_prompt: str,
    ) -> None:  # noqa: N802
        if not 0 <= segment_index < len(self._session.project.segments):
            self._set_status("Select an existing planned segment")
            return
        try:
            selected_mode = WanMode(mode)
            current = self._session.project.segments[segment_index]
            capabilities = self._inspected_capabilities or self._capabilities
            model = capabilities.model(current.model_id)
            if selected_mode not in model.supported_modes:
                raise ValueError(f"Mode {selected_mode.value} is not supported by the segment model")
            self._validate_character_limit(
                model,
                selected_mode,
                current.character_identity_ids,
            )
            segment = current.model_copy(
                update={
                    "mode": selected_mode,
                    "prompt": prompt,
                    "negative_prompt": negative_prompt,
                }
            )
            segments = tuple(
                segment if index == segment_index else item
                for index, item in enumerate(self._session.project.segments)
            )
            updated = self._sync_segment_plan(
                self._session.project.model_copy(update={"segments": segments}),
                segment,
            )
            self._session.project = self._invalidate_generated_segment(
                updated,
                segment.segment_id,
                "segment prompt or mode changed",
            )
        except Exception as error:
            self._set_status(f"Segment update failed: {error}")
            return
        self._append_event(f"Updated segment {segment.segment_id} inspector settings")
        self._set_status("Segment prompt and mode updated")
        self.projectChanged.emit()

    @Slot(int, str, str, str, str, str, str, str, float)
    def setSegmentAction(  # noqa: N802
        self,
        segment_index: int,
        motion_instruction: str,
        starting_pose_ref: str,
        ending_pose_ref: str,
        character_trajectory: str,
        camera_trajectory: str,
        contact_constraints: str,
        speed_easing: str,
        pose_accuracy_preference: float,
    ) -> None:
        try:
            segment = self._session.project.segments[segment_index]
            action_id = segment.action_spec_id or f"action-{uuid4().hex}"
            existing = next(
                (
                    item
                    for item in self._session.project.actions
                    if item.action_id == action_id
                ),
                None,
            )
            action = ActionSpec(
                action_id=action_id,
                motion_instruction=motion_instruction.strip(),
                starting_pose_ref=starting_pose_ref.strip() or None,
                ending_pose_ref=ending_pose_ref.strip() or None,
                character_trajectory=character_trajectory.strip(),
                camera_trajectory=camera_trajectory.strip(),
                contact_constraints=tuple(
                    item.strip()
                    for item in contact_constraints.split(",")
                    if item.strip()
                ),
                speed_easing=speed_easing.strip(),
                driving_video_asset_id=(
                    segment.driving_video_asset_id
                    or segment.source_video_asset_id
                    or (existing.driving_video_asset_id if existing is not None else None)
                ),
                pose_accuracy_preference=pose_accuracy_preference,
            )
            actions = tuple(
                action if item.action_id == action_id else item
                for item in self._session.project.actions
            )
            if existing is None:
                actions = (*actions, action)
            segments = tuple(
                segment.model_copy(update={"action_spec_id": action.action_id})
                if index == segment_index
                else item
                for index, item in enumerate(self._session.project.segments)
            )
            updated = Wan2LabProject.model_validate(
                self._session.project.model_copy(
                    update={"actions": actions, "segments": segments}
                ).model_dump()
            )
            self._session.project = self._invalidate_generated_segment(
                updated,
                segment.segment_id,
                "structured action changed",
            )
        except Exception as error:
            self._set_status(f"Action update failed: {error}")
            return
        self._append_event(f"Updated structured action for {segment.segment_id}")
        self._set_status("Structured action saved; supported controls will bind at generation")
        self.projectChanged.emit()

    @Slot(int, int, bool)
    def setSegmentCharacterAssignment(  # noqa: N802
        self,
        segment_index: int,
        identity_index: int,
        assigned: bool,
    ) -> None:
        try:
            segment = self._session.project.segments[segment_index]
            identity = self._session.project.characters[identity_index]
            selected = list(segment.character_identity_ids)
            if assigned and identity.identity_id not in selected:
                selected.append(identity.identity_id)
            elif not assigned and identity.identity_id in selected:
                selected.remove(identity.identity_id)
            capabilities = self._inspected_capabilities or self._capabilities
            model = capabilities.model(segment.model_id)
            self._validate_character_limit(model, segment.mode, tuple(selected))
            revised = segment.model_copy(
                update={"character_identity_ids": tuple(selected)}
            )
            segments = tuple(
                revised if index == segment_index else item
                for index, item in enumerate(self._session.project.segments)
            )
            updated = Wan2LabProject.model_validate(
                self._session.project.model_copy(update={"segments": segments}).model_dump()
            )
            self._session.project = self._invalidate_generated_segment(
                updated,
                segment.segment_id,
                "segment character assignments changed",
            )
        except Exception as error:
            self._set_status(f"Character assignment failed: {error}")
            return
        self._set_status(
            f"{identity.name} {'assigned to' if assigned else 'removed from'} "
            f"{segment.segment_id}"
        )
        self.projectChanged.emit()

    @Slot(int, str)
    def setSegmentContinuationPolicy(  # noqa: N802
        self,
        segment_index: int,
        policy: str,
    ) -> None:
        try:
            selected = ContinuationPolicy(policy)
            segments = tuple(
                item.model_copy(update={"continuation_policy": selected})
                if index == segment_index
                else item
                for index, item in enumerate(self._session.project.segments)
            )
            updated = self._sync_segment_plan(
                self._session.project.model_copy(update={"segments": segments}),
                segments[segment_index],
            )
            self._session.project = self._invalidate_generated_segment(
                updated,
                segments[segment_index].segment_id,
                "continuation policy changed",
            )
        except Exception as error:
            self._set_status(f"Continuation policy update failed: {error}")
            return
        self._set_status(f"Continuation policy set to {selected.value}")
        self.projectChanged.emit()

    @Slot(int, str, QUrl)
    def importSegmentAsset(  # noqa: N802
        self,
        segment_index: int,
        role: str,
        url: QUrl,
    ) -> None:
        fields = {
            "start_image": ("start_image_asset_id", AssetKind.IMAGE),
            "end_image": ("end_image_asset_id", AssetKind.IMAGE),
            "reference_character": ("reference_character_asset_id", AssetKind.IMAGE),
            "driving_video": ("driving_video_asset_id", AssetKind.VIDEO),
            "source_video": ("source_video_asset_id", AssetKind.VIDEO),
            "mask": ("mask_asset_id", AssetKind.IMAGE),
        }
        try:
            field, kind = fields[role]
            source = Path(url.toLocalFile()).expanduser().resolve()
            if kind is AssetKind.IMAGE:
                media_type = image_media_type(source)
            else:
                media_type = mimetypes.guess_type(source.name)[0] or "video/mp4"
                if not media_type.startswith("video/"):
                    raise ValueError("selected mode input is not a recognized video")
            record = self._asset_store.register_imported(
                source,
                media_type=media_type,
                metadata={"operation": "import_segment_input", "role": role},
            )
            provenance_id = f"provenance-{uuid4().hex}"
            asset = self._wan_asset(record, kind).model_copy(
                update={"creation_operation_id": provenance_id}
            )
            provenance = ProvenanceRecord(
                provenance_id=provenance_id,
                operation="import_segment_input",
                created_at=datetime.now(UTC),
                output_asset_ids=(asset.asset_id,),
                parameters={"segment_index": segment_index, "role": role},
            )
            segments = tuple(
                item.model_copy(update={field: asset.asset_id})
                if index == segment_index
                else item
                for index, item in enumerate(self._session.project.segments)
            )
            actions = self._session.project.actions
            action_id = segments[segment_index].action_spec_id
            if role in {"driving_video", "source_video"} and action_id is not None:
                actions = tuple(
                    action.model_copy(update={"driving_video_asset_id": asset.asset_id})
                    if action.action_id == action_id
                    else action
                    for action in actions
                )
            updated = Wan2LabProject.model_validate(
                self._session.project.model_copy(
                    update={
                        "assets": (*self._session.project.assets, asset),
                        "generation_records": (
                            *self._session.project.generation_records,
                            provenance,
                        ),
                        "actions": actions,
                        "segments": segments,
                    }
                ).model_dump()
            )
            self._session.project = self._invalidate_generated_segment(
                updated,
                segments[segment_index].segment_id,
                f"segment {role} input changed",
            )
        except Exception as error:
            self._set_status(f"Segment input import failed: {error}")
            return
        self._append_event(f"Imported immutable {role} input for segment {segment_index}")
        self._set_status("Segment mode input saved; original assets remain immutable")
        self.projectChanged.emit()

    @staticmethod
    def _invalidate_generated_segment(
        project: Wan2LabProject,
        segment_id: str,
        reason: str,
    ) -> Wan2LabProject:
        segment = next(item for item in project.segments if item.segment_id == segment_id)
        if not segment.revision_ids:
            return project
        return Wan2LabProject.model_validate(
            invalidate_segments(project, (segment_id,), reason=reason).model_dump()
        )

    @Slot(int, str, str)
    def setSegmentBackendParameter(
        self,
        segment_index: int,
        key: str,
        value: str,
    ) -> None:  # noqa: N802
        if not 0 <= segment_index < len(self._session.project.segments):
            self._set_status("Select an existing planned segment")
            return
        descriptor = next(
            (item for item in self._backend_parameter_descriptors if item.get("key") == key),
            None,
        )
        if descriptor is None:
            self._set_status(f"Unknown backend parameter: {key}")
            return
        try:
            parsed = self._parse_parameter_value(descriptor, value)
            current = self._session.project.segments[segment_index]
            segment = current.model_copy(
                update={"parameters": {**current.parameters, key: parsed}}
            )
            segments = tuple(
                segment if index == segment_index else item
                for index, item in enumerate(self._session.project.segments)
            )
            updated = Wan2LabProject.model_validate(
                self._session.project.model_copy(update={"segments": segments}).model_dump()
            )
            self._session.project = self._invalidate_generated_segment(
                updated,
                segment.segment_id,
                f"Wan generation parameter {key} changed",
            )
        except Exception as error:
            self._set_status(f"Parameter update failed: {error}")
            return
        self._set_status(f"Set {key}={parsed}")
        self.projectChanged.emit()

    @Slot(int, float, str)
    def setSegmentTemporalSettings(  # noqa: N802
        self,
        segment_index: int,
        generation_fps: float,
        frame_rounding: str,
    ) -> None:
        try:
            current = self._session.project.segments[segment_index]
            capabilities = self._inspected_capabilities or self._capabilities
            model = capabilities.model(current.model_id)
            if generation_fps not in model.supported_generation_fps:
                raise ValueError(
                    f"generation FPS {generation_fps:g} is not supported by {model.display_name}"
                )
            rounding = FrameRounding(frame_rounding)
            frame_count = model.resolve_frame_count(
                current.end_ms - current.start_ms,
                generation_fps,
                rounding,
            )
            segment = current.model_copy(
                update={
                    "generation_fps": generation_fps,
                    "frame_count": frame_count,
                    "frame_rounding": rounding,
                }
            )
            segments = tuple(
                segment if index == segment_index else item
                for index, item in enumerate(self._session.project.segments)
            )
            updated = self._sync_segment_plan(
                self._session.project.model_copy(update={"segments": segments}),
                segment,
            )
            self._session.project = self._invalidate_generated_segment(
                updated,
                segment.segment_id,
                "generation FPS or frame-count rounding changed",
            )
            actual_duration = model.frame_duration_ms(frame_count, generation_fps)
        except Exception as error:
            self._set_status(f"Temporal setting update failed: {error}")
            return
        self._set_status(
            f"Generation timing set to {generation_fps:g} fps / {frame_count} frames "
            f"({actual_duration / 1000:g}s actual)"
        )
        self.projectChanged.emit()

    @staticmethod
    def _validate_character_limit(model, mode: WanMode, identity_ids: tuple[str, ...]) -> None:
        limits = tuple(
            item.maximum_reference_characters
            for item in model.adapter_compatibility
            if item.mode is mode and item.maximum_reference_characters is not None
        )
        if limits and len(identity_ids) > min(limits):
            maximum = min(limits)
            raise ValueError(
                f"{model.display_name} supports at most {maximum} reference "
                f"character{'s' if maximum != 1 else ''} in {mode.value} mode"
            )

    def _sync_segment_plan(
        self,
        project: Wan2LabProject,
        segment,
    ) -> Wan2LabProject:
        plan = project.segment_plan
        if plan is None:
            return Wan2LabProject.model_validate(project.model_dump())
        capabilities = self._inspected_capabilities or self._capabilities
        model = capabilities.model(segment.model_id)
        generation_fps = segment.generation_fps or model.default_generation_fps
        frame_count = segment.frame_count or model.resolve_frame_count(
            segment.end_ms - segment.start_ms,
            generation_fps,
            segment.frame_rounding,
        )
        planned_segments = tuple(
            item.model_copy(
                update={
                    "mode": segment.mode,
                    "generation_fps": generation_fps,
                    "frame_count": frame_count,
                    "actual_duration_ms": model.frame_duration_ms(
                        frame_count,
                        generation_fps,
                    ),
                    "continuation_policy": segment.continuation_policy,
                }
            )
            if item.segment_id == segment.segment_id
            else item
            for item in plan.segments
        )
        synchronized = plan.model_copy(update={"segments": planned_segments})
        self._session.segment_plan = synchronized
        return Wan2LabProject.model_validate(
            project.model_copy(update={"segment_plan": synchronized}).model_dump()
        )

    @Slot(QUrl)
    def exportApprovedVideo(self, url: QUrl) -> None:  # noqa: N802
        if self._export_runner.running:
            self._set_status("An export is already running")
            return
        output = Path(url.toLocalFile()).expanduser().resolve()
        if not output.suffix:
            output = output.with_suffix(".mp4")
        try:
            ffmpeg_executable = (
                self._session.project.project_settings.ffmpeg_executable
            )
            if shutil.which(ffmpeg_executable) is None:
                raise FileNotFoundError(
                    f"configured FFmpeg executable was not found: {ffmpeg_executable}"
                )
            revisions = {item.revision_id: item for item in self._session.project.segment_revisions}
            source_paths = {}
            for segment in self._session.project.segments:
                revision_id = segment.current_approved_revision_id
                if revision_id is None:
                    continue
                revision = revisions[revision_id]
                result_asset = next(
                    item
                    for item in self._session.project.assets
                    if item.asset_id == revision.result_asset_id
                )
                source_paths[result_asset.asset_id] = str(
                    self._asset_store.resolve_ref(result_asset)
                )
            plan = build_export_plan(
                export_id=f"export-{uuid4().hex}",
                segments=self._session.project.segments,
                revisions=self._session.project.segment_revisions,
                source_paths=source_paths,
                output_path=str(output),
                output_fps=self.outputFps,
                ffmpeg_executable=ffmpeg_executable,
                work_directory=str(output.parent / f".{output.stem}-wan2lab-work"),
                provenance_id=f"provenance-{uuid4().hex}",
            )
            self._active_export_plan = plan
            self._export_runner.start(plan)
        except Exception as error:
            self._active_export_plan = None
            self._set_status(f"Export planning failed: {error}")
            return
        self._append_event(f"Started non-blocking export to {output}")
        self._set_status("Exporting approved immutable revisions…")
        self.statusChanged.emit()

    @Slot()
    def cancelExport(self) -> None:  # noqa: N802
        self._export_runner.cancel()

    @Slot(str)
    def openProject(self, path: str) -> None:  # noqa: N802
        if self._active_wan_commands or self.frameModificationRunning:
            self._set_status("Cancel active generation or modification before opening a project")
            return
        try:
            project = load_project(Path(path).expanduser())
        except Exception as error:
            self._set_status(f"Open failed: {error}")
            return
        self._session = WanStudioSession(project)
        self._asset_store = LocalAssetStore(
            Path(path).expanduser().resolve().parent / project.project_settings.asset_root
        )
        self._project_name = Path(path).stem
        self._review_segment_index = 0
        self._review_revision_id = None
        self._preview_keyframe_index = 0
        self._events.clear()
        self._draft_keyframe_regions.clear()
        self._face_batch_draft = None
        self._pending_checkpoint_application = None
        self._refresh_mannequin_preview()
        self._set_status(f"Opened {path}")
        self.projectChanged.emit()
        self.eventLogChanged.emit()

    @property
    def session(self) -> WanStudioSession:
        return self._session

    @Slot(int)
    def selectReviewSegment(self, segment_index: int) -> None:  # noqa: N802
        if not self._session.project.segments:
            self._review_segment_index = 0
        else:
            self._review_segment_index = min(
                max(0, segment_index),
                len(self._session.project.segments) - 1,
            )
        self._review_revision_id = None
        self.projectChanged.emit()

    @Slot(int)
    def selectPreviewKeyframe(self, keyframe_index: int) -> None:  # noqa: N802
        if not self._session.project.keyframes:
            self._preview_keyframe_index = 0
        else:
            self._preview_keyframe_index = min(
                max(0, keyframe_index),
                len(self._session.project.keyframes) - 1,
            )
        self.projectChanged.emit()

    @Slot(int)
    def selectReviewRevision(self, revision_index: int) -> None:  # noqa: N802
        if not self._session.project.segments:
            self._review_revision_id = None
            return
        segment = self._session.project.segments[
            min(self._review_segment_index, len(self._session.project.segments) - 1)
        ]
        if not segment.revision_ids:
            self._review_revision_id = None
            return
        index = min(max(0, revision_index), len(segment.revision_ids) - 1)
        self._review_revision_id = segment.revision_ids[index]
        self.projectChanged.emit()

    def _selected_review_revision(self):
        if not self._session.project.segments:
            return None
        index = min(self._review_segment_index, len(self._session.project.segments) - 1)
        segment = self._session.project.segments[index]
        if not segment.revision_ids:
            return None
        revision_id = segment.revision_ids[self.reviewRevisionIndex]
        return next(
            (
                item
                for item in self._session.project.segment_revisions
                if item.revision_id == revision_id
            ),
            None,
        )

    @staticmethod
    def _new_session(duration_ms: int) -> WanStudioSession:
        return WanStudioSession(
            Wan2LabProject(
                project_id=f"project-{uuid4().hex}",
                project_settings=ProjectSettings(
                    default_wan_backend_id="mock-wan",
                    default_wan_model_id="wan-test",
                ),
                timeline=Timeline(duration_ms=duration_ms, output_fps=24.0),
            )
        )

    def _store_for_project(self, project_id: str) -> LocalAssetStore:
        return LocalAssetStore(self._asset_base / project_id / "assets")

    def _mannequin_guide_assets(self, scene_id: str) -> dict[GuideKind, str]:
        scene = next(
            item
            for item in self._session.project.mannequin_scenes
            if item.scene_id == scene_id
        )
        available = set(scene.guide_asset_ids)
        guides: dict[GuideKind, str] = {}
        for record in self._session.project.generation_records:
            if record.parameters.get("scene_id") != scene_id:
                continue
            try:
                kind = GuideKind(str(record.parameters["guide_kind"]))
            except (KeyError, ValueError):
                continue
            output = next(
                (item for item in record.output_asset_ids if item in available),
                None,
            )
            if output is not None:
                guides[kind] = output
        return guides

    @staticmethod
    def _wan_asset(record, kind: AssetKind) -> AssetRef:
        return AssetRef(
            asset_id=record.asset_id,
            kind=kind,
            storage_path=record.relative_path,
            sha256=record.sha256,
            width=record.width,
            height=record.height,
            parent_asset_ids=record.parent_asset_ids,
        )

    @staticmethod
    def _z_rotation(degrees: float) -> Quaternion:
        half = math.radians(max(-180.0, min(180.0, degrees))) / 2
        return Quaternion(z=math.sin(half), w=math.cos(half))

    @staticmethod
    def _euler_rotation(
        x_degrees: float,
        y_degrees: float,
        z_degrees: float,
    ) -> Quaternion:
        roll = math.radians(x_degrees) / 2
        pitch = math.radians(y_degrees) / 2
        yaw = math.radians(z_degrees) / 2
        cr, sr = math.cos(roll), math.sin(roll)
        cp, sp = math.cos(pitch), math.sin(pitch)
        cy, sy = math.cos(yaw), math.sin(yaw)
        return Quaternion(
            x=sr * cp * cy - cr * sp * sy,
            y=cr * sp * cy + sr * cp * sy,
            z=cr * cp * sy - sr * sp * cy,
            w=cr * cp * cy + sr * sp * sy,
        )

    def _selected_mannequin_instance(
        self,
        scene,
    ) -> tuple[int, MannequinInstance]:
        index = min(self._mannequin_instance_index, len(scene.instances) - 1)
        return index, scene.instances[index]

    @staticmethod
    def _camera_rotation(yaw_degrees: float, pitch_degrees: float) -> Quaternion:
        yaw = math.radians(max(-180.0, min(180.0, yaw_degrees))) / 2
        pitch = math.radians(max(-89.0, min(89.0, pitch_degrees))) / 2
        return Quaternion(
            x=math.cos(yaw) * math.sin(pitch),
            y=math.sin(yaw) * math.cos(pitch),
            z=-math.sin(yaw) * math.sin(pitch),
            w=math.cos(yaw) * math.cos(pitch),
        )

    def _refresh_mannequin_preview(self) -> None:
        if not self._session.project.mannequin_scenes:
            self._mannequin_preview_url = QUrl()
            return
        scene = self._session.project.mannequin_scenes[-1]
        preview_dir = self._asset_base / self._session.project.project_id / ".preview"
        try:
            guides = render_mannequin_guides(scene, preview_dir)
            shaded = next(item for item in guides if item.kind is GuideKind.SHADED)
        except Exception as error:
            self._mannequin_preview_url = QUrl()
            self._set_status(f"Mannequin preview failed: {error}")
            return
        self._mannequin_preview_revision += 1
        url = QUrl.fromLocalFile(str(shaded.path))
        url.setQuery(f"revision={self._mannequin_preview_revision}")
        self._mannequin_preview_url = url

    @Slot(object)
    def _handle_worker_event(self, event) -> None:
        if isinstance(event, CapabilitiesEvent):
            models = event.capabilities.get("model_variants", ())
            parameters = event.capabilities.get("parameter_descriptors", ())
            components = event.capabilities.get("component_models", {})
            normalized_capabilities = {
                key: value
                for key, value in event.capabilities.items()
                if key != "component_models"
            }
            self._inspected_capabilities = BackendCapabilities.model_validate(
                normalized_capabilities
            )
            self._backend_model_descriptors = [
                dict(item) for item in models if isinstance(item, dict)
            ]
            self._wan_model_control_index = 0
            self._backend_models = [
                str(item.get("display_name", item.get("model_id", "unknown")))
                for item in models
                if isinstance(item, dict)
            ]
            self._backend_parameters = [
                str(item.get("display_name", item.get("key", "unknown")))
                for item in parameters
                if isinstance(item, dict)
            ]
            self._backend_parameter_descriptors = [
                dict(item) for item in parameters if isinstance(item, dict)
            ]
            component_mapping = components if isinstance(components, dict) else {}
            self._backend_vae_models = [
                str(item) for item in component_mapping.get("vae", ())
            ]
            self._backend_text_encoder_models = [
                str(item) for item in component_mapping.get("text_encoder", ())
            ]
            vendor = ", ".join(event.capabilities.get("accelerator_vendors", ()))
            wrapper = event.capabilities.get("wrapper_version", "unknown")
            self._backend_status = (
                f"ComfyUI Wan ready · {vendor or 'unknown accelerator'} · wrapper {wrapper} · "
                f"{len(self._backend_models)} compatible model(s)"
            )
            self._append_event(self._backend_status)
            self.projectChanged.emit()
        elif isinstance(event, ModelsEvent):
            self._backend_models = [str(item.get("display_name", "unknown")) for item in event.models]
        elif isinstance(event, RuntimeStatusEvent):
            self._backend_status = json.dumps(event.status, sort_keys=True)
        elif isinstance(event, ProgressEvent):
            progress = event.progress
            detail = (
                f" {progress.current}/{progress.total}"
                if progress.current is not None and progress.total is not None
                else ""
            )
            self._backend_status = progress.message or f"Wan {progress.stage}{detail}"
            self._set_status(f"{progress.segment_id or progress.job_id}: {progress.stage}{detail}")
        elif isinstance(event, ResultEvent):
            self._complete_worker_result(event)
        elif isinstance(event, ErrorEvent):
            self._backend_status = event.error.message
            self._append_event(f"Wan worker: {event.error.message}")
            if event.command_id == self._pending_model_command_id:
                self._pending_model_command_id = None
                self._pending_wan_model_id = None
            revision_id = self._finish_active_wan_command(event.command_id)
            if revision_id is not None:
                try:
                    self._session.fail_worker_generation(
                        revision_id=revision_id,
                        message=event.error.message,
                        cancelled=event.error.stage == "cancelled",
                    )
                    self.projectChanged.emit()
                except Exception as error:
                    self._append_event(f"Could not record worker failure: {error}")
        elif isinstance(event, AckEvent):
            self._backend_status = event.message
            self._append_event(f"Wan worker: {event.message}")
            if (
                event.command_id == self._pending_model_command_id
                and self._pending_wan_model_id is not None
                and self._inspected_capabilities is not None
            ):
                self._selected_wan_model_id = self._pending_wan_model_id
                self._capabilities = self._inspected_capabilities
                settings = self._session.project.project_settings.model_copy(
                    update={
                        "default_wan_backend_id": self._capabilities.backend_id,
                        "default_wan_model_id": self._selected_wan_model_id,
                    }
                )
                self._session.project = Wan2LabProject.model_validate(
                    self._session.project.model_copy(
                        update={"project_settings": settings}
                    ).model_dump()
                )
                self._pending_model_command_id = None
                self._pending_wan_model_id = None
                self.projectChanged.emit()
            if event.command_id == self._pending_release_command_id:
                self._pending_release_command_id = None
                self._selected_wan_model_id = None
                self._set_status("All accelerator models released")
                self.projectChanged.emit()
        self.statusChanged.emit()

    def _complete_worker_result(self, event: ResultEvent) -> None:
        revision_id = self._finish_active_wan_command(event.command_id)
        if revision_id is None:
            self._append_event(f"Ignored result for unknown command {event.command_id}")
            return
        try:
            revision = next(
                item
                for item in self._session.project.segment_revisions
                if item.revision_id == revision_id
            )
            storage_keys = event.result.metadata.get("output_storage_keys", ())
            if not isinstance(storage_keys, (list, tuple)) or len(storage_keys) != 1:
                raise ValueError("Wan generation requires exactly one persistent video output")
            source = self._comfy_assets.resolve_output(str(storage_keys[0]))
            record = self._asset_store.register_generated(
                source,
                media_type="video/mp4",
                parent_asset_ids=tuple(
                    item
                    for item in (
                        revision.source_request.start_image_asset_id,
                        revision.source_request.end_image_asset_id,
                    )
                    if item is not None
                ),
                metadata={"comfyui_storage_key": str(storage_keys[0])},
            )
            result_asset = AssetRef(
                asset_id=record.asset_id,
                kind=AssetKind.VIDEO,
                storage_path=record.relative_path,
                sha256=record.sha256,
                width=revision.source_request.width,
                height=revision.source_request.height,
                frame_count=revision.source_request.frame_count,
                duration_ms=(
                    revision.source_request.end_ms - revision.source_request.start_ms
                ),
                parent_asset_ids=record.parent_asset_ids,
                creation_operation_id=f"{revision.revision_id}-provenance",
                immutable_source=False,
            )
            normalized_result = event.result.model_copy(
                update={"result_asset_id": result_asset.asset_id}
            )
            completed = self._session.complete_worker_generation(
                revision_id=revision_id,
                result=normalized_result,
                result_asset=result_asset,
                backend_version=(
                    self._inspected_capabilities.backend_version
                    if self._inspected_capabilities is not None
                    else None
                ),
            )
        except Exception as error:
            try:
                self._session.fail_worker_generation(
                    revision_id=revision_id,
                    message=f"result registration failed: {error}",
                )
            except Exception:
                pass
            self._set_status(f"Wan result registration failed: {error}")
            self.projectChanged.emit()
            return
        self._append_event(
            f"{completed.segment_id} revision {completed.revision_number} ready for review"
        )
        self._set_status("Segment ready for review — approve before continuing")
        self.projectChanged.emit()

    def _finish_active_wan_command(self, command_id: str) -> str | None:
        revision_id = self._active_wan_commands.pop(command_id, None)
        for job_id, active_command_id in tuple(self._active_wan_jobs.items()):
            if active_command_id == command_id:
                del self._active_wan_jobs[job_id]
        return revision_id

    @Slot(str)
    def _handle_worker_transport_error(self, message: str) -> None:
        self._backend_status = message
        self._append_event(message)
        for command_id in tuple(self._active_wan_commands):
            revision_id = self._finish_active_wan_command(command_id)
            if revision_id is not None:
                try:
                    self._session.fail_worker_generation(
                        revision_id=revision_id,
                        message=message,
                    )
                except Exception as error:
                    self._append_event(f"Could not record worker transport failure: {error}")
        if self._pending_model_command_id is not None:
            self._pending_model_command_id = None
            self._pending_wan_model_id = None
        self.projectChanged.emit()
        self.statusChanged.emit()

    @Slot(object)
    def _handle_krea_event(self, event) -> None:
        if not isinstance(event, dict):
            self._handle_krea_transport_error("Invalid Krea worker event object")
            return
        command_id = str(event.get("command_id", ""))
        state = str(event.get("state", "error"))
        message = str(event.get("message", "Krea worker event"))
        payload = event.get("payload", {})
        payload = payload if isinstance(payload, dict) else {}
        self._krea_status = message
        if state == "ready" and command_id == self._krea_load_command_id:
            self._krea_loaded = True
            self._krea_load_command_id = None
            capabilities = payload.get("capabilities", {})
            capability_mapping = capabilities if isinstance(capabilities, dict) else {}
            metadata = capability_mapping.get("metadata", {})
            metadata_mapping = metadata if isinstance(metadata, dict) else {}
            depth_models = metadata_mapping.get("depth_control_model_ids", ())
            self._krea_depth_control_model_ids = (
                tuple(str(item) for item in depth_models)
                if isinstance(depth_models, (list, tuple))
                else ()
            )
            self._append_event("Krea model loaded in isolated worker")
            self._set_status("Krea ready for character sheets, keyframes, and frame edits")
        elif state == "complete" and command_id in self._pending_krea_jobs:
            self._complete_krea_job(command_id, payload)
        elif state in {"error", "cancelled"}:
            failed_context = self._pending_krea_jobs.pop(command_id, None)
            if failed_context is not None and failed_context.get("group_id") is not None:
                self._style_duplications.pop(str(failed_context["group_id"]), None)
            if (
                failed_context is not None
                and failed_context.get("operation") == "frame_edit_replacement"
            ):
                self._pending_krea_frame_edit = None
            if (
                failed_context is not None
                and failed_context.get("operation")
                in {"batch_frame_edit_replacement", "batch_face_detection"}
            ):
                self._active_batch_frame_edit = None
            if command_id == self._krea_load_command_id:
                self._krea_load_command_id = None
                self._krea_loaded = False
            self._append_event(f"Krea worker: {message}")
            self._set_status(message)
        elif state == "running":
            self._set_status(f"Krea: {message}")
        else:
            self._append_event(f"Krea worker: {message}")
        self.statusChanged.emit()

    def _complete_krea_job(self, command_id: str, payload: dict[str, object]) -> None:
        context = self._pending_krea_jobs.pop(command_id)
        try:
            operation = str(context["operation"])
            if operation == "batch_face_detection":
                batch = self._active_batch_frame_edit
                if batch is None or batch.get("mode") != "face_detection":
                    raise RuntimeError("face detection was cancelled after a worker result")
                raw_faces = payload.get("faces", ())
                if not isinstance(raw_faces, list):
                    raise ValueError("face detector result must contain a face list")
                frame_index = int(context["frame_index"])
                proposals = []
                for candidate_index, raw_face in enumerate(raw_faces):
                    if not isinstance(raw_face, dict) or not isinstance(
                        raw_face.get("box"), dict
                    ):
                        raise ValueError("face detector returned an invalid candidate")
                    box = raw_face["box"]
                    proposals.append(
                        FaceProposal(
                            proposal_id=(
                                f"face-{frame_index}-{candidate_index}-{uuid4().hex}"
                            ),
                            frame_index=frame_index,
                            identity_id=str(batch["identity_id"]),
                            region_id=f"face-{frame_index}-{candidate_index}",
                            box=Rectangle(
                                x0=float(box["x0"]),
                                y0=float(box["y0"]),
                                x1=float(box["x1"]),
                                y1=float(box["y1"]),
                            ),
                            score=float(raw_face["score"]),
                            prompt=str(batch["identity_prompt"]),
                        )
                    )
                batch["candidates"][frame_index] = tuple(proposals)
                self._start_next_batch_krea_edit()
                return
            raw_paths = payload.get("asset_paths", ())
            if not isinstance(raw_paths, list) or len(raw_paths) != 1:
                raise ValueError("Krea job must return exactly one image")
            source = Path(str(raw_paths[0])).expanduser().resolve()
            if self._krea_result_root not in source.parents or not source.is_file():
                raise ValueError("Krea worker returned an output outside its result root")
            if operation == "frame_edit_replacement":
                frame_context = context["frame_context"]
                self._pending_krea_frame_edit = None
                self.modifyFrame(
                    int(frame_context["segment_index"]),
                    int(frame_context["frame_index"]),
                    QUrl.fromLocalFile(str(source)),
                    str(frame_context["prompt"]),
                    bool(frame_context["propagate"]),
                )
                if self._active_frame_edit is None:
                    raise RuntimeError("Krea replacement could not start revision assembly")
                self._active_frame_edit.update(
                    {
                        "operation_type": frame_context["operation_type"],
                        "region": frame_context["region"],
                        "user_confirmed_face_region": frame_context[
                            "user_confirmed_face_region"
                        ],
                    }
                )
                return
            if operation == "batch_frame_edit_replacement":
                batch = self._active_batch_frame_edit
                if batch is None:
                    raise RuntimeError("batch frame edit was cancelled after a worker result")
                replacements = batch["replacement_paths"]
                if not isinstance(replacements, dict):
                    raise RuntimeError("invalid batch replacement accumulator")
                replacements[int(context["frame_index"])] = str(source)
                self._start_next_batch_krea_edit()
                return
            record = self._asset_store.register_generated(
                source,
                media_type=image_media_type(source),
                parent_asset_ids=tuple(context.get("input_asset_ids", ())),
                metadata={
                    "worker_command_id": command_id,
                    "operation": str(context["operation"]),
                },
            )
            provenance_id = f"provenance-{uuid4().hex}"
            asset = self._wan_asset(record, AssetKind.IMAGE).model_copy(
                update={
                    "creation_operation_id": provenance_id,
                    "immutable_source": False,
                }
            )
            provenance = ProvenanceRecord(
                provenance_id=provenance_id,
                operation=f"krea_generate_{operation}",
                created_at=datetime.now(UTC),
                model_identifiers=("krea2",),
                backend_id="krea-comfyui",
                parameters={"request": context["request"]},
                input_asset_ids=tuple(context.get("input_asset_ids", ())),
                output_asset_ids=(asset.asset_id,),
                runtime={"worker_payload": payload.get("metadata", {})},
            )
            if operation == "character_sheet_entry":
                entry = PoseViewEntry(
                    entry_id=f"entry-{uuid4().hex}",
                    name=str(context["entry_name"]),
                    image_asset_id=asset.asset_id,
                    identity_id=str(context["identity_id"]),
                    appearance_id=str(context["appearance_id"]),
                    source_type=PoseViewSource.GENERATED,
                    provenance_id=provenance.provenance_id,
                )
                self._session.project = register_pose_view_entry(
                    self._session.project,
                    sheet_id=str(context["sheet_id"]),
                    entry=entry,
                    asset=asset,
                    provenance=provenance,
                )
                completed_label = f"character-sheet entry {entry.name}"
                status = "Generated entry saved as an immutable draft for review"
            elif operation == "regional_keyframe":
                keyframe = Keyframe(
                    keyframe_id=f"keyframe-{uuid4().hex}",
                    time_ms=int(context["time_ms"]),
                    image_asset_id=asset.asset_id,
                    source_type=KeyframeSource.KREA_GENERATED,
                    scene_prompt=str(context["scene_prompt"]),
                    environment_prompt=str(context["environment_prompt"]),
                    lighting_prompt=str(context["lighting_prompt"]),
                    region_assignments=tuple(context["region_assignments"]),
                    mannequin_scene_id=(
                        str(context["mannequin_scene_id"])
                        if context["mannequin_scene_id"] is not None
                        else None
                    ),
                    provenance_id=provenance.provenance_id,
                )
                self._session.project = add_timeline_keyframe(
                    self._session.project,
                    keyframe=keyframe,
                    asset=asset,
                    provenance=provenance,
                )
                self._draft_keyframe_regions.clear()
                completed_label = f"regional keyframe at {keyframe.time_ms / 1000:g}s"
                status = "Generated keyframe saved as a draft; approve it before Wan planning"
            elif operation == "keyframe_face_refinement":
                source_keyframe = next(
                    item
                    for item in self._session.project.keyframes
                    if item.keyframe_id == context["source_keyframe_id"]
                )
                keyframe = source_keyframe.model_copy(
                    update={
                        "keyframe_id": f"keyframe-{uuid4().hex}",
                        "image_asset_id": asset.asset_id,
                        "source_type": KeyframeSource.EDITED,
                        "provenance_id": provenance.provenance_id,
                        "approved": False,
                        "locked": False,
                        "parent_keyframe_id": source_keyframe.keyframe_id,
                        "source_frame_asset_id": source_keyframe.image_asset_id,
                    }
                )
                self._session.project = revise_timeline_keyframe(
                    self._session.project,
                    source_keyframe_id=source_keyframe.keyframe_id,
                    revised_keyframe=keyframe,
                    asset=asset,
                    provenance=provenance,
                )
                self._session.segment_plan = None
                completed_label = (
                    f"face-refined keyframe at {keyframe.time_ms / 1000:g}s"
                )
                status = "Refined keyframe saved as a draft; approve before replanning"
            elif operation == "style_duplication_entry":
                group_id = str(context["group_id"])
                group = self._style_duplications.get(group_id)
                if group is None:
                    raise RuntimeError("style duplication was cancelled after a worker result")
                replacements = group["replacements"]
                output_assets = group["assets"]
                provenance_records = group["provenance"]
                if not all(
                    isinstance(items, list)
                    for items in (replacements, output_assets, provenance_records)
                ):
                    raise RuntimeError("invalid style-duplication accumulator")
                replacements.append(
                    StyleDuplicationEntry(
                        source_entry_id=str(context["source_entry_id"]),
                        target_entry_id=f"entry-{uuid4().hex}",
                        target_asset_id=asset.asset_id,
                        provenance_id=provenance.provenance_id,
                    )
                )
                output_assets.append(asset)
                provenance_records.append(provenance)
                self._start_next_style_duplication(group_id)
                return
            else:
                raise ValueError(f"unknown pending Krea operation: {operation}")
        except Exception as error:
            self._set_status(f"Krea result registration failed: {error}")
            return
        self._append_event(f"Generated {completed_label} with Krea")
        self._set_status(status)
        self.projectChanged.emit()

    def _finish_style_duplication(self, group_id: str) -> None:
        group = self._style_duplications.pop(group_id)
        try:
            self._session.project = register_style_duplication(
                self._session.project,
                source_sheet_id=str(group["source_sheet_id"]),
                target_profile=group["target_profile"],
                target_sheet_id=str(group["target_sheet_id"]),
                target_name=str(group["target_name"]),
                replacements=tuple(group["replacements"]),
                assets=tuple(group["assets"]),
                provenance=tuple(group["provenance"]),
            )
        except Exception as error:
            self._set_status(f"Style duplication registration failed: {error}")
            return
        self._append_event(
            f"Created non-destructive appearance sheet {group['target_name']}"
        )
        self._set_status("Restyled sheet saved; every derived entry remains a review draft")
        self.projectChanged.emit()

    @Slot(str)
    def _handle_krea_transport_error(self, message: str) -> None:
        self._krea_status = message
        self._krea_loaded = False
        self._krea_load_command_id = None
        self._pending_krea_jobs.clear()
        self._pending_krea_frame_edit = None
        self._active_batch_frame_edit = None
        self._style_duplications.clear()
        self._append_event(message)
        self._set_status(message)

    @Slot(str, int, int)
    def _handle_export_progress(self, stage: str, current: int, total: int) -> None:
        self._set_status(f"Export {stage}: {current}/{total}")

    @Slot(str, int, int)
    def _handle_frame_progress(self, stage: str, current: int, total: int) -> None:
        self._set_status(f"Frame modification {stage}: {current}/{total}")

    @Slot(str, str, str)
    def _complete_frame_modification(
        self,
        original_path: str,
        replacement_path: str,
        revised_path: str,
    ) -> None:
        context = self._active_frame_edit
        self._active_frame_edit = None
        if context is None:
            self._set_status("Frame modification completed without an active edit")
            return
        try:
            source_revision_id = str(context["revision_id"])
            source_video_asset_id = str(context["source_video_asset_id"])
            frame_index = int(context["frame_index"])
            original_record = self._asset_store.register_generated(
                Path(original_path),
                media_type="image/png",
                parent_asset_ids=(source_video_asset_id,),
                metadata={"frame_index": frame_index, "operation": "extract_frame"},
            )
            original_asset = self._wan_asset(original_record, AssetKind.IMAGE)
            replacement_record = self._asset_store.create_derived(
                Path(replacement_path),
                parent_asset_ids=(original_asset.asset_id,),
                media_type="image/png",
                metadata={"frame_index": frame_index, "operation": "replace_frame"},
            )
            replacement_asset = self._wan_asset(replacement_record, AssetKind.IMAGE)
            revised_record = self._asset_store.register_generated(
                Path(revised_path),
                media_type="video/mp4",
                parent_asset_ids=(source_video_asset_id, replacement_asset.asset_id),
                metadata={"operation": "assemble_frame_revision"},
            )
            source_revision = next(
                item
                for item in self._session.project.segment_revisions
                if item.revision_id == source_revision_id
            )
            revised_asset = AssetRef(
                asset_id=revised_record.asset_id,
                kind=AssetKind.VIDEO,
                storage_path=revised_record.relative_path,
                sha256=revised_record.sha256,
                width=source_revision.source_request.width,
                height=source_revision.source_request.height,
                frame_count=source_revision.source_request.frame_count,
                duration_ms=(
                    source_revision.source_request.end_ms
                    - source_revision.source_request.start_ms
                ),
                parent_asset_ids=revised_record.parent_asset_ids,
                immutable_source=False,
            )
            extract_provenance_id = f"provenance-{uuid4().hex}"
            edit_provenance_id = f"provenance-{uuid4().hex}"
            assembly_provenance_id = f"provenance-{uuid4().hex}"
            original_asset = original_asset.model_copy(
                update={
                    "creation_operation_id": extract_provenance_id,
                    "immutable_source": False,
                }
            )
            replacement_asset = replacement_asset.model_copy(
                update={
                    "creation_operation_id": edit_provenance_id,
                    "immutable_source": False,
                }
            )
            revised_asset = revised_asset.model_copy(
                update={"creation_operation_id": assembly_provenance_id}
            )
            edit = FrameEditRecord(
                edit_id=f"frame-edit-{uuid4().hex}",
                segment_revision_id=source_revision_id,
                original_frame_asset_id=original_asset.asset_id,
                replacement_frame_asset_id=replacement_asset.asset_id,
                frame_index=frame_index,
                operation_type=context.get(
                    "operation_type",
                    FrameEditOperation.IMAGE_EDIT,
                ),
                prompt=str(context["prompt"]),
                region=context.get("region"),
                user_confirmed_face_region=bool(
                    context.get("user_confirmed_face_region", False)
                ),
                boundary_propagation=(
                    BoundaryPropagation.PROPAGATE_AS_ANCHOR
                    if bool(context["propagate"])
                    else BoundaryPropagation.LOCAL_REPAIR
                ),
                provenance_id=edit_provenance_id,
            )
            provenance = (
                ProvenanceRecord(
                    provenance_id=extract_provenance_id,
                    operation="extract_frame",
                    created_at=datetime.now(UTC),
                    input_asset_ids=(source_video_asset_id,),
                    output_asset_ids=(original_asset.asset_id,),
                    parameters={"frame_index": frame_index},
                ),
                ProvenanceRecord(
                    provenance_id=edit_provenance_id,
                    operation=(
                        "krea_refine_face"
                        if edit.operation_type is FrameEditOperation.FACE_REFINEMENT
                        else "import_or_generate_frame_replacement"
                    ),
                    created_at=datetime.now(UTC),
                    prompts={"prompt": str(context["prompt"])},
                    input_asset_ids=(original_asset.asset_id,),
                    output_asset_ids=(replacement_asset.asset_id,),
                    parameters={"frame_index": frame_index},
                ),
                ProvenanceRecord(
                    provenance_id=assembly_provenance_id,
                    operation="assemble_frame_revision",
                    created_at=datetime.now(UTC),
                    input_asset_ids=(source_video_asset_id, replacement_asset.asset_id),
                    output_asset_ids=(revised_asset.asset_id,),
                    parameters={
                        "frame_index": frame_index,
                        "generation_fps": source_revision.source_request.generation_fps,
                    },
                    runtime={
                        "ffmpeg": self._session.project.project_settings.ffmpeg_executable
                    },
                ),
            )
            project_with_original = Wan2LabProject.model_validate(
                self._session.project.model_copy(
                    update={
                        "assets": (*self._session.project.assets, original_asset),
                        "generation_records": (
                            *self._session.project.generation_records,
                            provenance[0],
                        ),
                    }
                ).model_dump()
            )
            self._session.project = commit_frame_edit_revision(
                project_with_original,
                segment_id=str(context["segment_id"]),
                source_revision_id=source_revision_id,
                edit_records=(edit,),
                replacement_assets=(replacement_asset,),
                revised_video_asset=revised_asset,
                provenance=provenance[1:],
                assembly_provenance_id=assembly_provenance_id,
                new_revision_id=f"{source_revision.segment_id}-revision-{uuid4().hex}",
            )
        except Exception as error:
            self._set_status(f"Frame modification registration failed: {error}")
            return
        self._append_event(
            f"Frame {frame_index} became a new immutable segment revision for review"
        )
        self._set_status("Modified segment ready for mandatory review")
        self.projectChanged.emit()

    @Slot(str)
    def _fail_frame_modification(self, message: str) -> None:
        self._active_frame_edit = None
        self._append_event(message)
        self._set_status(message)

    @Slot(object, object, str)
    def _complete_batch_frame_modification(
        self,
        original_paths,
        replacement_paths,
        revised_path: str,
    ) -> None:
        context = self._active_batch_frame_edit
        self._active_batch_frame_edit = None
        if context is None:
            self._set_status("Batch frame modification completed without an active edit")
            return
        try:
            source_revision_id = str(context["revision_id"])
            source_video_asset_id = str(context["source_video_asset_id"])
            selection = context["selection"]
            source_revision = next(
                item
                for item in self._session.project.segment_revisions
                if item.revision_id == source_revision_id
            )
            originals = []
            replacements = []
            edit_records = []
            extraction_provenance = []
            edit_provenance = []
            is_face_refinement = context.get("mode") == "face_refinement"
            reference_inputs = (
                (
                    str(context["reference_asset_id"]),
                    *tuple(str(item) for item in context["adapter_asset_ids"]),
                )
                if is_face_refinement
                else ()
            )
            for frame_index, original_path, replacement_path in zip(
                selection.frame_indices,
                original_paths,
                replacement_paths,
                strict=True,
            ):
                original_record = self._asset_store.register_generated(
                    Path(original_path),
                    media_type="image/png",
                    parent_asset_ids=(source_video_asset_id,),
                    metadata={"frame_index": frame_index, "operation": "extract_frame"},
                )
                original_asset = self._wan_asset(original_record, AssetKind.IMAGE)
                replacement_record = self._asset_store.create_derived(
                    Path(replacement_path),
                    parent_asset_ids=(original_asset.asset_id,),
                    media_type="image/png",
                    metadata={
                        "frame_index": frame_index,
                        "operation": (
                            "krea_batch_face_refinement"
                            if is_face_refinement
                            else "krea_batch_edit"
                        ),
                    },
                )
                replacement_asset = self._wan_asset(replacement_record, AssetKind.IMAGE)
                extract_id = f"provenance-{uuid4().hex}"
                edit_id = f"provenance-{uuid4().hex}"
                original_asset = original_asset.model_copy(
                    update={
                        "creation_operation_id": extract_id,
                        "immutable_source": False,
                    }
                )
                replacement_asset = replacement_asset.model_copy(
                    update={
                        "creation_operation_id": edit_id,
                        "immutable_source": False,
                    }
                )
                originals.append(original_asset)
                replacements.append(replacement_asset)
                extraction_provenance.append(
                    ProvenanceRecord(
                        provenance_id=extract_id,
                        operation="extract_frame",
                        created_at=datetime.now(UTC),
                        input_asset_ids=(source_video_asset_id,),
                        output_asset_ids=(original_asset.asset_id,),
                        parameters={"frame_index": frame_index},
                    )
                )
                edit_provenance.append(
                    ProvenanceRecord(
                        provenance_id=edit_id,
                        operation=(
                            "krea_batch_face_refinement"
                            if is_face_refinement
                            else "krea_batch_frame_edit"
                        ),
                        created_at=datetime.now(UTC),
                        model_identifiers=("krea2",),
                        backend_id="krea-comfyui",
                        prompts={"prompt": str(context["prompt"])},
                        input_asset_ids=(original_asset.asset_id, *reference_inputs),
                        output_asset_ids=(replacement_asset.asset_id,),
                        parameters={
                            "frame_index": frame_index,
                            "operation_type": (
                                FrameEditOperation.FACE_REFINEMENT.value
                                if is_face_refinement
                                else FrameEditOperation.IMAGE_EDIT.value
                            ),
                            "region": (
                                context["regions"][frame_index].box.model_dump(
                                    mode="json"
                                )
                                if is_face_refinement
                                else None
                            ),
                        },
                    )
                )
                edit_records.append(
                    FrameEditRecord(
                        edit_id=f"frame-edit-{uuid4().hex}",
                        segment_revision_id=source_revision_id,
                        original_frame_asset_id=original_asset.asset_id,
                        replacement_frame_asset_id=replacement_asset.asset_id,
                        frame_index=frame_index,
                        operation_type=(
                            FrameEditOperation.FACE_REFINEMENT
                            if is_face_refinement
                            else FrameEditOperation.IMAGE_EDIT
                        ),
                        prompt=str(context["prompt"]),
                        settings=(
                            {"reference_asset_id": str(context["reference_asset_id"])}
                            if is_face_refinement
                            else {}
                        ),
                        region=(
                            context["regions"][frame_index].box
                            if is_face_refinement
                            else None
                        ),
                        identity_id=(
                            str(context["identity_id"]) if is_face_refinement else None
                        ),
                        adapters=(context["adapters"] if is_face_refinement else ()),
                        user_confirmed_face_region=is_face_refinement,
                        boundary_propagation=(
                            BoundaryPropagation.PROPAGATE_AS_ANCHOR
                            if bool(context.get("propagate"))
                            and frame_index
                            in {0, source_revision.source_request.frame_count - 1}
                            else BoundaryPropagation.LOCAL_REPAIR
                        ),
                        provenance_id=edit_id,
                    )
                )
            revised_record = self._asset_store.register_generated(
                Path(revised_path),
                media_type="video/mp4",
                parent_asset_ids=(
                    source_video_asset_id,
                    *(item.asset_id for item in replacements),
                ),
                metadata={"operation": "assemble_batch_frame_revision"},
            )
            revised_asset = AssetRef(
                asset_id=revised_record.asset_id,
                kind=AssetKind.VIDEO,
                storage_path=revised_record.relative_path,
                sha256=revised_record.sha256,
                width=source_revision.source_request.width,
                height=source_revision.source_request.height,
                frame_count=source_revision.source_request.frame_count,
                duration_ms=(
                    source_revision.source_request.end_ms
                    - source_revision.source_request.start_ms
                ),
                parent_asset_ids=revised_record.parent_asset_ids,
                immutable_source=False,
            )
            assembly_id = f"provenance-{uuid4().hex}"
            revised_asset = revised_asset.model_copy(
                update={"creation_operation_id": assembly_id}
            )
            assembly_provenance = ProvenanceRecord(
                provenance_id=assembly_id,
                operation="assemble_batch_frame_revision",
                created_at=datetime.now(UTC),
                input_asset_ids=(
                    source_video_asset_id,
                    *(item.asset_id for item in replacements),
                ),
                output_asset_ids=(revised_asset.asset_id,),
                parameters={
                    "frame_indices": list(selection.frame_indices),
                    "generation_fps": source_revision.source_request.generation_fps,
                },
                runtime={"ffmpeg": self._session.project.project_settings.ffmpeg_executable},
            )
            project_with_originals = Wan2LabProject.model_validate(
                self._session.project.model_copy(
                    update={
                        "assets": (*self._session.project.assets, *originals),
                        "generation_records": (
                            *self._session.project.generation_records,
                            *extraction_provenance,
                        ),
                    }
                ).model_dump()
            )
            self._session.project = commit_frame_edit_revision(
                project_with_originals,
                segment_id=str(context["segment_id"]),
                source_revision_id=source_revision_id,
                edit_records=tuple(edit_records),
                replacement_assets=tuple(replacements),
                revised_video_asset=revised_asset,
                provenance=(*edit_provenance, assembly_provenance),
                assembly_provenance_id=assembly_id,
                new_revision_id=f"{source_revision.segment_id}-revision-{uuid4().hex}",
            )
        except Exception as error:
            self._set_status(f"Batch frame modification registration failed: {error}")
            return
        self._append_event(
            f"Batch-{'refined identity in' if is_face_refinement else 'edited'} "
            f"{len(selection.frame_indices)} frames as one immutable revision"
        )
        if is_face_refinement:
            self._face_batch_draft = None
        self._set_status("Batch-modified segment ready for mandatory review")
        self.projectChanged.emit()

    @Slot(str)
    def _fail_batch_frame_modification(self, message: str) -> None:
        self._active_batch_frame_edit = None
        self._append_event(message)
        self._set_status(message)

    @Slot(str)
    def _complete_export(self, output_path: str) -> None:
        plan = self._active_export_plan
        self._active_export_plan = None
        if plan is None:
            self._set_status("Export completed without an active plan")
            return
        try:
            plan_revision_ids = {item.revision_id for item in plan.segment_inputs}
            approved_revisions = tuple(
                revision
                for revision in self._session.project.segment_revisions
                if revision.revision_id in plan_revision_ids
            )
            parent_ids = tuple(
                revision.result_asset_id
                for revision in approved_revisions
                if revision.result_asset_id is not None
            )
            record = self._asset_store.register_generated(
                Path(output_path),
                media_type="video/mp4",
                parent_asset_ids=parent_ids,
                metadata={"export_id": plan.export_id, "output_fps": plan.output_fps},
            )
            output_asset = AssetRef(
                asset_id=record.asset_id,
                kind=AssetKind.VIDEO,
                storage_path=record.relative_path,
                sha256=record.sha256,
                width=self._session.project.project_settings.width,
                height=self._session.project.project_settings.height,
                frame_count=max(
                    1,
                    round(
                        self._session.project.timeline.duration_ms * plan.output_fps / 1000
                    ),
                ),
                duration_ms=self._session.project.timeline.duration_ms,
                parent_asset_ids=record.parent_asset_ids,
                creation_operation_id=plan.provenance_id,
                immutable_source=False,
            )
            provenance = ProvenanceRecord(
                provenance_id=plan.provenance_id,
                operation="assemble_approved_segments",
                created_at=datetime.now(UTC),
                parameters={
                    "output_fps": plan.output_fps,
                    "output_frame_count": output_asset.frame_count,
                    "segment_inputs": [
                        item.model_dump(mode="json") for item in plan.segment_inputs
                    ],
                    "fps_conversion": [
                        item.model_dump(mode="json") for item in plan.fps_plans
                    ],
                    "ffmpeg_commands": [
                        item.model_dump(mode="json") for item in plan.commands
                    ],
                },
                input_asset_ids=parent_ids,
                output_asset_ids=(output_asset.asset_id,),
                parent_provenance_ids=tuple(
                    item.provenance_id
                    for item in approved_revisions
                    if item.provenance_id is not None
                ),
                runtime={
                    "ffmpeg": self._session.project.project_settings.ffmpeg_executable,
                    "memory_policy": self._session.project.project_settings.memory_policy,
                },
            )
            complete = plan.model_copy(update={"state": ExportState.COMPLETE})
            updated = self._session.project.model_copy(
                update={
                    "assets": (*self._session.project.assets, output_asset),
                    "generation_records": (
                        *self._session.project.generation_records,
                        provenance,
                    ),
                    "exports": (*self._session.project.exports, complete),
                }
            )
            self._session.project = Wan2LabProject.model_validate(updated.model_dump())
        except Exception as error:
            self._set_status(f"Export registration failed: {error}")
            return
        self._append_event(f"Export completed and registered as {output_asset.asset_id}")
        self._set_status(f"Export complete: {output_path}")
        self.projectChanged.emit()

    @Slot(str)
    def _fail_export(self, message: str) -> None:
        self._active_export_plan = None
        self._append_event(message)
        self._set_status(message)

    @staticmethod
    def _parse_parameter_value(descriptor: dict[str, object], value: str) -> object:
        parameter_type = str(descriptor.get("parameter_type", "string"))
        if parameter_type == "integer":
            parsed: object = int(value)
        elif parameter_type == "number":
            parsed = float(value)
        elif parameter_type == "boolean":
            normalized = value.strip().lower()
            if normalized not in {"true", "false", "1", "0", "yes", "no"}:
                raise ValueError("boolean values must be true or false")
            parsed = normalized in {"true", "1", "yes"}
        elif parameter_type == "enum":
            choices = tuple(descriptor.get("choices", ()))
            match = next((item for item in choices if str(item) == value), None)
            if match is None:
                raise ValueError(f"value must be one of: {', '.join(map(str, choices))}")
            parsed = match
        else:
            parsed = value

        if isinstance(parsed, (int, float)) and not isinstance(parsed, bool):
            minimum = descriptor.get("minimum")
            maximum = descriptor.get("maximum")
            if minimum is not None and parsed < float(minimum):
                raise ValueError(f"value must be at least {minimum}")
            if maximum is not None and parsed > float(maximum):
                raise ValueError(f"value must be at most {maximum}")
        return parsed

    def _set_status(self, value: str) -> None:
        self._status = value
        self.statusChanged.emit()

    def _append_event(self, value: str) -> None:
        self._events.append(value)
        self.eventLogChanged.emit()


__all__ = ["DesktopController"]
