"""Exact-time keyframes and regional character assignments."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from wan2core.base import DomainModel, Identifier, Milliseconds, require_unique


class Rectangle(DomainModel):
    x0: float = Field(ge=0.0)
    y0: float = Field(ge=0.0)
    x1: float = Field(gt=0.0)
    y1: float = Field(gt=0.0)

    @model_validator(mode="after")
    def validate_extent(self) -> "Rectangle":
        if self.x1 <= self.x0 or self.y1 <= self.y0:
            raise ValueError("rectangle must have positive width and height")
        return self


class AdapterSelection(DomainModel):
    adapter_id: Identifier
    strength: float = Field(default=1.0, ge=-10.0, le=10.0)


class CharacterRegionAssignment(DomainModel):
    region_id: Identifier
    name: str = Field(min_length=1)
    rectangle: Rectangle
    identity_id: Identifier
    appearance_id: Identifier
    pose_view_entry_id: Identifier
    prompt: str = ""
    negative_prompt: str = ""
    adapters: tuple[AdapterSelection, ...] = ()
    priority: int = 0

    @model_validator(mode="after")
    def validate_adapters(self) -> "CharacterRegionAssignment":
        require_unique([item.adapter_id for item in self.adapters], "regional adapter IDs")
        return self


class KeyframeSource(StrEnum):
    IMPORTED = "imported"
    KREA_GENERATED = "krea_generated"
    EXTRACTED_VIDEO = "extracted_video"
    EDITED = "edited"


class Keyframe(DomainModel):
    keyframe_id: Identifier
    time_ms: Milliseconds
    image_asset_id: Identifier
    source_type: KeyframeSource
    scene_prompt: str = ""
    environment_prompt: str = ""
    lighting_prompt: str = ""
    region_assignments: tuple[CharacterRegionAssignment, ...] = ()
    mannequin_scene_id: Identifier | None = None
    provenance_id: Identifier
    approved: bool = False
    locked: bool = False
    parent_keyframe_id: Identifier | None = None
    source_frame_asset_id: Identifier | None = None

    @model_validator(mode="after")
    def validate_keyframe(self) -> "Keyframe":
        require_unique(
            [assignment.region_id for assignment in self.region_assignments],
            "keyframe region IDs",
        )
        if self.locked and not self.approved:
            raise ValueError("a locked keyframe must be approved")
        return self


__all__ = [
    "AdapterSelection",
    "CharacterRegionAssignment",
    "Keyframe",
    "KeyframeSource",
    "Rectangle",
]
