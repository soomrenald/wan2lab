"""Non-blocking Qt execution of an immutable FFmpeg export plan."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QProcess, Signal, Slot

from wan2core.export import ExportPlan


class ExportProcessRunner(QObject):
    progress = Signal(str, int, int)
    completed = Signal(str)
    failed = Signal(str)
    runningChanged = Signal(bool)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._process = QProcess(self)
        self._plan: ExportPlan | None = None
        self._command_index = 0
        self._stderr = bytearray()
        self._process.readyReadStandardError.connect(self._read_stderr)
        self._process.finished.connect(self._command_finished)

    @property
    def running(self) -> bool:
        return self._plan is not None

    def start(self, plan: ExportPlan) -> None:
        if self.running:
            raise RuntimeError("an export is already running")
        self._write_manifest(plan)
        self._plan = plan
        self._command_index = 0
        self._stderr.clear()
        self.runningChanged.emit(True)
        self._start_current()

    @Slot()
    def cancel(self) -> None:
        if not self.running:
            return
        self._process.kill()
        self._plan = None
        self.runningChanged.emit(False)
        self.failed.emit("Export cancelled")

    def _start_current(self) -> None:
        assert self._plan is not None
        command = self._plan.commands[self._command_index]
        Path(command.output_path).parent.mkdir(parents=True, exist_ok=True)
        self.progress.emit(command.stage, self._command_index, len(self._plan.commands))
        self._process.start(command.arguments[0], list(command.arguments[1:]))

    def _command_finished(self, exit_code: int, _status) -> None:
        if self._plan is None:
            return
        if exit_code != 0:
            message = self._stderr.decode("utf-8", errors="replace").strip()[-4_000:]
            self._plan = None
            self.runningChanged.emit(False)
            self.failed.emit(f"FFmpeg export failed ({exit_code}): {message}")
            return
        self._command_index += 1
        if self._command_index < len(self._plan.commands):
            self._start_current()
            return
        output = Path(self._plan.output_path)
        if not output.is_file() or output.stat().st_size == 0:
            self._plan = None
            self.runningChanged.emit(False)
            self.failed.emit("FFmpeg completed without creating the export")
            return
        output_path = str(output)
        self.progress.emit("complete", self._command_index, self._command_index)
        self._plan = None
        self.runningChanged.emit(False)
        self.completed.emit(output_path)

    def _read_stderr(self) -> None:
        self._stderr.extend(bytes(self._process.readAllStandardError()))

    @staticmethod
    def _write_manifest(plan: ExportPlan) -> None:
        concat = plan.commands[-1]
        try:
            manifest = Path(concat.arguments[concat.arguments.index("-i") + 1])
        except (ValueError, IndexError) as error:
            raise ValueError("concat command does not identify its manifest") from error
        manifest.parent.mkdir(parents=True, exist_ok=True)
        lines = []
        for entry in plan.concat_manifest_entries:
            if "\n" in entry or "\r" in entry:
                raise ValueError("concat paths cannot contain newlines")
            escaped = entry.replace("\\", "/").replace("'", "'\\''")
            lines.append(f"file '{escaped}'\n")
        manifest.write_text("".join(lines), encoding="utf-8")


__all__ = ["ExportProcessRunner"]
