"""Generated segment and immutable revision records."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from wan2core.backends import FrameRounding, WanMode
from wan2core.actions import ActionSpec
from wan2core.base import DomainModel, Identifier, Milliseconds, require_unique


class ContinuationPolicy(StrEnum):
    AUTHORED_ANCHOR = "authored_anchor"
    GENERATED_LAST_FRAME = "generated_last_frame"
    CORRECTED_CONTINUATION = "corrected_continuation"
    DUAL_BOUNDARY = "dual_boundary"
    OVERLAP = "overlap"


class SegmentState(StrEnum):
    DRAFT = "draft"
    QUEUED = "queued"
    GENERATING = "generating"
    READY_FOR_REVIEW = "ready_for_review"
    MODIFYING = "modifying"
    APPROVED_LOCKED = "approved_locked"
    REJECTED = "rejected"
    STALE = "stale"
    CANCELLED = "cancelled"
    ERROR = "error"


class RevisionReviewState(StrEnum):
    QUEUED = "queued"
    GENERATING = "generating"
    READY_FOR_REVIEW = "ready_for_review"
    MODIFYING = "modifying"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    CANCELLED = "cancelled"
    ERROR = "error"


class SegmentRequest(DomainModel):
    request_id: Identifier
    segment_id: Identifier
    mode: WanMode
    backend_id: Identifier
    model_id: Identifier
    start_ms: Milliseconds
    end_ms: Milliseconds
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    generation_fps: float = Field(gt=0.0)
    frame_count: int = Field(gt=0)
    frame_rounding: FrameRounding = FrameRounding.NEAREST
    start_image_asset_id: Identifier | None = None
    end_image_asset_id: Identifier | None = None
    reference_character_asset_id: Identifier | None = None
    driving_video_asset_id: Identifier | None = None
    source_video_asset_id: Identifier | None = None
    mask_asset_id: Identifier | None = None
    prompt: str = ""
    negative_prompt: str = ""
    action_spec_id: Identifier | None = None
    action_spec: ActionSpec | None = None
    character_identity_ids: tuple[Identifier, ...] = ()
    parameters: dict[str, object] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_interval_and_inputs(self) -> "SegmentRequest":
        if self.end_ms <= self.start_ms:
            raise ValueError("segment request end_ms must exceed start_ms")
        required: dict[WanMode, tuple[str, ...]] = {
            WanMode.PROMPT: (),
            WanMode.I2V: ("start_image_asset_id",),
            WanMode.FIRST_LAST: ("start_image_asset_id", "end_image_asset_id"),
            WanMode.ANIMATE: ("reference_character_asset_id", "driving_video_asset_id"),
            WanMode.REPLACE: ("reference_character_asset_id", "source_video_asset_id"),
        }
        missing = [name for name in required[self.mode] if getattr(self, name) is None]
        if missing:
            raise ValueError(f"{self.mode.value} request is missing: {', '.join(missing)}")
        require_unique(self.character_identity_ids, "segment character identity IDs")
        if self.action_spec is not None and self.action_spec.action_id != self.action_spec_id:
            raise ValueError("embedded action spec must match action_spec_id")
        return self


class SegmentRevision(DomainModel):
    revision_id: Identifier
    segment_id: Identifier
    revision_number: int = Field(gt=0)
    source_request: SegmentRequest
    resolved_parameters: dict[str, object] = Field(default_factory=dict)
    seed: int = Field(ge=0, le=2_147_483_647)
    result_asset_id: Identifier | None = None
    frame_asset_ids: tuple[Identifier, ...] = ()
    replacement_frame_map: dict[int, Identifier] = Field(default_factory=dict)
    start_frame_asset_id: Identifier | None = None
    end_frame_asset_id: Identifier | None = None
    generation_metadata: dict[str, object] = Field(default_factory=dict)
    review_state: RevisionReviewState = RevisionReviewState.QUEUED
    parent_revision_id: Identifier | None = None
    superseded_reason: str | None = None
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    provenance_id: Identifier | None = None

    @model_validator(mode="after")
    def validate_revision(self) -> "SegmentRevision":
        if self.source_request.segment_id != self.segment_id:
            raise ValueError("revision request must reference the same segment")
        if self.review_state in {
            RevisionReviewState.READY_FOR_REVIEW,
            RevisionReviewState.APPROVED,
            RevisionReviewState.SUPERSEDED,
        } and self.result_asset_id is None:
            raise ValueError("reviewable revisions require a result asset")
        return self


class Segment(DomainModel):
    segment_id: Identifier
    start_ms: Milliseconds
    end_ms: Milliseconds
    start_keyframe_id: Identifier | None = None
    end_keyframe_id: Identifier | None = None
    mode: WanMode
    prompt: str = ""
    negative_prompt: str = ""
    parameters: dict[str, object] = Field(default_factory=dict)
    action_spec_id: Identifier | None = None
    character_identity_ids: tuple[Identifier, ...] = ()
    driving_video_asset_id: Identifier | None = None
    backend_id: Identifier
    model_id: Identifier
    continuation_policy: ContinuationPolicy
    state: SegmentState = SegmentState.DRAFT
    revision_ids: tuple[Identifier, ...] = ()
    current_approved_revision_id: Identifier | None = None
    stale_reason: str | None = None

    @model_validator(mode="after")
    def validate_segment(self) -> "Segment":
        if self.end_ms <= self.start_ms:
            raise ValueError("segment end_ms must exceed start_ms")
        require_unique(self.revision_ids, "segment revision IDs")
        if (
            self.current_approved_revision_id is not None
            and self.current_approved_revision_id not in self.revision_ids
        ):
            raise ValueError("approved revision must belong to the segment")
        if self.state is SegmentState.APPROVED_LOCKED and self.current_approved_revision_id is None:
            raise ValueError("approved segment requires an approved revision")
        if self.state is SegmentState.STALE and not self.stale_reason:
            raise ValueError("stale segment requires a reason")
        return self


class PlannedSegment(DomainModel):
    segment_id: Identifier
    start_ms: Milliseconds
    end_ms: Milliseconds
    requested_duration_ms: int = Field(gt=0)
    actual_duration_ms: int = Field(gt=0)
    start_keyframe_id: Identifier | None = None
    end_keyframe_id: Identifier | None = None
    end_anchor_is_review_target: bool = False
    mode: WanMode
    backend_id: Identifier
    model_id: Identifier
    generation_fps: float = Field(gt=0.0)
    frame_count: int = Field(gt=0)
    output_fps: float = Field(gt=0.0)
    continuation_policy: ContinuationPolicy
    review_required: bool = True


__all__ = [
    "ContinuationPolicy",
    "PlannedSegment",
    "RevisionReviewState",
    "Segment",
    "SegmentRequest",
    "SegmentRevision",
    "SegmentState",
]
