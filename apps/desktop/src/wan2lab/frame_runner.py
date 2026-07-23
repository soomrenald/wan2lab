"""Non-blocking FFmpeg runner for one immutable frame-modification revision."""

from __future__ import annotations

from pathlib import Path

from PIL import Image
from PySide6.QtCore import QObject, QProcess, Signal, Slot

from wan2core.editing.workflows import FrameExtractionPlan, FrameRevisionAssemblyPlan


class FrameExtractionProcessRunner(QObject):
    completed = Signal(str)
    failed = Signal(str)
    runningChanged = Signal(bool)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._process = QProcess(self)
        self._plan: FrameExtractionPlan | None = None
        self._stderr = bytearray()
        self._process.readyReadStandardError.connect(self._read_stderr)
        self._process.finished.connect(self._finished)

    @property
    def running(self) -> bool:
        return self._plan is not None

    def start(self, plan: FrameExtractionPlan) -> None:
        if self.running:
            raise RuntimeError("a Krea source-frame extraction is already running")
        Path(plan.output_path).parent.mkdir(parents=True, exist_ok=True)
        self._plan = plan
        self._stderr.clear()
        self.runningChanged.emit(True)
        self._process.start(plan.arguments[0], list(plan.arguments[1:]))

    @Slot()
    def cancel(self) -> None:
        if not self.running:
            return
        self._process.kill()
        self._plan = None
        self.runningChanged.emit(False)
        self.failed.emit("Krea source-frame extraction cancelled")

    def _finished(self, exit_code: int, _status) -> None:
        plan = self._plan
        if plan is None:
            return
        self._plan = None
        self.runningChanged.emit(False)
        if exit_code != 0:
            message = self._stderr.decode("utf-8", errors="replace").strip()[-4_000:]
            self.failed.emit(f"FFmpeg frame extraction failed ({exit_code}): {message}")
            return
        output = Path(plan.output_path)
        if not output.is_file() or output.stat().st_size == 0:
            self.failed.emit("FFmpeg completed without extracting the Krea source frame")
            return
        self.completed.emit(str(output))

    def _read_stderr(self) -> None:
        self._stderr.extend(bytes(self._process.readAllStandardError()))


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


class BatchFrameModificationProcessRunner(QObject):
    progress = Signal(str, int, int)
    completed = Signal(object, object, str)
    failed = Signal(str)
    runningChanged = Signal(bool)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._process = QProcess(self)
        self._extractions: tuple[FrameExtractionPlan, ...] = ()
        self._assembly: FrameRevisionAssemblyPlan | None = None
        self._staged_replacements: tuple[Path, ...] = ()
        self._command_index = 0
        self._stderr = bytearray()
        self._process.readyReadStandardError.connect(self._read_stderr)
        self._process.finished.connect(self._command_finished)

    @property
    def running(self) -> bool:
        return self._assembly is not None

    def start(
        self,
        extractions: tuple[FrameExtractionPlan, ...],
        assembly: FrameRevisionAssemblyPlan,
        *,
        replacement_sources: tuple[Path, ...],
    ) -> None:
        if self.running:
            raise RuntimeError("a batch frame modification is already running")
        if not extractions or len(extractions) != len(assembly.replacements):
            raise ValueError("batch modification requires one extraction per replacement")
        if len(replacement_sources) != len(assembly.replacements):
            raise ValueError("batch modification replacement count differs from the plan")
        staged = []
        for source, replacement in zip(
            replacement_sources,
            assembly.replacements,
            strict=True,
        ):
            resolved = source.expanduser().resolve()
            if not resolved.is_file():
                raise FileNotFoundError(resolved)
            destination = Path(replacement.source_path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with Image.open(resolved) as image:
                image.save(destination, format="PNG")
            staged.append(destination)
        for extraction in extractions:
            Path(extraction.output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(assembly.frame_directory).mkdir(parents=True, exist_ok=True)
        Path(assembly.output_path).parent.mkdir(parents=True, exist_ok=True)
        self._extractions = extractions
        self._assembly = assembly
        self._staged_replacements = tuple(staged)
        self._command_index = 0
        self._stderr.clear()
        self.runningChanged.emit(True)
        self._start_current()

    @Slot()
    def cancel(self) -> None:
        if not self.running:
            return
        self._process.kill()
        self._clear()
        self.failed.emit("Batch frame modification cancelled")

    def _start_current(self) -> None:
        assert self._assembly is not None
        extraction_count = len(self._extractions)
        if self._command_index < extraction_count:
            arguments = self._extractions[self._command_index].arguments
            stage = "extract_selected"
        elif self._command_index == extraction_count:
            arguments = self._assembly.extract_arguments
            stage = "extract_sequence"
        else:
            for replacement in self._assembly.replacements:
                destination = Path(replacement.destination_path)
                destination.parent.mkdir(parents=True, exist_ok=True)
                with Image.open(replacement.source_path) as image:
                    image.save(destination, format="PNG")
            arguments = self._assembly.encode_arguments
            stage = "encode_revision"
        total = extraction_count + 2
        self.progress.emit(stage, self._command_index, total)
        self._process.start(arguments[0], list(arguments[1:]))

    def _command_finished(self, exit_code: int, _status) -> None:
        if self._assembly is None:
            return
        if exit_code != 0:
            message = self._stderr.decode("utf-8", errors="replace").strip()[-4_000:]
            self._clear()
            self.failed.emit(f"FFmpeg batch modification failed ({exit_code}): {message}")
            return
        self._command_index += 1
        if self._command_index < len(self._extractions) + 2:
            self._start_current()
            return
        originals = tuple(Path(item.output_path) for item in self._extractions)
        output = Path(self._assembly.output_path)
        if (
            any(not item.is_file() for item in originals)
            or not output.is_file()
            or output.stat().st_size == 0
        ):
            self._clear()
            self.failed.emit("FFmpeg completed without all immutable batch outputs")
            return
        values = (
            tuple(str(item) for item in originals),
            tuple(str(item) for item in self._staged_replacements),
            str(output),
        )
        self._clear()
        self.completed.emit(*values)

    def _clear(self) -> None:
        self._extractions = ()
        self._assembly = None
        self._staged_replacements = ()
        self._command_index = 0
        self.runningChanged.emit(False)

    def _read_stderr(self) -> None:
        self._stderr.extend(bytes(self._process.readAllStandardError()))


__all__ = [
    "BatchFrameModificationProcessRunner",
    "FrameExtractionProcessRunner",
    "FrameModificationProcessRunner",
]
