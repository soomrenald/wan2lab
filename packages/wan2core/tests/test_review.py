from __future__ import annotations

import unittest

from wan2core.backends import WanMode
from wan2core.review import (
    InvalidReviewTransition,
    approve_revision,
    begin_modification,
    complete_generation,
    complete_modification,
    mark_segment_stale,
    queue_revision,
    reject_revision,
    start_generation,
)
from wan2core.segments import (
    ContinuationPolicy,
    RevisionReviewState,
    Segment,
    SegmentRequest,
    SegmentState,
)


def segment() -> Segment:
    return Segment(
        segment_id="segment-1",
        start_ms=0,
        end_ms=5_000,
        mode=WanMode.PROMPT,
        backend_id="mock-wan",
        model_id="wan-test",
        continuation_policy=ContinuationPolicy.AUTHORED_ANCHOR,
    )


def request() -> SegmentRequest:
    return SegmentRequest(
        request_id="request-1",
        segment_id="segment-1",
        mode=WanMode.PROMPT,
        backend_id="mock-wan",
        model_id="wan-test",
        start_ms=0,
        end_ms=5_000,
        width=1280,
        height=720,
        generation_fps=16.0,
        frame_count=81,
    )


def ready_revision():
    current, revision = queue_revision(
        segment(), revision_id="revision-1", request=request(), seed=42
    )
    current, revision = start_generation(current, revision)
    return complete_generation(
        current,
        revision,
        result_asset_id="video-1",
        start_frame_asset_id="frame-first",
        end_frame_asset_id="frame-last",
        provenance_id="prov-1",
    )


class ReviewStateMachineTests(unittest.TestCase):
    def test_generation_stops_at_review_until_explicit_approval(self) -> None:
        current, revision = ready_revision()
        self.assertEqual(current.state, SegmentState.READY_FOR_REVIEW)
        self.assertEqual(revision.review_state, RevisionReviewState.READY_FOR_REVIEW)
        approved, approved_revision = approve_revision(current, revision)
        self.assertEqual(approved.state, SegmentState.APPROVED_LOCKED)
        self.assertEqual(approved.current_approved_revision_id, revision.revision_id)
        self.assertEqual(approved_revision.review_state, RevisionReviewState.APPROVED)

    def test_reject_preserves_revision_and_allows_new_immutable_revision(self) -> None:
        current, revision = ready_revision()
        rejected, old_revision = reject_revision(current, revision, reason="identity drift")
        queued, new_revision = queue_revision(
            rejected,
            revision_id="revision-2",
            request=request().model_copy(update={"request_id": "request-2"}),
            seed=43,
            parent_revision_id=revision.revision_id,
        )
        self.assertEqual(old_revision.review_state, RevisionReviewState.REJECTED)
        self.assertEqual(new_revision.revision_number, 2)
        self.assertEqual(new_revision.parent_revision_id, revision.revision_id)
        self.assertEqual(queued.revision_ids, ("revision-1", "revision-2"))

    def test_modify_creates_new_revision_without_overwriting_source(self) -> None:
        current, revision = ready_revision()
        current, revision = begin_modification(current, revision)
        current, source, revised = complete_modification(
            current,
            revision,
            revision_id="revision-2",
            result_asset_id="video-2",
            replacement_frame_map={12: "frame-12-edited"},
            provenance_id="prov-2",
        )
        self.assertEqual(source.result_asset_id, "video-1")
        self.assertEqual(source.review_state, RevisionReviewState.SUPERSEDED)
        self.assertEqual(revised.parent_revision_id, "revision-1")
        self.assertEqual(revised.result_asset_id, "video-2")
        self.assertEqual(current.state, SegmentState.READY_FOR_REVIEW)

    def test_invalid_transition_is_rejected(self) -> None:
        current, revision = ready_revision()
        with self.assertRaises(InvalidReviewTransition):
            start_generation(current, revision)

    def test_stale_segment_cannot_remain_falsely_approved(self) -> None:
        current, revision = ready_revision()
        current, _revision = approve_revision(current, revision)
        stale = mark_segment_stale(current, "authored keyframe changed")
        self.assertEqual(stale.state, SegmentState.STALE)
        self.assertIsNone(stale.current_approved_revision_id)

        queued, replacement = queue_revision(
            stale,
            revision_id="revision-2",
            request=request().model_copy(update={"request_id": "request-2"}),
            seed=43,
            parent_revision_id=revision.revision_id,
        )
        self.assertEqual(queued.state, SegmentState.QUEUED)
        self.assertEqual(replacement.parent_revision_id, revision.revision_id)


if __name__ == "__main__":
    unittest.main()
