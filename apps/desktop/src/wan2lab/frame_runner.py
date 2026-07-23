"""Non-blocking FFmpeg runner for one immutable frame-modification revision."""

from __future__ import annotations

from pathlib import Path

from PIL import Image
from PySide6.QtCore import QObject, QProcess, Signal, Slot

from wan2core.editing.workflows import FrameExtractionPlan, FrameRevisionAssemblyPlan


class FrameModificationProcessRunner(QObject):
    progress = Signal(str, int, int)
    completed = Signal(str, str, str)
    failed = Signal(str)
    runningChanged = Signal(bool)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._process = QProcess(self)
        self._extraction: FrameExtractionPlan | None = None
        self._assembly: FrameRevisionAssemblyPlan | None = None
        self._replacement_source: Path | None = None
        self._staged_replacement: Path | None = None
        self._phase = 0
        self._stderr = bytearray()
        self._process.readyReadStandardError.connect(self._read_stderr)
        self._process.finished.connect(self._command_finished)

    @property
    def running(self) -> bool:
        return self._assembly is not None

    def start(
        self,
        extraction: FrameExtractionPlan,
        assembly: FrameRevisionAssemblyPlan,
        *,
        replacement_source: Path,
    ) -> None:
        if self.running:
            raise RuntimeError("a frame modification is already running")
        source = replacement_source.expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(source)
        if len(assembly.replacements) != 1:
            raise ValueError("desktop single-frame modification requires one replacement")
        staged = Path(assembly.replacements[0].source_path)
        staged.parent.mkdir(parents=True, exist_ok=True)
        Path(extraction.output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(assembly.frame_directory).mkdir(parents=True, exist_ok=True)
        Path(assembly.output_path).parent.mkdir(parents=True, exist_ok=True)
        with Image.open(source) as image:
            image.save(staged, format="PNG")
        self._extraction = extraction
        self._assembly = assembly
        self._replacement_source = source
        self._staged_replacement = staged
        self._phase = 0
        self._stderr.clear()
        self.runningChanged.emit(True)
        self._start_command(extraction.arguments, "extract_selected", 0)

    @Slot()
    def cancel(self) -> None:
        if not self.running:
            return
        self._process.kill()
        self._clear()
        self.failed.emit("Frame modification cancelled")

    def _start_command(self, arguments: tuple[str, ...], stage: str, current: int) -> None:
        self.progress.emit(stage, current, 3)
        self._process.start(arguments[0], list(arguments[1:]))

    def _command_finished(self, exit_code: int, _status) -> None:
        if self._assembly is None or self._extraction is None:
            return
        if exit_code != 0:
            message = self._stderr.decode("utf-8", errors="replace").strip()[-4_000:]
            self._clear()
            self.failed.emit(f"FFmpeg frame modification failed ({exit_code}): {message}")
            return
        self._phase += 1
        if self._phase == 1:
            self._start_command(self._assembly.extract_arguments, "extract_sequence", 1)
            return
        if self._phase == 2:
            for replacement in self._assembly.replacements:
                destination = Path(replacement.destination_path)
                destination.parent.mkdir(parents=True, exist_ok=True)
                with Image.open(replacement.source_path) as image:
                    image.save(destination, format="PNG")
            self._start_command(self._assembly.encode_arguments, "encode_revision", 2)
            return
        output = Path(self._assembly.output_path)
        original = Path(self._extraction.output_path)
        staged = self._staged_replacement
        if (
            staged is None
            or not original.is_file()
            or not output.is_file()
            or output.stat().st_size == 0
        ):
            self._clear()
            self.failed.emit("FFmpeg completed without all immutable frame outputs")
            return
        values = (str(original), str(staged), str(output))
        self.progress.emit("complete", 3, 3)
        self._clear()
        self.completed.emit(*values)

    def _clear(self) -> None:
        self._extraction = None
        self._assembly = None
        self._replacement_source = None
        self._staged_replacement = None
        self._phase = 0
        self.runningChanged.emit(False)

    def _read_stderr(self) -> None:
        self._stderr.extend(bytes(self._process.readAllStandardError()))


__all__ = ["FrameModificationProcessRunner"]
