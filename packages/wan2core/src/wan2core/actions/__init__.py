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


def compile_action_prompt(
    base_prompt: str,
    action: ActionSpec | None,
) -> tuple[str, tuple[str, ...]]:
    if action is None:
        return base_prompt.strip(), ()
    controls = (
        ("motion", action.motion_instruction),
        ("character trajectory", action.character_trajectory),
        ("camera trajectory", action.camera_trajectory),
        ("contact constraints", "; ".join(action.contact_constraints)),
        ("speed/easing", action.speed_easing),
        (
            "pose preference",
            f"{action.pose_accuracy_preference:.2f} toward pose accuracy, "
            f"{1 - action.pose_accuracy_preference:.2f} toward natural movement",
        ),
    )
    fragments = [base_prompt.strip()] if base_prompt.strip() else []
    supported = []
    for label, value in controls:
        if value.strip():
            fragments.append(f"{label}: {value.strip()}")
            supported.append(label)
    return ". ".join(fragments), tuple(supported)


__all__ = ["ActionSpec", "compile_action_prompt"]
