"""Deterministic no-GPU backend for orchestration and parity tests."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from threading import Event
from typing import Callable

from wan2core.backends import BackendCapabilities
from wan2core.segments import SegmentRequest
from wan2core.workers import WorkerProgress, WorkerResult


ProgressCallback = Callable[[WorkerProgress], None]


class MockGenerationCancelled(RuntimeError):
    pass


@dataclass(slots=True)
class CancellationToken:
    _event: Event

    @classmethod
    def create(cls) -> "CancellationToken":
        return cls(Event())

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self) -> None:
        self._event.set()

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise MockGenerationCancelled("mock generation cancelled")


class MockWanBackend:
    """Produces stable logical asset IDs and progress without writing media."""

    backend_id = "mock-wan"

    def __init__(self, capabilities: BackendCapabilities) -> None:
        if capabilities.backend_id != self.backend_id:
            raise ValueError("mock capabilities must use the mock-wan backend ID")
        self._capabilities = capabilities
        self.loaded_model_id: str | None = None

    def capabilities(self) -> BackendCapabilities:
        return self._capabilities

    def load_model(self, model_id: str) -> None:
        self._capabilities.model(model_id)
        self.loaded_model_id = model_id

    def release(self) -> None:
        self.loaded_model_id = None

    def validate_segment_request(self, request: SegmentRequest) -> tuple[str, ...]:
        errors: list[str] = []
        if request.backend_id != self.backend_id:
            errors.append(f"request backend must be {self.backend_id}")
            return tuple(errors)
        try:
            model = self._capabilities.model(request.model_id)
        except KeyError:
            return (f"unknown model: {request.model_id}",)
        if request.mode not in model.supported_modes:
            errors.append(f"mode {request.mode.value} is unsupported")
        if not model.supports_resolution(request.width, request.height):
            errors.append(f"resolution {request.width}x{request.height} is unsupported")
        if request.frame_count not in model.valid_frame_counts():
            errors.append(f"frame count {request.frame_count} is invalid")
        if request.generation_fps not in model.supported_generation_fps:
            errors.append(f"generation FPS {request.generation_fps} is unsupported")
        required = model.required_inputs_by_mode.get(request.mode, ())
        for field_name in required:
            if getattr(request, field_name, None) is None:
                errors.append(f"required input is missing: {field_name}")
        supported_parameter_keys = {
            item.key for item in (*self._capabilities.parameter_descriptors, *model.parameter_descriptors)
            if request.mode in item.applicable_modes
        }
        unknown = set(request.parameters) - supported_parameter_keys
        if unknown:
            errors.append(f"unsupported parameters: {', '.join(sorted(unknown))}")
        return tuple(errors)

    def generate_segment(
        self,
        request: SegmentRequest,
        *,
        job_id: str,
        progress: ProgressCallback,
        cancellation: CancellationToken,
    ) -> WorkerResult:
        errors = self.validate_segment_request(request)
        if errors:
            raise ValueError("; ".join(errors))
        if self.loaded_model_id != request.model_id:
            raise RuntimeError("requested model is not loaded")
        stages = ("validate", "prepare", "diffusion", "encode")
        for index, stage in enumerate(stages, start=1):
            cancellation.raise_if_cancelled()
            progress(
                WorkerProgress(
                    job_id=job_id,
                    segment_id=request.segment_id,
                    stage=stage,
                    current=index,
                    total=len(stages),
                    message=f"Mock {stage}",
                )
            )
        digest = hashlib.sha256(request.model_dump_json().encode("utf-8")).hexdigest()[:20]
        result_id = f"mock-video-{digest}"
        frame_ids = tuple(f"mock-frame-{digest}-{index:06d}" for index in range(request.frame_count))
        return WorkerResult(
            job_id=job_id,
            result_asset_id=result_id,
            frame_asset_ids=frame_ids,
            metadata={
                "backend_id": self.backend_id,
                "model_id": request.model_id,
                "mode": request.mode.value,
                "deterministic_mock": True,
            },
        )


__all__ = [
    "CancellationToken",
    "MockGenerationCancelled",
    "MockWanBackend",
]
