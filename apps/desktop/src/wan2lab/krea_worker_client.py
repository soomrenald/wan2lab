"""Qt process client for the isolated, accelerator-enabled Krea worker."""

from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Signal

import k2core
from k2core.config import discover_worker_python


class KreaWorkerProcess(QObject):
    eventReceived = Signal(object)
    transportError = Signal(str)
    runningChanged = Signal(bool)

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        comfyui_root: Path | None = None,
        worker_python: Path | None = None,
    ) -> None:
        super().__init__(parent)
        self.comfyui_root = (comfyui_root or Path("~/ComfyUI")).expanduser().resolve()
        self.worker_python = (
            worker_python.absolute()
            if worker_python is not None
            else discover_worker_python(self.comfyui_root)
        )
        self._process = QProcess(self)
        self._buffer = b""
        self._pending: list[bytes] = []
        self._process.readyReadStandardOutput.connect(self._read_stdout)
        self._process.readyReadStandardError.connect(self._read_stderr)
        self._process.started.connect(self._flush_pending)
        self._process.started.connect(lambda: self.runningChanged.emit(True))
        self._process.finished.connect(lambda *_args: self.runningChanged.emit(False))
        self._process.errorOccurred.connect(
            lambda error: self.transportError.emit(f"Krea worker process error: {error}")
        )

    @property
    def running(self) -> bool:
        return self._process.state() is not QProcess.ProcessState.NotRunning

    def send(self, kind: str, payload: dict[str, object] | None = None) -> str:
        command_id = f"krea-{uuid4().hex}"
        document = (
            json.dumps(
                {
                    "command_id": command_id,
                    "kind": kind,
                    "payload": payload or {},
                },
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
        )
        if self._process.state() is QProcess.ProcessState.Running:
            self._process.write(document)
            return command_id
        if not self.worker_python.is_file():
            self.transportError.emit(
                f"Krea worker interpreter is missing: {self.worker_python}"
            )
            return command_id
        self._pending.append(document)
        if self._process.state() is QProcess.ProcessState.NotRunning:
            environment = QProcessEnvironment.systemEnvironment()
            package_root = Path(__file__).resolve().parent.parent
            k2core_root = Path(k2core.__file__).resolve().parent.parent
            existing = environment.value("PYTHONPATH")
            python_path = os.pathsep.join(
                str(item)
                for item in (package_root, k2core_root, existing)
                if str(item)
            )
            environment.insert("PYTHONPATH", python_path)
            environment.insert("VIRTUAL_ENV", str(self.worker_python.parent.parent))
            self._process.setProcessEnvironment(environment)
            self._process.setWorkingDirectory(str(self.comfyui_root))
            self._process.start(str(self.worker_python), ["-m", "wan2lab.krea_worker"])
        return command_id

    def close(self) -> None:
        if self._process.state() is QProcess.ProcessState.NotRunning:
            return
        self.send("shutdown")
        self._process.closeWriteChannel()
        self._process.terminate()
        if not self._process.waitForFinished(1_000):
            self._process.kill()
            self._process.waitForFinished(1_000)

    def _flush_pending(self) -> None:
        for document in self._pending:
            self._process.write(document)
        self._pending.clear()

    def _read_stdout(self) -> None:
        self._buffer += bytes(self._process.readAllStandardOutput())
        lines = self._buffer.split(b"\n")
        self._buffer = lines.pop()
        for line in lines:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
                if not isinstance(event, dict):
                    raise ValueError("event is not an object")
            except Exception as error:
                self.transportError.emit(f"Invalid Krea worker event: {error}")
                continue
            self.eventReceived.emit(event)

    def _read_stderr(self) -> None:
        message = bytes(self._process.readAllStandardError()).decode(
            "utf-8", errors="replace"
        ).strip()
        if message:
            self.transportError.emit(message)


__all__ = ["KreaWorkerProcess"]
