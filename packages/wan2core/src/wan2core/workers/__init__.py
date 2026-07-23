"""Typed Wan worker requests and result/progress envelopes."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter, model_validator

from wan2core.base import DomainModel, Identifier
from wan2core.segments import SegmentRequest


WAN_WORKER_PROTOCOL_VERSION = "1"


class WanCommandKind(StrEnum):
    INSPECT_CAPABILITIES = "inspect_wan_capabilities"
    DISCOVER_MODELS = "discover_wan_models"
    LOAD_MODEL = "load_wan_model"
    GENERATE_SEGMENT = "generate_wan_segment"
    CANCEL = "cancel"
    RELEASE_WAN_MODEL = "release_wan_model"
    RELEASE_ALL_MODELS = "release_all_models"
    RUNTIME_STATUS = "runtime_status"


class InspectCapabilitiesRequest(DomainModel):
    kind: Literal[WanCommandKind.INSPECT_CAPABILITIES] = WanCommandKind.INSPECT_CAPABILITIES
    command_id: Identifier
    backend_id: Identifier


class DiscoverModelsRequest(DomainModel):
    kind: Literal[WanCommandKind.DISCOVER_MODELS] = WanCommandKind.DISCOVER_MODELS
    command_id: Identifier
    backend_id: Identifier
    search_roots: tuple[str, ...]


class LoadModelRequest(DomainModel):
    kind: Literal[WanCommandKind.LOAD_MODEL] = WanCommandKind.LOAD_MODEL
    command_id: Identifier
    backend_id: Identifier
    model_id: Identifier
    precision: str
    quantization: str | None = None
    offload_mode: str | None = None
    component_model_ids: dict[str, Identifier] = Field(default_factory=dict)


class GenerateSegmentRequest(DomainModel):
    kind: Literal[WanCommandKind.GENERATE_SEGMENT] = WanCommandKind.GENERATE_SEGMENT
    command_id: Identifier
    job_id: Identifier
    request: SegmentRequest
    seed: int = Field(ge=0, le=2_147_483_647)
    asset_inputs: dict[Identifier, str] = Field(default_factory=dict)
    output_prefix: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_paths(self) -> "GenerateSegmentRequest":
        for path in (*self.asset_inputs.values(), self.output_prefix):
            normalized = path.replace("\\", "/")
            if normalized.startswith("/") or ".." in normalized.split("/"):
                raise ValueError("worker paths must be workspace-relative and cannot escape")
        return self


class CancelRequest(DomainModel):
    kind: Literal[WanCommandKind.CANCEL] = WanCommandKind.CANCEL
    command_id: Identifier
    job_id: Identifier


class ReleaseWanModelRequest(DomainModel):
    kind: Literal[WanCommandKind.RELEASE_WAN_MODEL] = WanCommandKind.RELEASE_WAN_MODEL
    command_id: Identifier
    backend_id: Identifier | None = None
    model_id: Identifier | None = None


class ReleaseAllModelsRequest(DomainModel):
    kind: Literal[WanCommandKind.RELEASE_ALL_MODELS] = WanCommandKind.RELEASE_ALL_MODELS
    command_id: Identifier


class RuntimeStatusRequest(DomainModel):
    kind: Literal[WanCommandKind.RUNTIME_STATUS] = WanCommandKind.RUNTIME_STATUS
    command_id: Identifier


WanWorkerRequest = Annotated[
    InspectCapabilitiesRequest
    | DiscoverModelsRequest
    | LoadModelRequest
    | GenerateSegmentRequest
    | CancelRequest
    | ReleaseWanModelRequest
    | ReleaseAllModelsRequest
    | RuntimeStatusRequest,
    Field(discriminator="kind"),
]

_REQUEST_ADAPTER = TypeAdapter(WanWorkerRequest)


def parse_worker_request(payload: dict[str, object]) -> WanWorkerRequest:
    return _REQUEST_ADAPTER.validate_python(payload)


def worker_request_schema() -> dict[str, object]:
    return _REQUEST_ADAPTER.json_schema()


class WorkerEventKind(StrEnum):
    CAPABILITIES = "capabilities"
    MODELS = "models"
    RUNTIME_STATUS = "runtime_status"
    PROGRESS = "progress"
    RESULT = "result"
    ERROR = "error"
    ACK = "ack"


class WorkerProgress(DomainModel):
    job_id: Identifier
    segment_id: Identifier | None = None
    frame_id: Identifier | None = None
    stage: str = Field(min_length=1)
    current: int | None = Field(default=None, ge=0)
    total: int | None = Field(default=None, ge=0)
    message: str = ""
    warning: str | None = None
    runtime_status: dict[str, object] = Field(default_factory=dict)


class WorkerResult(DomainModel):
    job_id: Identifier
    result_asset_id: Identifier
    frame_asset_ids: tuple[Identifier, ...] = ()
    metadata: dict[str, object] = Field(default_factory=dict)


class WorkerError(DomainModel):
    job_id: Identifier
    stage: str = Field(min_length=1)
    message: str = Field(min_length=1)
    recoverable: bool = False
    details: dict[str, object] = Field(default_factory=dict)


class CapabilitiesEvent(DomainModel):
    kind: Literal[WorkerEventKind.CAPABILITIES] = WorkerEventKind.CAPABILITIES
    command_id: Identifier
    capabilities: dict[str, object]


class ModelsEvent(DomainModel):
    kind: Literal[WorkerEventKind.MODELS] = WorkerEventKind.MODELS
    command_id: Identifier
    models: tuple[dict[str, object], ...]


class RuntimeStatusEvent(DomainModel):
    kind: Literal[WorkerEventKind.RUNTIME_STATUS] = WorkerEventKind.RUNTIME_STATUS
    command_id: Identifier
    status: dict[str, object]


class ProgressEvent(DomainModel):
    kind: Literal[WorkerEventKind.PROGRESS] = WorkerEventKind.PROGRESS
    command_id: Identifier
    progress: WorkerProgress


class ResultEvent(DomainModel):
    kind: Literal[WorkerEventKind.RESULT] = WorkerEventKind.RESULT
    command_id: Identifier
    result: WorkerResult


class ErrorEvent(DomainModel):
    kind: Literal[WorkerEventKind.ERROR] = WorkerEventKind.ERROR
    command_id: Identifier
    error: WorkerError


class AckEvent(DomainModel):
    kind: Literal[WorkerEventKind.ACK] = WorkerEventKind.ACK
    command_id: Identifier
    message: str


WanWorkerEvent = Annotated[
    CapabilitiesEvent
    | ModelsEvent
    | RuntimeStatusEvent
    | ProgressEvent
    | ResultEvent
    | ErrorEvent
    | AckEvent,
    Field(discriminator="kind"),
]

_EVENT_ADAPTER = TypeAdapter(WanWorkerEvent)


def parse_worker_event(payload: dict[str, object]) -> WanWorkerEvent:
    return _EVENT_ADAPTER.validate_python(payload)


def worker_event_schema() -> dict[str, object]:
    return _EVENT_ADAPTER.json_schema()


__all__ = [
    "CancelRequest",
    "CapabilitiesEvent",
    "DiscoverModelsRequest",
    "GenerateSegmentRequest",
    "InspectCapabilitiesRequest",
    "LoadModelRequest",
    "ModelsEvent",
    "ProgressEvent",
    "ReleaseAllModelsRequest",
    "ReleaseWanModelRequest",
    "RuntimeStatusRequest",
    "RuntimeStatusEvent",
    "ResultEvent",
    "WAN_WORKER_PROTOCOL_VERSION",
    "WanCommandKind",
    "WanWorkerEvent",
    "WanWorkerRequest",
    "WorkerError",
    "WorkerEventKind",
    "WorkerProgress",
    "WorkerResult",
    "parse_worker_request",
    "parse_worker_event",
    "worker_event_schema",
    "worker_request_schema",
]
