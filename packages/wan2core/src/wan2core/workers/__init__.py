"""Typed Wan worker requests and result/progress envelopes."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter

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


class GenerateSegmentRequest(DomainModel):
    kind: Literal[WanCommandKind.GENERATE_SEGMENT] = WanCommandKind.GENERATE_SEGMENT
    command_id: Identifier
    job_id: Identifier
    request: SegmentRequest


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


__all__ = [
    "CancelRequest",
    "DiscoverModelsRequest",
    "GenerateSegmentRequest",
    "InspectCapabilitiesRequest",
    "LoadModelRequest",
    "ReleaseAllModelsRequest",
    "ReleaseWanModelRequest",
    "RuntimeStatusRequest",
    "WAN_WORKER_PROTOCOL_VERSION",
    "WanCommandKind",
    "WanWorkerRequest",
    "WorkerError",
    "WorkerProgress",
    "WorkerResult",
    "parse_worker_request",
    "worker_request_schema",
]
