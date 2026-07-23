"""Canonical timeline and backend-aware segment derivation."""

from __future__ import annotations

from pydantic import Field, model_validator

from wan2core.backends import BackendCapabilities, FrameRounding, WanMode
from wan2core.base import DomainModel, Identifier, Milliseconds, require_unique
from wan2core.keyframes import Keyframe
from wan2core.segments import ContinuationPolicy, PlannedSegment


class Timeline(DomainModel):
    duration_ms: Milliseconds
    output_fps: float = Field(gt=0.0)
    keyframe_ids: tuple[Identifier, ...] = ()
    segment_ids: tuple[Identifier, ...] = ()

    @model_validator(mode="after")
    def validate_references(self) -> "Timeline":
        if self.duration_ms <= 0:
            raise ValueError("timeline duration must be positive")
        require_unique(self.keyframe_ids, "timeline keyframe IDs")
        require_unique(self.segment_ids, "timeline segment IDs")
        return self


class SegmentPlan(DomainModel):
    timeline_duration_ms: int = Field(gt=0)
    backend_id: Identifier
    model_id: Identifier
    segments: tuple[PlannedSegment, ...]
    warnings: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_coverage(self) -> "SegmentPlan":
        if not self.segments:
            raise ValueError("segment plan must contain at least one segment")
        cursor = 0
        for segment in self.segments:
            if segment.start_ms != cursor:
                raise ValueError("segment plan must cover the timeline without gaps or overlaps")
            cursor = segment.end_ms
        if cursor != self.timeline_duration_ms:
            raise ValueError("segment plan must end at the timeline duration")
        return self


def plan_segments(
    timeline: Timeline,
    keyframes: tuple[Keyframe, ...],
    capabilities: BackendCapabilities,
    *,
    model_id: str,
    default_segment_budget_ms: int = 5_000,
    generation_fps: float | None = None,
    frame_rounding: FrameRounding = FrameRounding.NEAREST,
    continuation_policy: ContinuationPolicy = ContinuationPolicy.AUTHORED_ANCHOR,
) -> SegmentPlan:
    """Derive bounded, gap-free jobs from arbitrary exact-time keyframes."""

    if default_segment_budget_ms <= 0:
        raise ValueError("default segment budget must be positive")
    model = capabilities.model(model_id)
    fps = generation_fps or model.default_generation_fps
    if fps not in model.supported_generation_fps:
        raise ValueError(f"unsupported generation FPS: {fps}")

    by_id = {keyframe.keyframe_id: keyframe for keyframe in keyframes}
    if len(by_id) != len(keyframes):
        raise ValueError("keyframe IDs must be unique")
    missing = [keyframe_id for keyframe_id in timeline.keyframe_ids if keyframe_id not in by_id]
    if missing:
        raise ValueError(f"timeline references missing keyframes: {', '.join(missing)}")
    selected = sorted((by_id[keyframe_id] for keyframe_id in timeline.keyframe_ids), key=lambda item: item.time_ms)
    unapproved = [item.keyframe_id for item in selected if not item.approved or not item.locked]
    if unapproved:
        raise ValueError(
            "timeline contains keyframes that are not approved and locked: "
            + ", ".join(unapproved)
        )
    if any(keyframe.time_ms > timeline.duration_ms for keyframe in selected):
        raise ValueError("keyframe time exceeds timeline duration")
    times = [keyframe.time_ms for keyframe in selected]
    if len(times) != len(set(times)):
        raise ValueError("only one authored keyframe may occupy an exact timeline time")

    max_native_ms = model.frame_duration_ms(model.max_frame_count, fps)
    budget_ms = min(default_segment_budget_ms, max_native_ms)
    if budget_ms <= 0:
        raise ValueError("backend capabilities do not permit a positive segment duration")

    anchors: list[tuple[int, Keyframe | None]] = [(0, None)]
    for keyframe in selected:
        if keyframe.time_ms == 0:
            anchors[0] = (0, keyframe)
        else:
            anchors.append((keyframe.time_ms, keyframe))
    if anchors[-1][0] < timeline.duration_ms:
        anchors.append((timeline.duration_ms, None))

    planned: list[PlannedSegment] = []
    warnings: list[str] = []
    sequence = 1
    for (interval_start, start_anchor), (interval_end, end_anchor) in zip(anchors, anchors[1:]):
        if interval_end <= interval_start:
            continue
        cursor = interval_start
        while cursor < interval_end:
            end = min(interval_end, cursor + budget_ms)
            is_first = cursor == interval_start
            is_final = end == interval_end
            effective_start = start_anchor if is_first else None
            effective_end = end_anchor if is_final else None
            mode, review_target = _select_mode(
                model.supported_modes,
                has_start_anchor=effective_start is not None or not is_first,
                end_anchor=effective_end,
            )
            frame_count = model.resolve_frame_count(end - cursor, fps, frame_rounding)
            actual_duration = model.frame_duration_ms(frame_count, fps)
            if abs(actual_duration - (end - cursor)) >= 100:
                warnings.append(
                    f"segment-{sequence} requested {end - cursor} ms but backend frames represent "
                    f"{actual_duration} ms"
                )
            planned.append(
                PlannedSegment(
                    segment_id=f"segment-{sequence}",
                    start_ms=cursor,
                    end_ms=end,
                    requested_duration_ms=end - cursor,
                    actual_duration_ms=actual_duration,
                    start_keyframe_id=effective_start.keyframe_id if effective_start else None,
                    end_keyframe_id=effective_end.keyframe_id if effective_end else None,
                    end_anchor_is_review_target=review_target,
                    mode=mode,
                    backend_id=capabilities.backend_id,
                    model_id=model.model_id,
                    generation_fps=fps,
                    frame_count=frame_count,
                    output_fps=timeline.output_fps,
                    continuation_policy=(
                        ContinuationPolicy.DUAL_BOUNDARY
                        if mode is WanMode.FIRST_LAST
                        else continuation_policy
                    ),
                )
            )
            sequence += 1
            cursor = end

    return SegmentPlan(
        timeline_duration_ms=timeline.duration_ms,
        backend_id=capabilities.backend_id,
        model_id=model.model_id,
        segments=tuple(planned),
        warnings=tuple(warnings),
    )


def _select_mode(
    supported_modes: frozenset[WanMode],
    *,
    has_start_anchor: bool,
    end_anchor: Keyframe | None,
) -> tuple[WanMode, bool]:
    if has_start_anchor and end_anchor is not None:
        if WanMode.FIRST_LAST in supported_modes:
            return WanMode.FIRST_LAST, False
        if WanMode.I2V in supported_modes:
            return WanMode.I2V, True
        raise ValueError("backend cannot generate an interval between authored anchors")
    if has_start_anchor:
        if WanMode.I2V in supported_modes:
            return WanMode.I2V, False
        raise ValueError("backend cannot generate from a start anchor")
    if WanMode.PROMPT in supported_modes:
        return WanMode.PROMPT, end_anchor is not None
    raise ValueError("backend cannot generate a prompt-only interval")


__all__ = ["SegmentPlan", "Timeline", "plan_segments"]
