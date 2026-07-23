"""Dependency-aware invalidation that never silently regenerates work."""

from __future__ import annotations

from collections.abc import Iterable

from wan2core.export import ExportState
from wan2core.projects import Wan2LabProject
from wan2core.review import mark_segment_stale


def invalidate_segments(
    project: Wan2LabProject,
    segment_ids: Iterable[str],
    *,
    reason: str,
) -> Wan2LabProject:
    selected = set(segment_ids)
    if not selected:
        return project
    known = {segment.segment_id for segment in project.segments}
    missing = selected - known
    if missing:
        raise ValueError(f"cannot invalidate missing segments: {', '.join(sorted(missing))}")
    segments = tuple(
        mark_segment_stale(segment, reason) if segment.segment_id in selected else segment
        for segment in project.segments
    )
    exports = tuple(
        export.model_copy(update={"state": ExportState.STALE, "stale_reason": reason})
        for export in project.exports
    )
    return project.model_copy(update={"segments": segments, "exports": exports})


def invalidate_for_keyframe(
    project: Wan2LabProject,
    keyframe_id: str,
    *,
    reason: str = "authored keyframe changed",
) -> Wan2LabProject:
    affected = (
        segment.segment_id
        for segment in project.segments
        if segment.start_keyframe_id == keyframe_id or segment.end_keyframe_id == keyframe_id
    )
    return invalidate_segments(project, affected, reason=reason)


def change_output_fps(project: Wan2LabProject, output_fps: float) -> Wan2LabProject:
    if output_fps <= 0:
        raise ValueError("output FPS must be positive")
    reason = "output FPS changed"
    settings = project.project_settings.model_copy(update={"output_fps": output_fps})
    timeline = project.timeline.model_copy(update={"output_fps": output_fps})
    exports = tuple(
        export.model_copy(update={"state": ExportState.STALE, "stale_reason": reason})
        for export in project.exports
    )
    return project.model_copy(
        update={"project_settings": settings, "timeline": timeline, "exports": exports}
    )


__all__ = ["change_output_fps", "invalidate_for_keyframe", "invalidate_segments"]

