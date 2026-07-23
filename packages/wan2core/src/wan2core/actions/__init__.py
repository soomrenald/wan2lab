"""Structured action intent attached to a generated segment."""

from __future__ import annotations

from pydantic import Field

from wan2core.base import DomainModel, Identifier


class ActionSpec(DomainModel):
    action_id: Identifier
    motion_instruction: str = ""
    starting_pose_ref: Identifier | None = None
    ending_pose_ref: Identifier | None = None
    character_trajectory: str = ""
    camera_trajectory: str = ""
    contact_constraints: tuple[str, ...] = ()
    speed_easing: str = ""
    driving_video_asset_id: Identifier | None = None
    pose_accuracy_preference: float = Field(default=0.5, ge=0.0, le=1.0)


__all__ = ["ActionSpec"]

