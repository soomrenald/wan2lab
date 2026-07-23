"""Thin Qt adapter over the authoritative wan2core session."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
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
from wan2lab.assets import LocalAssetStore


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

    @Slot(float)
    def newProject(self, duration_seconds: float = 18.0) -> None:  # noqa: N802
        duration_ms = max(1_000, round(duration_seconds * 1000))
        self._session = self._new_session(duration_ms)
        self._asset_store = self._store_for_project(self._session.project.project_id)
        self._project_name = "Untitled Wan2Lab Project"
        self._events.clear()
        self._set_status("New project ready")
        self.projectChanged.emit()
        self.eventLogChanged.emit()

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

    def _set_status(self, value: str) -> None:
        self._status = value
        self.statusChanged.emit()

    def _append_event(self, value: str) -> None:
        self._events.append(value)
        self.eventLogChanged.emit()


__all__ = ["DesktopController"]
