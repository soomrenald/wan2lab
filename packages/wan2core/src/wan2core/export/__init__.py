"""FFmpeg command planning for approved segment assembly."""

from __future__ import annotations

from enum import StrEnum
from pathlib import PurePosixPath

from pydantic import Field, model_validator

from wan2core.base import DomainModel, Identifier
from wan2core.fps import FpsConversionPlan, plan_fps_conversion
from wan2core.segments import Segment, SegmentRevision, SegmentState


class SegmentExportInput(DomainModel):
    segment_id: Identifier
    revision_id: Identifier
    source_path: str = Field(min_length=1)
    duration_ms: int = Field(gt=0)
    generation_fps: float = Field(gt=0.0)
    frame_count: int = Field(gt=0)


class FfmpegCommand(DomainModel):
    stage: str = Field(min_length=1)
    arguments: tuple[str, ...]
    output_path: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_arguments(self) -> "FfmpegCommand":
        if not self.arguments or not self.arguments[0]:
            raise ValueError("FFmpeg command must be an argument array")
        return self


class ExportState(StrEnum):
    PLANNED = "planned"
    RUNNING = "running"
    COMPLETE = "complete"
    STALE = "stale"
    ERROR = "error"


class ExportPlan(DomainModel):
    export_id: Identifier
    output_path: str = Field(min_length=1)
    output_fps: float = Field(gt=0.0)
    segment_inputs: tuple[SegmentExportInput, ...]
    fps_plans: tuple[FpsConversionPlan, ...]
    commands: tuple[FfmpegCommand, ...]
    concat_manifest_entries: tuple[str, ...]
    provenance_id: Identifier
    state: ExportState = ExportState.PLANNED
    stale_reason: str | None = None

    @model_validator(mode="after")
    def validate_state(self) -> "ExportPlan":
        if self.state is ExportState.STALE and not self.stale_reason:
            raise ValueError("stale export requires a reason")
        return self


def build_export_plan(
    *,
    export_id: Identifier,
    segments: tuple[Segment, ...],
    revisions: tuple[SegmentRevision, ...],
    source_paths: dict[str, str],
    output_path: str,
    output_fps: float,
    ffmpeg_executable: str,
    work_directory: str = "export_work",
    provenance_id: Identifier,
) -> ExportPlan:
    """Build deterministic argument arrays; execution remains adapter-owned."""

    if not ffmpeg_executable.strip():
        raise ValueError("FFmpeg executable must not be empty")
    by_id = {revision.revision_id: revision for revision in revisions}
    ordered = sorted(segments, key=lambda segment: segment.start_ms)
    cursor = 0
    inputs: list[SegmentExportInput] = []
    fps_plans: list[FpsConversionPlan] = []
    commands: list[FfmpegCommand] = []
    manifest: list[str] = []
    for index, segment in enumerate(ordered, start=1):
        if segment.start_ms != cursor:
            raise ValueError("export segments contain a gap or overlap")
        cursor = segment.end_ms
        if segment.state is not SegmentState.APPROVED_LOCKED:
            raise ValueError(f"segment {segment.segment_id} is not approved and current")
        revision_id = segment.current_approved_revision_id
        if revision_id is None or revision_id not in by_id:
            raise ValueError(f"segment {segment.segment_id} has no approved revision record")
        revision = by_id[revision_id]
        if revision.result_asset_id is None or revision.review_state.value != "approved":
            raise ValueError(f"segment {segment.segment_id} revision is not approved")
        source_path = source_paths.get(revision.result_asset_id)
        if not source_path:
            raise ValueError(f"missing source path for asset {revision.result_asset_id}")
        request = revision.source_request
        duration_ms = segment.end_ms - segment.start_ms
        item = SegmentExportInput(
            segment_id=segment.segment_id,
            revision_id=revision_id,
            source_path=source_path,
            duration_ms=duration_ms,
            generation_fps=request.generation_fps,
            frame_count=request.frame_count,
        )
        fps_plan = plan_fps_conversion(
            duration_ms=duration_ms,
            input_fps=request.generation_fps,
            input_frame_count=request.frame_count,
            output_fps=output_fps,
        )
        normalized = str(PurePosixPath(work_directory) / f"segment-{index:04d}.mp4")
        commands.append(
            FfmpegCommand(
                stage=f"normalize_segment_{index}",
                arguments=(
                    ffmpeg_executable,
                    "-y",
                    "-i",
                    source_path,
                    "-vf",
                    fps_plan.filter_expression,
                    "-an",
                    normalized,
                ),
                output_path=normalized,
            )
        )
        inputs.append(item)
        fps_plans.append(fps_plan)
        manifest.append(normalized)

    manifest_path = str(PurePosixPath(work_directory) / "segments.txt")
    commands.append(
        FfmpegCommand(
            stage="concatenate",
            arguments=(
                ffmpeg_executable,
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                manifest_path,
                "-c",
                "copy",
                output_path,
            ),
            output_path=output_path,
        )
    )
    return ExportPlan(
        export_id=export_id,
        output_path=output_path,
        output_fps=output_fps,
        segment_inputs=tuple(inputs),
        fps_plans=tuple(fps_plans),
        commands=tuple(commands),
        concat_manifest_entries=tuple(manifest),
        provenance_id=provenance_id,
    )


__all__ = [
    "ExportPlan",
    "ExportState",
    "FfmpegCommand",
    "SegmentExportInput",
    "build_export_plan",
]
