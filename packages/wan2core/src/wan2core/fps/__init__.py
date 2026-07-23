"""Duration-preserving deterministic FPS conversion planning."""

from __future__ import annotations

from pydantic import Field

from wan2core.base import DomainModel


class FpsConversionPlan(DomainModel):
    duration_ms: int = Field(gt=0)
    input_fps: float = Field(gt=0.0)
    input_frame_count: int = Field(gt=0)
    output_fps: float = Field(gt=0.0)
    output_frame_count: int = Field(gt=0)
    method: str = "ffmpeg_fps_duplicate_drop"
    filter_expression: str


def plan_fps_conversion(
    *,
    duration_ms: int,
    input_fps: float,
    input_frame_count: int,
    output_fps: float,
) -> FpsConversionPlan:
    if duration_ms <= 0:
        raise ValueError("duration must be positive")
    if input_fps <= 0 or output_fps <= 0:
        raise ValueError("FPS values must be positive")
    if input_frame_count <= 0:
        raise ValueError("input frame count must be positive")
    output_frame_count = max(1, round(duration_ms * output_fps / 1000.0))
    return FpsConversionPlan(
        duration_ms=duration_ms,
        input_fps=input_fps,
        input_frame_count=input_frame_count,
        output_fps=output_fps,
        output_frame_count=output_frame_count,
        filter_expression=f"fps={output_fps:g}",
    )


__all__ = ["FpsConversionPlan", "plan_fps_conversion"]

