"""Thin Qt adapter over the authoritative wan2core session."""

from __future__ import annotations

from datetime import UTC, datetime
import json
import math
from pathlib import Path
import tempfile
from uuid import uuid4

from PIL import Image
from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot

from k2core import __version__ as k2core_version
from wan2core import __version__ as wan2core_version
from wan2core.assets import AssetKind, AssetRef
from wan2core.backends import BackendCapabilities, WanMode
from wan2core.backends.mock import MockWanBackend, default_mock_capabilities
from wan2core.characters import (
    AppearanceProfile,
    CharacterIdentity,
    CharacterSheet,
    PoseViewEntry,
    PoseViewSource,
)
from wan2core.export import ExportPlan, ExportState, build_export_plan
from wan2core.editing import BoundaryPropagation, FrameEditOperation, FrameEditRecord
from wan2core.editing.workflows import (
    commit_frame_edit_revision,
    plan_frame_extraction,
    plan_frame_revision_assembly,
)
from wan2core.keyframes import Keyframe, KeyframeSource
from wan2core.keyframes.generation import CharacterSheetImageRequest
from wan2core.keyframes.workflows import add_timeline_keyframe, register_pose_view_entry
from wan2core.mannequin import JointPose, Quaternion
from wan2core.mannequin.workflows import (
    GuideKind,
    KreaMannequinCapabilities,
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
from wan2core.projects.invalidation import change_output_fps
from wan2core.segments import SegmentState
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
    ReleaseWanModelRequest,
    ResultEvent,
    RuntimeStatusEvent,
)
from wan2lab.backends.comfyui import BACKEND_ID
from wan2lab.assets import LocalAssetStore, LocalComfyAssetBridge, image_media_type
from wan2lab.export_runner import ExportProcessRunner
from wan2lab.frame_runner import FrameModificationProcessRunner
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
        self._status = "Ready — plan the timeline to begin"
        self._events: list[str] = []
        self._mannequin_preview_url = QUrl()
        self._mannequin_preview_revision = 0
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
        self._backend_parameters: list[str] = []
        self._backend_parameter_descriptors: list[dict[str, object]] = []
        self._backend_vae_models: list[str] = []
        self._backend_text_encoder_models: list[str] = []
        self._inspected_capabilities: BackendCapabilities | None = None
        self._selected_wan_model_id: str | None = None
        self._pending_model_command_id: str | None = None
        self._pending_wan_model_id: str | None = None
        self._active_wan_commands: dict[str, str] = {}
        self._active_wan_jobs: dict[str, str] = {}
        self._krea_status = "Local Krea worker not inspected"
        self._krea_loaded = False
        self._krea_load_command_id: str | None = None
        self._pending_krea_jobs: dict[str, dict[str, object]] = {}
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

    @Property(str, notify=projectChanged)
    def projectName(self) -> str:  # noqa: N802 - Qt property naming
        return self._project_name

    @Property(float, notify=projectChanged)
    def durationSeconds(self) -> float:  # noqa: N802
        return self._session.project.timeline.duration_ms / 1000.0

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
        return self._frame_runner.running

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
    def sheetEntryNames(self) -> list[str]:  # noqa: N802
        return [
            f"{sheet.name} · {entry.name}"
            for sheet in self._session.project.character_sheets
            for entry in sheet.entries
        ]

    @Property("QStringList", notify=projectChanged)
    def keyframeLabels(self) -> list[str]:  # noqa: N802
        return [
            f"{keyframe.time_ms / 1000:g}s · {keyframe.source_type.value}"
            for keyframe in self._session.project.keyframes
        ]

    @Property("QStringList", notify=projectChanged)
    def mannequinNames(self) -> list[str]:  # noqa: N802
        return [item.name for item in self._session.project.mannequin_scenes]

    @Property("QStringList", notify=projectChanged)
    def mannequinPoseNames(self) -> list[str]:  # noqa: N802
        return [item.name for item in self._session.project.mannequin_poses]

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

    @Property(str, notify=projectChanged)
    def mannequinConditioningPath(self) -> str:  # noqa: N802
        if not self._session.project.mannequin_scenes:
            return "No mannequin scene"
        scene = self._session.project.mannequin_scenes[-1]
        by_kind = (
            dict(zip(GuideKind, scene.guide_asset_ids[-3:], strict=True))
            if len(scene.guide_asset_ids) >= 3
            else {}
        )
        try:
            plan = plan_krea_conditioning(
                scene=scene,
                capabilities=KreaMannequinCapabilities(supports_i2i=True),
                guide_assets=by_kind,
            )
        except ValueError:
            return "Render guides to enable Krea conditioning"
        return f"{plan.path.value}: {plan.explanation}"

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

    @Property("QVariantList", notify=statusChanged)
    def backendParameterDescriptors(self) -> list[dict[str, object]]:  # noqa: N802
        return list(self._backend_parameter_descriptors)

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
        if self._active_wan_commands or self._frame_runner.running:
            self._set_status("Cancel active generation or modification before creating a project")
            return
        duration_ms = max(1_000, round(duration_seconds * 1000))
        self._session = self._new_session(duration_ms)
        self._asset_store = self._store_for_project(self._session.project.project_id)
        self._project_name = "Untitled Wan2Lab Project"
        self._events.clear()
        self._mannequin_preview_url = QUrl()
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
        if not self._krea_loaded:
            self._set_status("Load the local Krea backend before generating a sheet entry")
            return
        if not self._session.project.character_sheets:
            self._set_status("Create a character before generating a sheet entry")
            return
        sheet = self._session.project.character_sheets[0]
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

    @Slot()
    def closeWorker(self) -> None:  # noqa: N802
        self._wan_worker.close()
        self._krea_worker.close()
        self._export_runner.cancel()
        self._frame_runner.cancel()

    @Slot(str)
    def createMannequinScene(self, name: str) -> None:  # noqa: N802
        scene = default_mannequin_scene(
            scene_id=f"mannequin-scene-{uuid4().hex}",
            name=name.strip() or "Untitled pose",
            width=self._session.project.project_settings.width,
            height=self._session.project.project_settings.height,
        )
        self._session.project = save_mannequin_scene(self._session.project, scene)
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
        instance = scene.instances[0]
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
            update={"instances": (changed_instance, *scene.instances[1:])}
        )
        self._session.project = save_mannequin_scene(self._session.project, changed_scene)
        self._refresh_mannequin_preview()
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

    @Slot(str)
    def saveCurrentMannequinPose(self, name: str) -> None:  # noqa: N802
        if not self._session.project.mannequin_scenes:
            self._set_status("Create a mannequin scene first")
            return
        scene = self._session.project.mannequin_scenes[-1]
        pose = save_pose_from_instance(
            scene.instances[0],
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

    @Slot(QUrl, str)
    def importSheetEntry(self, source_url: QUrl, name: str) -> None:  # noqa: N802
        if not self._session.project.character_sheets:
            self._set_status("Create a character before importing a sheet entry")
            return
        try:
            source = Path(source_url.toLocalFile())
            record = self._asset_store.register_imported(
                source, media_type=image_media_type(source)
            )
            asset = self._wan_asset(record, AssetKind.IMAGE)
            sheet = self._session.project.character_sheets[0]
            provenance = ProvenanceRecord(
                provenance_id=f"provenance-{uuid4().hex}",
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

    @Slot(QUrl, float)
    def importKeyframe(self, source_url: QUrl, time_seconds: float) -> None:  # noqa: N802
        try:
            source = Path(source_url.toLocalFile())
            record = self._asset_store.register_imported(
                source, media_type=image_media_type(source)
            )
            asset = self._wan_asset(record, AssetKind.IMAGE)
            provenance = ProvenanceRecord(
                provenance_id=f"provenance-{uuid4().hex}",
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
            in {SegmentState.REJECTED, SegmentState.ERROR, SegmentState.CANCELLED}
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

    @Slot()
    def cancelFrameModification(self) -> None:  # noqa: N802
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
            model = self._capabilities.model(current.model_id)
            if selected_mode not in model.supported_modes:
                raise ValueError(f"Mode {selected_mode.value} is not supported by the segment model")
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
            self._session.project = Wan2LabProject.model_validate(
                self._session.project.model_copy(update={"segments": segments}).model_dump()
            )
        except Exception as error:
            self._set_status(f"Segment update failed: {error}")
            return
        self._append_event(f"Updated segment {segment.segment_id} inspector settings")
        self._set_status("Segment prompt and mode updated")
        self.projectChanged.emit()

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
            self._session.project = Wan2LabProject.model_validate(
                self._session.project.model_copy(update={"segments": segments}).model_dump()
            )
        except Exception as error:
            self._set_status(f"Parameter update failed: {error}")
            return
        self._set_status(f"Set {key}={parsed}")
        self.projectChanged.emit()

    @Slot(QUrl)
    def exportApprovedVideo(self, url: QUrl) -> None:  # noqa: N802
        if self._export_runner.running:
            self._set_status("An export is already running")
            return
        output = Path(url.toLocalFile()).expanduser().resolve()
        if not output.suffix:
            output = output.with_suffix(".mp4")
        try:
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
                ffmpeg_executable=self._session.project.project_settings.ffmpeg_executable,
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
        if self._active_wan_commands or self._frame_runner.running:
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
        self._events.clear()
        self._refresh_mannequin_preview()
        self._set_status(f"Opened {path}")
        self.projectChanged.emit()
        self.eventLogChanged.emit()

    @property
    def session(self) -> WanStudioSession:
        return self._session

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
                creation_operation_id=revision.source_request.request_id,
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
            self._append_event("Krea model loaded in isolated worker")
            self._set_status("Krea ready for character sheets, keyframes, and frame edits")
        elif state == "complete" and command_id in self._pending_krea_jobs:
            self._complete_krea_job(command_id, payload)
        elif state in {"error", "cancelled"}:
            self._pending_krea_jobs.pop(command_id, None)
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
            raw_paths = payload.get("asset_paths", ())
            if not isinstance(raw_paths, list) or len(raw_paths) != 1:
                raise ValueError("Krea job must return exactly one image")
            source = Path(str(raw_paths[0])).expanduser().resolve()
            if self._krea_result_root not in source.parents or not source.is_file():
                raise ValueError("Krea worker returned an output outside its result root")
            record = self._asset_store.register_generated(
                source,
                media_type=image_media_type(source),
                metadata={
                    "worker_command_id": command_id,
                    "operation": str(context["operation"]),
                },
            )
            asset = self._wan_asset(record, AssetKind.IMAGE)
            provenance = ProvenanceRecord(
                provenance_id=f"provenance-{uuid4().hex}",
                operation="krea_generate_character_sheet_entry",
                created_at=datetime.now(UTC),
                model_identifiers=("krea2",),
                backend_id="krea-comfyui",
                parameters={"request": context["request"]},
                output_asset_ids=(asset.asset_id,),
                runtime={"worker_payload": payload.get("metadata", {})},
            )
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
        except Exception as error:
            self._set_status(f"Krea result registration failed: {error}")
            return
        self._append_event(f"Generated character-sheet entry {entry.name} with Krea")
        self._set_status("Generated entry saved as an immutable draft for review")
        self.projectChanged.emit()

    @Slot(str)
    def _handle_krea_transport_error(self, message: str) -> None:
        self._krea_status = message
        self._krea_loaded = False
        self._krea_load_command_id = None
        self._pending_krea_jobs.clear()
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
            edit = FrameEditRecord(
                edit_id=f"frame-edit-{uuid4().hex}",
                segment_revision_id=source_revision_id,
                original_frame_asset_id=original_asset.asset_id,
                replacement_frame_asset_id=replacement_asset.asset_id,
                frame_index=frame_index,
                operation_type=FrameEditOperation.IMAGE_EDIT,
                prompt=str(context["prompt"]),
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
                    operation="import_frame_replacement",
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

    @Slot(str)
    def _complete_export(self, output_path: str) -> None:
        plan = self._active_export_plan
        self._active_export_plan = None
        if plan is None:
            self._set_status("Export completed without an active plan")
            return
        try:
            parent_ids = tuple(
                revision.result_asset_id
                for revision in self._session.project.segment_revisions
                if revision.review_state.value == "approved" and revision.result_asset_id is not None
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
            )
            provenance = ProvenanceRecord(
                provenance_id=plan.provenance_id,
                operation="assemble_approved_segments",
                created_at=datetime.now(UTC),
                parameters={"output_fps": plan.output_fps},
                input_asset_ids=parent_ids,
                output_asset_ids=(output_asset.asset_id,),
                runtime={"ffmpeg": self._session.project.project_settings.ffmpeg_executable},
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
