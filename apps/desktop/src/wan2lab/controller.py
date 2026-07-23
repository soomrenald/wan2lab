"""Thin Qt adapter over the authoritative wan2core session."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import Property, QObject, Signal, Slot

from k2core import __version__ as k2core_version
from wan2core import __version__ as wan2core_version
from wan2core.backends.mock import MockWanBackend, default_mock_capabilities
from wan2core.orchestration import ReviewGateBlocked, WanStudioSession
from wan2core.projects import (
    ProjectSettings,
    Wan2LabProject,
    load_project,
    save_project,
)
from wan2core.segments import SegmentState
from wan2core.timeline import Timeline


class DesktopController(QObject):
    projectChanged = Signal()
    statusChanged = Signal()
    eventLogChanged = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._capabilities = default_mock_capabilities()
        self._backend = MockWanBackend(self._capabilities)
        self._session = self._new_session(18_000)
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

    @Slot(float)
    def newProject(self, duration_seconds: float = 18.0) -> None:  # noqa: N802
        duration_ms = max(1_000, round(duration_seconds * 1000))
        self._session = self._new_session(duration_ms)
        self._project_name = "Untitled Wan2Lab Project"
        self._events.clear()
        self._set_status("New project ready")
        self.projectChanged.emit()
        self.eventLogChanged.emit()

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

    def _set_status(self, value: str) -> None:
        self._status = value
        self.statusChanged.emit()

    def _append_event(self, value: str) -> None:
        self._events.append(value)
        self.eventLogChanged.emit()


__all__ = ["DesktopController"]

