"""Non-blocking Qt client for the isolated Wan JSON-lines worker."""

from __future__ import annotations

import json
import sys

from PySide6.QtCore import QObject, QProcess, Signal

from wan2core.workers import WanWorkerEvent, WanWorkerRequest, parse_worker_event


class JsonLineDecoder:
    def __init__(self) -> None:
        self._buffer = b""

    def feed(self, data: bytes) -> tuple[WanWorkerEvent, ...]:
        self._buffer += data
        lines = self._buffer.split(b"\n")
        self._buffer = lines.pop()
        events = []
        for line in lines:
            if not line.strip():
                continue
            payload = json.loads(line)
            if not isinstance(payload, dict):
                raise ValueError("worker event must be a JSON object")
            events.append(parse_worker_event(payload))
        return tuple(events)


class WanWorkerProcess(QObject):
    eventReceived = Signal(object)
    transportError = Signal(str)
    runningChanged = Signal(bool)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._process = QProcess(self)
        self._decoder = JsonLineDecoder()
        self._pending: list[bytes] = []
        self._process.readyReadStandardOutput.connect(self._read_stdout)
        self._process.readyReadStandardError.connect(self._read_stderr)
        self._process.started.connect(self._flush_pending)
        self._process.started.connect(lambda: self.runningChanged.emit(True))
        self._process.finished.connect(lambda *_args: self.runningChanged.emit(False))
        self._process.errorOccurred.connect(
            lambda error: self.transportError.emit(f"Wan worker process error: {error}")
        )

    @property
    def running(self) -> bool:
        return self._process.state() is not QProcess.ProcessState.NotRunning

    def send(self, request: WanWorkerRequest) -> None:
        document = request.model_dump_json().encode("utf-8") + b"\n"
        if self._process.state() is QProcess.ProcessState.Running:
            self._process.write(document)
            return
        self._pending.append(document)
        if self._process.state() is QProcess.ProcessState.NotRunning:
            self._process.start(sys.executable, ["-m", "wan2lab.worker"])

    def close(self) -> None:
        if self._process.state() is QProcess.ProcessState.NotRunning:
            return
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
        try:
            events = self._decoder.feed(bytes(self._process.readAllStandardOutput()))
        except Exception as error:
            self.transportError.emit(f"Invalid Wan worker event: {error}")
            return
        for event in events:
            self.eventReceived.emit(event)

    def _read_stderr(self) -> None:
        message = bytes(self._process.readAllStandardError()).decode("utf-8", errors="replace").strip()
        if message:
            self.transportError.emit(message)


__all__ = ["JsonLineDecoder", "WanWorkerProcess"]
