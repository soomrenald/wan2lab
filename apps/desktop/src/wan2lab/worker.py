"""Out-of-process Wan worker using typed JSON-lines contracts."""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass, field
from threading import Event, Lock, Thread
from typing import Callable, Mapping, TextIO

from wan2core.backends import (
    BackendCapabilities,
    WanMode,
    resolve_wan_acceleration,
)
from wan2core.workers import (
    AckEvent,
    CancelRequest,
    CapabilitiesEvent,
    DiscoverModelsRequest,
    ErrorEvent,
    GenerateSegmentRequest,
    InspectCapabilitiesRequest,
    LoadModelRequest,
    ModelsEvent,
    ProgressEvent,
    ReleaseAllModelsRequest,
    ReleaseWanModelRequest,
    ResultEvent,
    RuntimeStatusEvent,
    RuntimeStatusRequest,
    WanWorkerEvent,
    WanWorkerRequest,
    WorkerError,
    WorkerResult,
    parse_worker_request,
)
from wan2lab.backends.comfy_runtime import ComfyWanExecutor, ModelResidencyManager
from wan2lab.backends.comfy_workflow import (
    ComfyModelSelection,
    ComfyWanWorkflowBuilder,
    ModeWorkflowTemplate,
)
from wan2lab.backends.comfyui import (
    BACKEND_ID,
    ComfyUIClient,
    accelerator_vendor,
    inspect_comfyui_wan,
)


class WanOutOfMemory(RuntimeError):
    recovery_actions = (
        "reduce_vae_tile_size",
        "reduce_frame_count",
        "reduce_resolution",
        "enable_offload",
        "select_smaller_model",
    )


@dataclass(slots=True)
class ThreadCancellation:
    event: Event = field(default_factory=Event)

    @property
    def cancelled(self) -> bool:
        return self.event.is_set()

    def cancel(self) -> None:
        self.event.set()


@dataclass(slots=True)
class ComfyWorkerService:
    client: ComfyUIClient
    specialized_templates: Mapping[WanMode, ModeWorkflowTemplate] = field(default_factory=dict)
    poll_interval_seconds: float = 0.25
    object_info: dict[str, object] = field(default_factory=dict, init=False)
    system_stats: dict[str, object] = field(default_factory=dict, init=False)
    capabilities: BackendCapabilities | None = field(default=None, init=False)
    selections: dict[str, ComfyModelSelection] = field(default_factory=dict, init=False)
    residency: ModelResidencyManager = field(init=False)

    def __post_init__(self) -> None:
        self.residency = ModelResidencyManager(self.client)

    def inspect(self, command_id: str) -> CapabilitiesEvent:
        self._refresh()
        assert self.capabilities is not None
        payload = self.capabilities.model_dump(mode="json")
        payload["component_models"] = {
            "vae": list(_node_choices(self.object_info, "WanVideoVAELoader", "model_name")),
            "text_encoder": list(
                _node_choices(
                    self.object_info,
                    "LoadWanVideoT5TextEncoder",
                    "model_name",
                )
            ),
        }
        return CapabilitiesEvent(
            command_id=command_id,
            capabilities=payload,
        )

    def discover(self, command_id: str) -> ModelsEvent:
        self._refresh()
        assert self.capabilities is not None
        return ModelsEvent(
            command_id=command_id,
            models=tuple(
                {
                    "model_id": item.model_id,
                    "display_name": item.display_name,
                    "supported_modes": sorted(mode.value for mode in item.supported_modes),
                    "resolutions": [
                        f"{resolution.width}x{resolution.height}"
                        for resolution in item.supported_resolutions
                    ],
                }
                for item in self.capabilities.model_variants
            ),
        )

    def load(self, request: LoadModelRequest) -> AckEvent:
        self._refresh()
        assert self.capabilities is not None
        if request.backend_id != BACKEND_ID:
            raise ValueError("load request targets a different backend")
        vendor = accelerator_vendor(self.system_stats)
        if vendor not in {"cuda", "rocm"}:
            raise ValueError(
                "the ComfyUI Wan backend requires a detected CUDA or ROCm accelerator"
            )
        model = self.capabilities.model(request.model_id)
        if request.precision not in model.supported_precisions:
            raise ValueError("selected precision is unsupported")
        quantization = request.quantization or "disabled"
        if quantization not in model.supported_quantizations:
            raise ValueError("selected quantization is unsupported")
        load_device = request.offload_mode or "offload_device"
        if load_device not in model.supported_offload_modes:
            raise ValueError("selected offload mode is unsupported")
        _guard_memory_capacity(model.estimated_memory_profiles, self.system_stats, load_device)
        required_components = {"vae", "text_encoder"}
        if missing := required_components - set(request.component_model_ids):
            raise ValueError(
                "explicit component model selections are required: "
                + ", ".join(sorted(missing))
            )
        vae = request.component_model_ids["vae"]
        text_encoder = request.component_model_ids["text_encoder"]
        if vae not in _node_choices(self.object_info, "WanVideoVAELoader", "model_name"):
            raise ValueError("selected Wan VAE is not installed")
        if text_encoder not in _node_choices(
            self.object_info, "LoadWanVideoT5TextEncoder", "model_name"
        ):
            raise ValueError("selected Wan text encoder is not installed")
        selection = ComfyModelSelection(
            model_id=model.model_id,
            model_filename=model.display_name,
            vae_filename=vae,
            text_encoder_filename=text_encoder,
            precision=request.precision,
            quantization=quantization,
            load_device=load_device,
        )
        self.selections[model.model_id] = selection
        self.residency.retain(selection)
        return AckEvent(
            command_id=request.command_id,
            message=(
                "Model selection validated. ComfyUI will materialize and retain it "
                "when the first graph executes."
            ),
        )

    def generate(
        self,
        command: GenerateSegmentRequest,
        cancellation: ThreadCancellation,
        emit: Callable[[WanWorkerEvent], None],
    ) -> ResultEvent:
        if self.capabilities is None:
            self._refresh()
        assert self.capabilities is not None
        if command.request.model_id not in self.selections:
            raise RuntimeError("load and validate the selected Wan model before generation")
        model = self.capabilities.model(command.request.model_id)
        acceleration = resolve_wan_acceleration(
            project_policy=command.acceleration_policy,
            segment_policy=command.request.acceleration,
            methods=model.acceleration_methods,
            model_id=model.model_id,
            model_family=model.model_family,
            mode=command.request.mode,
            accelerator_vendor=accelerator_vendor(self.system_stats),
            installed_artifact_ids=frozenset(
                artifact_id
                for method in model.acceleration_methods
                for artifact_id in method.required_artifact_ids
            ),
        )
        builder = ComfyWanWorkflowBuilder(
            self.object_info,
            self.capabilities,
            self.selections,
            self.specialized_templates,
        )
        plan = builder.build(
            command.request,
            asset_inputs=command.asset_inputs,
            filename_prefix=command.output_prefix,
            seed=command.seed,
        )
        executor = ComfyWanExecutor(
            self.client,
            self.residency,
            poll_interval_seconds=self.poll_interval_seconds,
        )
        try:
            execution = executor.execute(
                plan,
                job_id=command.job_id,
                segment_id=command.request.segment_id,
                cancellation=cancellation,
                progress=lambda item: emit(
                    ProgressEvent(command_id=command.command_id, progress=item)
                ),
            )
        except RuntimeError as error:
            if _is_out_of_memory(error):
                self.residency.release()
                raise WanOutOfMemory(
                    "Wan generation exhausted accelerator memory; models were released. "
                    "Retry with smaller VAE tiles, fewer frames, offload, lower "
                    "resolution, or a smaller model."
                ) from error
            raise
        storage_keys = tuple(item.storage_key for item in execution.outputs)
        digest = hashlib.sha256("\n".join(storage_keys).encode("utf-8")).hexdigest()[:24]
        return ResultEvent(
            command_id=command.command_id,
            result=WorkerResult(
                job_id=command.job_id,
                result_asset_id=f"comfy-video-{digest}",
                metadata={
                    "prompt_id": execution.prompt_id,
                    "output_storage_keys": storage_keys,
                    "template_id": plan.template_id,
                    "template_version": plan.template_version,
                    "resolved_parameters": plan.resolved_parameters,
                    "model_filename": plan.model_selection.model_filename,
                    "vae_filename": plan.model_selection.vae_filename,
                    "text_encoder_filename": plan.model_selection.text_encoder_filename,
                    "precision": plan.model_selection.precision,
                    "vae_precision": plan.model_selection.vae_precision,
                    "text_encoder_precision": plan.model_selection.text_encoder_precision,
                    "quantization": plan.model_selection.quantization,
                    "load_device": plan.model_selection.load_device,
                    "accelerator_vendors": sorted(
                        self.capabilities.accelerator_vendors
                    ),
                    "device": (
                        self.system_stats.get("devices", [{}])[0]
                        if isinstance(self.system_stats.get("devices"), list)
                        and self.system_stats.get("devices")
                        else {}
                    ),
                    "wan_acceleration": acceleration.model_dump(mode="json"),
                },
            ),
        )

    def status(self, command_id: str) -> RuntimeStatusEvent:
        self.system_stats = self.client.system_stats()
        devices = self.system_stats.get("devices", ())
        normalized_devices = tuple(
            {
                key: value
                for key, value in device.items()
                if key in {"name", "type", "index", "vram_total", "vram_free"}
            }
            for device in devices
            if isinstance(device, Mapping)
        ) if isinstance(devices, list) else ()
        return RuntimeStatusEvent(
            command_id=command_id,
            status={
                **self.residency.status(),
                "backend_id": BACKEND_ID,
                "capabilities_inspected": self.capabilities is not None,
                "accelerator_vendor": accelerator_vendor(self.system_stats),
                "devices": normalized_devices,
                "system": {
                    str(key): value
                    for key, value in self.system_stats.get("system", {}).items()
                }
                if isinstance(self.system_stats.get("system"), Mapping)
                else {},
            },
        )

    def release(self, command_id: str) -> AckEvent:
        self.residency.release()
        return AckEvent(command_id=command_id, message="Wan model residency released")

    def _refresh(self) -> None:
        self.object_info = self.client.object_info()
        self.system_stats = self.client.system_stats()
        self.capabilities = inspect_comfyui_wan(
            self.object_info,
            self.system_stats,
            executable_specialized_modes=frozenset(self.specialized_templates),
        )


class StdioWanWorker:
    def __init__(self, service: ComfyWorkerService, output: TextIO) -> None:
        self.service = service
        self.output = output
        self._write_lock = Lock()
        self._jobs: dict[str, ThreadCancellation] = {}

    def dispatch(self, request: WanWorkerRequest) -> None:
        try:
            if isinstance(request, GenerateSegmentRequest):
                if request.job_id in self._jobs:
                    raise ValueError("job ID is already active")
                token = ThreadCancellation()
                self._jobs[request.job_id] = token
                Thread(
                    target=self._generate,
                    args=(request, token),
                    daemon=True,
                    name=f"wan2lab-{request.job_id}",
                ).start()
                return
            if isinstance(request, CancelRequest):
                token = self._jobs.get(request.job_id)
                if token is None:
                    raise KeyError(f"unknown active job: {request.job_id}")
                token.cancel()
                self.emit(AckEvent(command_id=request.command_id, message="Cancellation requested"))
            elif isinstance(request, InspectCapabilitiesRequest):
                self.emit(self.service.inspect(request.command_id))
            elif isinstance(request, DiscoverModelsRequest):
                self.emit(self.service.discover(request.command_id))
            elif isinstance(request, LoadModelRequest):
                self.emit(self.service.load(request))
            elif isinstance(request, RuntimeStatusRequest):
                self.emit(self.service.status(request.command_id))
            elif isinstance(request, (ReleaseWanModelRequest, ReleaseAllModelsRequest)):
                self.emit(self.service.release(request.command_id))
            else:
                raise TypeError(f"unhandled worker request: {type(request).__name__}")
        except Exception as error:
            self.emit(_error_event(request, error))

    def _generate(
        self,
        request: GenerateSegmentRequest,
        token: ThreadCancellation,
    ) -> None:
        try:
            self.emit(self.service.generate(request, token, self.emit))
        except Exception as error:
            self.emit(_error_event(request, error))
        finally:
            self._jobs.pop(request.job_id, None)

    def emit(self, event: WanWorkerEvent) -> None:
        document = event.model_dump_json()
        with self._write_lock:
            self.output.write(document + "\n")
            self.output.flush()


def _error_event(request: WanWorkerRequest, error: Exception) -> ErrorEvent:
    job_id = getattr(request, "job_id", request.command_id)
    out_of_memory = isinstance(error, WanOutOfMemory)
    return ErrorEvent(
        command_id=request.command_id,
        error=WorkerError(
            job_id=job_id,
            stage="cancelled" if isinstance(error, InterruptedError) else "worker",
            message=f"{type(error).__name__}: {error}",
            recoverable=isinstance(
                error,
                (ConnectionError, InterruptedError, TimeoutError, WanOutOfMemory),
            ),
            details={
                "oom_recovered": out_of_memory,
                **(
                    {"recovery_actions": list(error.recovery_actions)}
                    if out_of_memory
                    else {}
                ),
            },
        ),
    )


def _guard_memory_capacity(
    profiles: Mapping[str, float],
    system_stats: Mapping[str, object],
    load_device: str,
) -> None:
    devices = system_stats.get("devices", ())
    if not isinstance(devices, list) or not devices:
        return
    device = devices[0]
    if not isinstance(device, Mapping):
        return
    free_bytes = device.get("vram_free")
    if not isinstance(free_bytes, (int, float)) or free_bytes <= 0:
        return
    free_gib = float(free_bytes) / (1024**3)
    full_residency_gib = min(profiles.values()) if profiles else 0.0
    required_gib = full_residency_gib if load_device == "main_device" else min(
        4.0,
        full_residency_gib,
    )
    if required_gib and free_gib < required_gib:
        raise MemoryError(
            f"only {free_gib:.1f} GiB VRAM is free; {load_device} requires approximately "
            f"{required_gib:.1f} GiB. Select offload or release another model."
        )


def _is_out_of_memory(error: BaseException) -> bool:
    text = str(error).casefold()
    return any(
        token in text
        for token in (
            "out of memory",
            "cuda oom",
            "hip out of memory",
            "allocation on device",
        )
    )


def _node_choices(
    object_info: Mapping[str, object],
    node_name: str,
    input_name: str,
) -> tuple[str, ...]:
    node = object_info.get(node_name)
    inputs = node.get("input") if isinstance(node, Mapping) else None
    required = inputs.get("required") if isinstance(inputs, Mapping) else None
    specification = required.get(input_name) if isinstance(required, Mapping) else None
    choices = specification[0] if isinstance(specification, (list, tuple)) and specification else ()
    return tuple(str(item) for item in choices) if isinstance(choices, (list, tuple)) else ()


def main() -> int:
    worker = StdioWanWorker(ComfyWorkerService(ComfyUIClient()), sys.stdout)
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
            if not isinstance(payload, dict):
                raise ValueError("worker command must be a JSON object")
            worker.dispatch(parse_worker_request(payload))
        except Exception as error:
            fallback = ErrorEvent(
                command_id="invalid-command",
                error=WorkerError(
                    job_id="invalid-command",
                    stage="protocol",
                    message=f"{type(error).__name__}: {error}",
                ),
            )
            worker.emit(fallback)
    return 0


__all__ = [
    "ComfyWorkerService",
    "StdioWanWorker",
    "ThreadCancellation",
    "WanOutOfMemory",
    "main",
]
