"""Non-destructive single- and batch-frame edit records."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from wan2core.base import DomainModel, Identifier
from wan2core.keyframes import AdapterSelection, Rectangle


class FrameEditOperation(StrEnum):
    IMAGE_EDIT = "image_edit"
    REGIONAL_EDIT = "regional_edit"
    FACE_REFINEMENT = "face_refinement"
    MANNEQUIN_GUIDED = "mannequin_guided"


class BoundaryPropagation(StrEnum):
    LOCAL_REPAIR = "local_repair"
    PROPAGATE_AS_ANCHOR = "propagate_as_anchor"


class FrameEditRecord(DomainModel):
    edit_id: Identifier
    segment_revision_id: Identifier
    original_frame_asset_id: Identifier
    replacement_frame_asset_id: Identifier
    frame_index: int = Field(ge=0)
    operation_type: FrameEditOperation
    prompt: str = ""
    settings: dict[str, object] = Field(default_factory=dict)
    region: Rectangle | None = None
    mask_asset_id: Identifier | None = None
    identity_id: Identifier | None = None
    appearance_id: Identifier | None = None
    adapters: tuple[AdapterSelection, ...] = ()
    parent_edit_id: Identifier | None = None
    user_confirmed_face_region: bool = False
    boundary_propagation: BoundaryPropagation = BoundaryPropagation.LOCAL_REPAIR
    provenance_id: Identifier

    @model_validator(mode="after")
    def validate_face_confirmation(self) -> "FrameEditRecord":
        if (
            self.operation_type is FrameEditOperation.FACE_REFINEMENT
            and not self.user_confirmed_face_region
        ):
            raise ValueError("face refinement requires a user-confirmed region")
        if self.boundary_propagation is BoundaryPropagation.PROPAGATE_AS_ANCHOR:
            if self.frame_index < 0:
                raise ValueError("propagated frame index must be valid")
        return self


class BatchFrameSelection(DomainModel):
    frame_indices: tuple[int, ...]

    @model_validator(mode="after")
    def validate_indices(self) -> "BatchFrameSelection":
        if not self.frame_indices:
            raise ValueError("batch selection must contain at least one frame")
        if any(index < 0 for index in self.frame_indices):
            raise ValueError("frame indices must not be negative")
        if tuple(sorted(set(self.frame_indices))) != self.frame_indices:
            raise ValueError("frame indices must be sorted and unique")
        return self


__all__ = [
    "BatchFrameSelection",
    "BoundaryPropagation",
    "FrameEditOperation",
    "FrameEditRecord",
]

