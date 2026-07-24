"""Cancellable ComfyUI execution and explicit model-residency state."""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Callable, Mapping, Protocol

from wan2core.workers import WorkerProgress
from wan2lab.backends.comfy_workflow import ComfyModelSelection, ComfyWorkflowPlan


class ComfyClient(Protocol):
    def queue_prompt(self, workflow: Mapping[str, object], *, client_id: str) -> dict[str, object]: ...

    def history(self, prompt_id: str) -> dict[str, object]: ...

    def queue(self) -> dict[str, object]: ...

    def interrupt(self) -> dict[str, object]: ...

    def free_models(self) -> dict[str, object]: ...


class Cancellation(Protocol):
    @property
    def cancelled(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class ComfyOutputFile:
    filename: str
    subfolder: str = ""
    storage_type: str = "output"

    @property
    def storage_key(self) -> str:
        return "/".join(item for item in (self.storage_type, self.subfolder, self.filename) if item)


@dataclass(frozen=True, slots=True)
class ComfyExecutionResult:
    prompt_id: str
    outputs: tuple[ComfyOutputFile, ...]
    history: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class ResidentWanModel:
    model_id: str
    model_filename: str
    vae_filename: str
    text_encoder_filename: str
    clip_vision_filename: str | None
    vitpose_filename: str | None
    yolo_filename: str | None
    sam2_filename: str | None
    onnx_device: str
    sam_device: str
    precision: str
    quantization: str
    load_device: str
    blocks_to_swap: int


@dataclass(slots=True)
class ModelResidencyManager:
    client: ComfyClient
    resident: ResidentWanModel | None = None

    def retain(self, selection: ComfyModelSelection) -> ResidentWanModel:
        desired = ResidentWanModel(
            model_id=selection.model_id,
            model_filename=selection.model_filename,
            vae_filename=selection.vae_filename,
            text_encoder_filename=selection.text_encoder_filename,
            clip_vision_filename=selection.clip_vision_filename,
            vitpose_filename=selection.vitpose_filename,
            yolo_filename=selection.yolo_filename,
            sam2_filename=selection.sam2_filename,
            onnx_device=selection.onnx_device,
            sam_device=selection.sam_device,
            precision=selection.precision,
            quantization=selection.quantization,
            load_device=selection.load_device,
            blocks_to_swap=selection.blocks_to_swap,
        )
        if self.resident is not None and self.resident != desired:
            self.client.free_models()
        self.resident = desired
        return desired

    def release(self) -> None:
        self.client.free_models()
        self.resident = None

    def status(self) -> dict[str, object]:
        return {
            "residency_group": "wan",
            "resident": self.resident is not None,
            "model": (
                None
                if self.resident is None
                else {
                    "model_id": self.resident.model_id,
                    "model_filename": self.resident.model_filename,
                    "vae_filename": self.resident.vae_filename,
                    "text_encoder_filename": self.resident.text_encoder_filename,
                    "clip_vision_filename": self.resident.clip_vision_filename,
                    "vitpose_filename": self.resident.vitpose_filename,
                    "yolo_filename": self.resident.yolo_filename,
                    "sam2_filename": self.resident.sam2_filename,
                    "onnx_device": self.resident.onnx_device,
                    "sam_device": self.resident.sam_device,
                    "precision": self.resident.precision,
                    "quantization": self.resident.quantization,
                    "load_device": self.resident.load_device,
                    "blocks_to_swap": self.resident.blocks_to_swap,
                }
            ),
        }


@dataclass(slots=True)
class ComfyWanExecutor:
    client: ComfyClient
    residency: ModelResidencyManager
    poll_interval_seconds: float = 0.25
    max_polls: int = 172_800
    sleep: Callable[[float], None] = time.sleep

    def execute(
        self,
        plan: ComfyWorkflowPlan,
        *,
        job_id: str,
        segment_id: str,
        cancellation: Cancellation,
        progress: Callable[[WorkerProgress], None],
    ) -> ComfyExecutionResult:
        self.residency.retain(plan.model_selection)
        progress(
            WorkerProgress(
                job_id=job_id,
                segment_id=segment_id,
                stage="queueing",
                message="Submitting versioned ComfyUI workflow",
                runtime_status=self.residency.status(),
            )
        )
        queued = self.client.queue_prompt(plan.workflow, client_id=job_id)
        prompt_id = queued.get("prompt_id")
        if not isinstance(prompt_id, str) or not prompt_id:
            raise RuntimeError("ComfyUI did not return a prompt ID")
        for poll in range(self.max_polls):
            if cancellation.cancelled:
                self.client.interrupt()
                raise InterruptedError(f"generation cancelled: {job_id}")
            history = self.client.history(prompt_id)
            record = history.get(prompt_id)
            if isinstance(record, Mapping):
                status = record.get("status")
                status_mapping = status if isinstance(status, Mapping) else {}
                if status_mapping.get("status_str") == "error":
                    raise RuntimeError(_history_error(status_mapping))
                if status_mapping.get("completed") is True:
                    outputs = _output_files(record, plan.output_node_id)
                    if not outputs:
                        raise RuntimeError("ComfyUI completed without a typed output file")
                    progress(
                        WorkerProgress(
                            job_id=job_id,
                            segment_id=segment_id,
                            stage="complete",
                            current=1,
                            total=1,
                            message="ComfyUI generation completed",
                            runtime_status=self.residency.status(),
                        )
                    )
                    return ComfyExecutionResult(
                        prompt_id=prompt_id,
                        outputs=outputs,
                        history=record,
                    )
            queue = self.client.queue()
            pending = _queue_position(queue, prompt_id)
            progress(
                WorkerProgress(
                    job_id=job_id,
                    segment_id=segment_id,
                    stage="generating" if pending == 0 else "queued",
                    current=poll,
                    message=(
                        "ComfyUI is executing the workflow"
                        if pending == 0
                        else f"ComfyUI queue position: {pending}"
                    ),
                    runtime_status=self.residency.status(),
                )
            )
            self.sleep(self.poll_interval_seconds)
        raise TimeoutError(f"ComfyUI history did not complete after {self.max_polls} polls")


def _output_files(record: Mapping[str, object], output_node_id: str) -> tuple[ComfyOutputFile, ...]:
    outputs = record.get("outputs")
    node = outputs.get(output_node_id) if isinstance(outputs, Mapping) else None
    if not isinstance(node, Mapping):
        return ()
    files = []
    for key in ("gifs", "videos", "images"):
        values = node.get(key, ())
        if not isinstance(values, list):
            continue
        for value in values:
            if not isinstance(value, Mapping) or not isinstance(value.get("filename"), str):
                continue
            files.append(
                ComfyOutputFile(
                    filename=str(value["filename"]),
                    subfolder=str(value.get("subfolder", "")),
                    storage_type=str(value.get("type", "output")),
                )
            )
    return tuple(files)


def _queue_position(queue: Mapping[str, object], prompt_id: str) -> int | None:
    running = queue.get("queue_running", ())
    if isinstance(running, list) and any(
        isinstance(item, list) and prompt_id in item for item in running
    ):
        return 0
    pending = queue.get("queue_pending", ())
    if isinstance(pending, list):
        for index, item in enumerate(pending, start=1):
            if isinstance(item, list) and prompt_id in item:
                return index
    return None


def _history_error(status: Mapping[str, object]) -> str:
    messages = status.get("messages", ())
    if isinstance(messages, list):
        for message in reversed(messages):
            if isinstance(message, list) and message and message[0] == "execution_error":
                return f"ComfyUI execution failed: {message[-1]}"
    return "ComfyUI execution failed"


__all__ = [
    "ComfyExecutionResult",
    "ComfyOutputFile",
    "ComfyWanExecutor",
    "ModelResidencyManager",
    "ResidentWanModel",
]
