"""Thin Qt adapter over the authoritative wan2core session."""

from __future__ import annotations

from datetime import UTC, datetime
import json
import math
from pathlib import Path
import tempfile
from uuid import uuid4

from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot

from k2core import __version__ as k2core_version
from wan2core import __version__ as wan2core_version
from wan2core.assets import AssetKind, AssetRef
from wan2core.backends.mock import MockWanBackend, default_mock_capabilities
from wan2core.characters import (
    AppearanceProfile,
    CharacterIdentity,
    CharacterSheet,
    PoseViewEntry,
    PoseViewSource,
)
from wan2core.keyframes import Keyframe, KeyframeSource
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
from wan2core.segments import SegmentState
from wan2core.timeline import Timeline
from wan2core.provenance import ProvenanceRecord
from wan2core.workers import (
    CapabilitiesEvent,
    ErrorEvent,
    InspectCapabilitiesRequest,
    ModelsEvent,
    RuntimeStatusEvent,
)
from wan2lab.backends.comfyui import BACKEND_ID
from wan2lab.assets import LocalAssetStore
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
        self._project_name = "Untitled Wan2Lab Project"
        self._status = "Ready — plan the timeline to begin"
        self._events: list[str] = []
        self._mannequin_preview_url = QUrl()
        self._mannequin_preview_revision = 0
        self._wan_worker = WanWorkerProcess(self)
        self._wan_worker.eventReceived.connect(self._handle_worker_event)
        self._wan_worker.transportError.connect(self._handle_worker_transport_error)
        self._backend_status = "Local ComfyUI backend not inspected"
        self._backend_models: list[str] = []
        self._backend_parameters: list[str] = []

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

    @Property("QStringList", notify=statusChanged)
    def backendModels(self) -> list[str]:  # noqa: N802
        return list(self._backend_models)

    @Property("QStringList", notify=statusChanged)
    def backendParameters(self) -> list[str]:  # noqa: N802
        return list(self._backend_parameters)

    @Slot(float)
    def newProject(self, duration_seconds: float = 18.0) -> None:  # noqa: N802
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
    def closeWorker(self) -> None:  # noqa: N802
        self._wan_worker.close()

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
            record = self._asset_store.register_imported(
                Path(source_url.toLocalFile()), media_type="image/png"
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
            record = self._asset_store.register_imported(
                Path(source_url.toLocalFile()), media_type="image/png"
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
        try:
            plan = self._session.plan(self._capabilities, model_id="wan-test")
        except Exception as error:
            self._set_status(f"Plan failed: {error}")
            return
        self._append_event(f"Planned {len(plan.segments)} review-gated segment(s)")
        self._set_status("Timeline planned — first segment is ready to generate")
        self.projectChanged.emit()

    @Slot()
    def generateNextMockSegment(self) -> None:  # noqa: N802
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

    @Slot(str)
    def saveProject(self, path: str) -> None:  # noqa: N802
        try:
            save_project(self._session.project, Path(path).expanduser())
        except Exception as error:
            self._set_status(f"Save failed: {error}")
            return
        self._set_status(f"Saved {path}")

    @Slot(str)
    def openProject(self, path: str) -> None:  # noqa: N802
        try:
            project = load_project(Path(path).expanduser())
        except Exception as error:
            self._set_status(f"Open failed: {error}")
            return
        self._session = WanStudioSession(project)
        self._asset_store = LocalAssetStore(Path(path).expanduser().resolve().parent / "assets")
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
        elif isinstance(event, ErrorEvent):
            self._backend_status = event.error.message
            self._append_event(f"Wan worker: {event.error.message}")
        self.statusChanged.emit()

    @Slot(str)
    def _handle_worker_transport_error(self, message: str) -> None:
        self._backend_status = message
        self._append_event(message)
        self.statusChanged.emit()

    def _set_status(self, value: str) -> None:
        self._status = value
        self.statusChanged.emit()

    def _append_event(self, value: str) -> None:
        self._events.append(value)
        self.eventLogChanged.emit()


__all__ = ["DesktopController"]
