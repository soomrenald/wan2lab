"""Mandatory review-gate state transitions."""

from __future__ import annotations

from wan2core.base import Identifier
from wan2core.segments import (
    RevisionReviewState,
    Segment,
    SegmentRequest,
    SegmentRevision,
    SegmentState,
)


class InvalidReviewTransition(ValueError):
    pass


def queue_revision(
    segment: Segment,
    *,
    revision_id: Identifier,
    request: SegmentRequest,
    seed: int,
    parent_revision_id: Identifier | None = None,
) -> tuple[Segment, SegmentRevision]:
    if segment.state not in {
        SegmentState.DRAFT,
        SegmentState.REJECTED,
        SegmentState.READY_FOR_REVIEW,
        SegmentState.ERROR,
        SegmentState.CANCELLED,
        SegmentState.STALE,
    }:
        raise InvalidReviewTransition(f"cannot queue a revision from {segment.state.value}")
    if request.segment_id != segment.segment_id:
        raise ValueError("request and segment IDs differ")
    revision = SegmentRevision(
        revision_id=revision_id,
        segment_id=segment.segment_id,
        revision_number=len(segment.revision_ids) + 1,
        source_request=request,
        seed=seed,
        parent_revision_id=parent_revision_id,
    )
    return (
        segment.model_copy(
            update={
                "state": SegmentState.QUEUED,
                "revision_ids": (*segment.revision_ids, revision_id),
                "current_approved_revision_id": None,
                "stale_reason": None,
            }
        ),
        revision,
    )


def start_generation(
    segment: Segment, revision: SegmentRevision
) -> tuple[Segment, SegmentRevision]:
    _require_current(segment, revision, SegmentState.QUEUED, RevisionReviewState.QUEUED)
    return (
        segment.model_copy(update={"state": SegmentState.GENERATING}),
        revision.model_copy(update={"review_state": RevisionReviewState.GENERATING}),
    )


def complete_generation(
    segment: Segment,
    revision: SegmentRevision,
    *,
    result_asset_id: Identifier,
    frame_asset_ids: tuple[Identifier, ...] = (),
    start_frame_asset_id: Identifier | None = None,
    end_frame_asset_id: Identifier | None = None,
    resolved_parameters: dict[str, object] | None = None,
    generation_metadata: dict[str, object] | None = None,
    warnings: tuple[str, ...] = (),
    provenance_id: Identifier | None = None,
) -> tuple[Segment, SegmentRevision]:
    _require_current(
        segment,
        revision,
        SegmentState.GENERATING,
        RevisionReviewState.GENERATING,
    )
    updated = revision.model_copy(
        update={
            "result_asset_id": result_asset_id,
            "frame_asset_ids": frame_asset_ids,
            "start_frame_asset_id": start_frame_asset_id,
            "end_frame_asset_id": end_frame_asset_id,
            "resolved_parameters": resolved_parameters or {},
            "generation_metadata": generation_metadata or {},
            "warnings": warnings,
            "provenance_id": provenance_id,
            "review_state": RevisionReviewState.READY_FOR_REVIEW,
        }
    )
    return segment.model_copy(update={"state": SegmentState.READY_FOR_REVIEW}), updated


def finish_generation_failure(
    segment: Segment,
    revision: SegmentRevision,
    *,
    message: str,
    cancelled: bool = False,
) -> tuple[Segment, SegmentRevision]:
    _require_current(
        segment,
        revision,
        SegmentState.GENERATING,
        RevisionReviewState.GENERATING,
    )
    if not message.strip():
        raise ValueError("generation failure message must not be empty")
    segment_state = SegmentState.CANCELLED if cancelled else SegmentState.ERROR
    revision_state = RevisionReviewState.CANCELLED if cancelled else RevisionReviewState.ERROR
    return (
        segment.model_copy(update={"state": segment_state}),
        revision.model_copy(
            update={
                "review_state": revision_state,
                "errors": (*revision.errors, message.strip()),
            }
        ),
    )


def approve_revision(
    segment: Segment, revision: SegmentRevision
) -> tuple[Segment, SegmentRevision]:
    _require_current(
        segment,
        revision,
        SegmentState.READY_FOR_REVIEW,
        RevisionReviewState.READY_FOR_REVIEW,
    )
    return (
        segment.model_copy(
            update={
                "state": SegmentState.APPROVED_LOCKED,
                "current_approved_revision_id": revision.revision_id,
            }
        ),
        revision.model_copy(update={"review_state": RevisionReviewState.APPROVED}),
    )


def reject_revision(
    segment: Segment,
    revision: SegmentRevision,
    *,
    reason: str,
) -> tuple[Segment, SegmentRevision]:
    _require_current(
        segment,
        revision,
        SegmentState.READY_FOR_REVIEW,
        RevisionReviewState.READY_FOR_REVIEW,
    )
    if not reason.strip():
        raise ValueError("rejection reason must not be empty")
    return (
        segment.model_copy(update={"state": SegmentState.REJECTED}),
        revision.model_copy(
            update={
                "review_state": RevisionReviewState.REJECTED,
                "superseded_reason": reason,
            }
        ),
    )


def begin_modification(
    segment: Segment, revision: SegmentRevision
) -> tuple[Segment, SegmentRevision]:
    _require_current(
        segment,
        revision,
        SegmentState.READY_FOR_REVIEW,
        RevisionReviewState.READY_FOR_REVIEW,
    )
    return (
        segment.model_copy(update={"state": SegmentState.MODIFYING}),
        revision.model_copy(update={"review_state": RevisionReviewState.MODIFYING}),
    )


def complete_modification(
    segment: Segment,
    source_revision: SegmentRevision,
    *,
    revision_id: Identifier,
    result_asset_id: Identifier,
    replacement_frame_map: dict[int, Identifier],
    provenance_id: Identifier,
    propagate_boundary_indices: tuple[int, ...] = (),
) -> tuple[Segment, SegmentRevision, SegmentRevision]:
    _require_current(
        segment,
        source_revision,
        SegmentState.MODIFYING,
        RevisionReviewState.MODIFYING,
    )
    superseded = source_revision.model_copy(
        update={
            "review_state": RevisionReviewState.SUPERSEDED,
            "superseded_reason": "modified into a new immutable revision",
        }
    )
    invalid_boundary_indices = set(propagate_boundary_indices) - {0, source_revision.source_request.frame_count - 1}
    if invalid_boundary_indices:
        raise ValueError("only first or last frame edits may propagate as segment boundaries")
    missing_propagations = set(propagate_boundary_indices) - set(replacement_frame_map)
    if missing_propagations:
        raise ValueError("propagated boundaries require replacement frames")
    start_frame_asset_id = source_revision.start_frame_asset_id
    end_frame_asset_id = source_revision.end_frame_asset_id
    if 0 in propagate_boundary_indices:
        start_frame_asset_id = replacement_frame_map[0]
    last_index = source_revision.source_request.frame_count - 1
    if last_index in propagate_boundary_indices:
        end_frame_asset_id = replacement_frame_map[last_index]
    revised = source_revision.model_copy(
        update={
            "revision_id": revision_id,
            "revision_number": len(segment.revision_ids) + 1,
            "result_asset_id": result_asset_id,
            "replacement_frame_map": replacement_frame_map,
            "start_frame_asset_id": start_frame_asset_id,
            "end_frame_asset_id": end_frame_asset_id,
            "review_state": RevisionReviewState.READY_FOR_REVIEW,
            "parent_revision_id": source_revision.revision_id,
            "superseded_reason": None,
            "provenance_id": provenance_id,
        }
    )
    updated_segment = segment.model_copy(
        update={
            "state": SegmentState.READY_FOR_REVIEW,
            "revision_ids": (*segment.revision_ids, revision_id),
        }
    )
    return updated_segment, superseded, revised


def mark_segment_stale(segment: Segment, reason: str) -> Segment:
    if not reason.strip():
        raise ValueError("stale reason must not be empty")
    return segment.model_copy(
        update={
            "state": SegmentState.STALE,
            "current_approved_revision_id": None,
            "stale_reason": reason,
        }
    )


def _require_current(
    segment: Segment,
    revision: SegmentRevision,
    segment_state: SegmentState,
    revision_state: RevisionReviewState,
) -> None:
    if revision.segment_id != segment.segment_id or revision.revision_id not in segment.revision_ids:
        raise InvalidReviewTransition("revision does not belong to the segment")
    if segment.state is not segment_state or revision.review_state is not revision_state:
        raise InvalidReviewTransition(
            f"expected {segment_state.value}/{revision_state.value}, got "
            f"{segment.state.value}/{revision.review_state.value}"
        )


__all__ = [
    "InvalidReviewTransition",
    "approve_revision",
    "begin_modification",
    "complete_generation",
    "complete_modification",
    "finish_generation_failure",
    "mark_segment_stale",
    "queue_revision",
    "reject_revision",
    "start_generation",
]
