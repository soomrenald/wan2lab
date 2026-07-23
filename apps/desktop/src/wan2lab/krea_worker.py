"""Isolated Krea worker built exclusively on the public k2core backend/runtime."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event, Lock, Thread
from typing import Callable, Mapping, TextIO

from k2core.backends import ComfyKreaBackend
from k2core.config import ModelDirectories
from k2core.model import discover_model_artifacts
from k2core.worker.protocol import CommandKind, WorkerState
from k2core.worker.runtime import ComfyBaselineRuntime, probe_runtime


@dataclass(slots=True)
class KreaCancellation:
    event: Event = field(default_factory=Event)

    @property
    def cancelled(self) -> bool:
        return self.event.is_set()

    def cancel(self) -> None:
        self.event.set()

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise InterruptedError("Krea job cancelled")


RuntimeLoader = Callable[[Mapping[str, object]], tuple[object, dict[str, object]]]


def _default_runtime_loader(payload: Mapping[str, object]) -> tuple[object, dict[str, object]]:
    comfyui_root = Path(str(payload.get("comfyui_root", "~/ComfyUI"))).expanduser().resolve()
    directories = ModelDirectories(
        diffusion_models=Path(
            str(payload.get("diffusion_models", comfyui_root / "models/diffusion_models"))
        ).expanduser(),
        text_encoders=Path(
            str(payload.get("text_encoders", comfyui_root / "models/text_encoders"))
        ).expanduser(),
        vae=Path(str(payload.get("vae", comfyui_root / "models/vae"))).expanduser(),
        diffusion_model_file=(
            Path(str(payload["diffusion_model_file"])).expanduser()
            if payload.get("diffusion_model_file")
            else None
        ),
        text_encoder_file=(
            Path(str(payload["text_encoder_file"])).expanduser()
            if payload.get("text_encoder_file")
            else None
        ),
        vae_file=(
            Path(str(payload["vae_file"])).expanduser()
            if payload.get("vae_file")
            else None
        ),
    )
    artifacts = discover_model_artifacts(directories)
    if not artifacts.complete:
        raise RuntimeError("Krea transformer, Qwen text encoder, and VAE are all required")
    runtime = ComfyBaselineRuntime(comfyui_root)
    loaded = runtime.load(
        artifacts,
        memory_policy_key=str(payload.get("memory_policy", "safe_16gb")),
        reserve_vram_gb=float(payload.get("reserve_vram_gb", 4.0)),
        minimum_system_ram_gb=float(payload.get("minimum_system_ram_gb", 14.0)),
        cpu_vae=bool(payload.get("cpu_vae", False)),
        oom_recovery=True,
    )
    return runtime, loaded


@dataclass(slots=True)
class KreaWorkerService:
    result_root: Path
    runtime_loader: RuntimeLoader = _default_runtime_loader
    runtime: object | None = field(default=None, init=False)
    backend: ComfyKreaBackend | None = field(default=None, init=False)
    _asset_paths: dict[str, Path] = field(default_factory=dict, init=False)

    def probe(self, payload: Mapping[str, object]) -> dict[str, object]:
        comfyui_root = Path(str(payload.get("comfyui_root", "~/ComfyUI"))).expanduser()
        return dict(probe_runtime(comfyui_root))

    def load(self, payload: Mapping[str, object]) -> dict[str, object]:
        if self.backend is not None and bool(getattr(self.runtime, "loaded", False)):
            return {"reused": True, **dict(self.backend.capabilities().metadata)}
        runtime, metadata = self.runtime_loader(payload)
        self.runtime = runtime
        self.backend = ComfyKreaBackend(
            runtime=runtime,
            asset_resolver=self._resolve_asset,
            output_directory=self.result_root.expanduser().resolve(),
            release_callback=self.release,
        )
        capabilities = self.backend.capabilities()
        return {
            **metadata,
            "capabilities": {
                "backend_id": capabilities.backend_id,
                "modes": sorted(capabilities.modes),
                "accelerator_vendors": sorted(capabilities.accelerator_vendors),
                "parameters": list(capabilities.parameters),
                "metadata": dict(capabilities.metadata),
            },
        }

    def execute(
        self,
        kind: CommandKind,
        payload: Mapping[str, object],
        *,
        cancellation: KreaCancellation,
        progress: Callable[[str, float | None, Mapping[str, object]], None],
    ) -> dict[str, object]:
        if self.backend is None:
            raise RuntimeError("load the Krea backend before executing an image job")
        raw_request = payload.get("request")
        if not isinstance(raw_request, Mapping):
            raise ValueError("Krea image command requires a normalized request object")
        self._asset_paths = self._validated_asset_paths(payload.get("asset_paths", {}))
        request = dict(raw_request)
        if kind is CommandKind.GENERATE_BASELINE:
            result = self.backend.generate_image(
                request,
                progress=progress,
                cancellation=cancellation,
            )
        elif kind is CommandKind.EDIT_IMAGE:
            result = self.backend.edit_frame(
                request,
                progress=progress,
                cancellation=cancellation,
            )
        elif kind is CommandKind.REFINE_FACES:
            result = self.backend.refine_faces(
                request,
                progress=progress,
                cancellation=cancellation,
            )
        else:
            raise ValueError(f"unsupported Krea execution command: {kind.value}")
        return {
            "asset_paths": [str(path.expanduser().resolve()) for path in result.asset_paths],
            "metadata": dict(result.metadata),
            "warnings": list(result.warnings),
        }

    def release(self) -> None:
        runtime = self.runtime
        release = getattr(runtime, "release", None)
        if callable(release):
            release()
        elif runtime is not None:
            for name in ("model", "clip", "vae"):
                if hasattr(runtime, name):
                    setattr(runtime, name, None)
        self.runtime = None
        self.backend = None
        self._asset_paths.clear()

    def _resolve_asset(self, asset_id: str) -> Path:
        try:
            return self._asset_paths[asset_id]
        except KeyError as error:
            raise FileNotFoundError(f"Krea asset was not staged: {asset_id}") from error

    @staticmethod
    def _validated_asset_paths(value: object) -> dict[str, Path]:
        if not isinstance(value, Mapping):
            raise ValueError("asset_paths must be an object")
        paths = {}
        for asset_id, raw_path in value.items():
            path = Path(str(raw_path)).expanduser().resolve()
            if not path.is_file():
                raise FileNotFoundError(path)
            paths[str(asset_id)] = path
        return paths


class StdioKreaWorker:
    def __init__(self, service: KreaWorkerService, output: TextIO) -> None:
        self.service = service
        self.output = output
        self._write_lock = Lock()
        self._jobs: dict[str, KreaCancellation] = {}

    def dispatch(self, command: Mapping[str, object]) -> None:
        command_id = str(command.get("command_id", "invalid-command"))
        try:
            kind = CommandKind(str(command["kind"]))
            payload = command.get("payload", {})
            if not isinstance(payload, Mapping):
                raise ValueError("worker payload must be an object")
            if kind in {
                CommandKind.GENERATE_BASELINE,
                CommandKind.EDIT_IMAGE,
                CommandKind.REFINE_FACES,
            }:
                if self._jobs:
                    raise RuntimeError("only one Krea job may run at a time")
                token = KreaCancellation()
                self._jobs[command_id] = token
                Thread(
                    target=self._execute,
                    args=(command_id, kind, payload, token),
                    daemon=True,
                    name=f"wan2lab-krea-{command_id}",
                ).start()
            elif kind is CommandKind.CANCEL:
                target = str(payload.get("command_id", ""))
                token = self._jobs.get(target)
                if token is None:
                    raise KeyError(f"unknown Krea job: {target}")
                token.cancel()
                self.emit(command_id, WorkerState.CANCELLED, "Cancellation requested")
            elif kind is CommandKind.PROBE:
                result = self.service.probe(payload)
                self.emit(command_id, WorkerState.READY, "Krea runtime probe complete", result)
            elif kind is CommandKind.LOAD_MODEL:
                result = self.service.load(payload)
                self.emit(command_id, WorkerState.READY, "Krea model loaded", result)
            elif kind is CommandKind.SHUTDOWN:
                self.service.release()
                self.emit(command_id, WorkerState.UNLOADED, "Krea model released")
            else:
                raise ValueError(f"unsupported Krea worker command: {kind.value}")
        except Exception as error:
            self.emit(command_id, WorkerState.ERROR, f"{type(error).__name__}: {error}")

    def _execute(
        self,
        command_id: str,
        kind: CommandKind,
        payload: Mapping[str, object],
        token: KreaCancellation,
    ) -> None:
        try:
            result = self.service.execute(
                kind,
                payload,
                cancellation=token,
                progress=lambda stage, fraction, detail: self.emit(
                    command_id,
                    WorkerState.RUNNING,
                    stage,
                    {"fraction": fraction, **dict(detail)},
                ),
            )
            self.emit(command_id, WorkerState.COMPLETE, "Krea image job complete", result)
        except InterruptedError as error:
            self.emit(command_id, WorkerState.CANCELLED, str(error))
        except Exception as error:
            self.emit(command_id, WorkerState.ERROR, f"{type(error).__name__}: {error}")
        finally:
            self._jobs.pop(command_id, None)

    def emit(
        self,
        command_id: str,
        state: WorkerState,
        message: str,
        payload: Mapping[str, object] | None = None,
    ) -> None:
        document = json.dumps(
            {
                "command_id": command_id,
                "state": state.value,
                "message": message,
                "payload": dict(payload or {}),
            },
            separators=(",", ":"),
        )
        with self._write_lock:
            self.output.write(document + "\n")
            self.output.flush()


def main() -> int:
    worker = StdioKreaWorker(
        KreaWorkerService(Path("~/.cache/wan2lab/krea-results")),
        sys.stdout,
    )
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
            if not isinstance(payload, dict):
                raise ValueError("worker command must be an object")
            worker.dispatch(payload)
        except Exception as error:
            worker.emit("invalid-command", WorkerState.ERROR, f"{type(error).__name__}: {error}")
    return 0


__all__ = ["KreaCancellation", "KreaWorkerService", "StdioKreaWorker", "main"]
