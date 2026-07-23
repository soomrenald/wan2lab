"""Wan project asset records."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from wan2core.base import DomainModel, Identifier, Milliseconds


class AssetKind(StrEnum):
    IMAGE = "image"
    VIDEO = "video"
    FRAME_SEQUENCE = "frame_sequence"
    MASK = "mask"
    DEPTH = "depth"
    MANNEQUIN_GUIDE = "mannequin_guide"
    PROJECT = "project"


class AssetRef(DomainModel):
    asset_id: Identifier
    kind: AssetKind
    storage_path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    width: int | None = Field(default=None, gt=0)
    height: int | None = Field(default=None, gt=0)
    frame_count: int | None = Field(default=None, gt=0)
    duration_ms: Milliseconds | None = None
    parent_asset_ids: tuple[Identifier, ...] = ()
    creation_operation_id: Identifier | None = None
    immutable_source: bool = True

    @model_validator(mode="after")
    def validate_storage_path(self) -> "AssetRef":
        if self.storage_path.startswith(("/", "\\")):
            raise ValueError("asset storage_path must be project-relative or an adapter key")
        if ".." in self.storage_path.replace("\\", "/").split("/"):
            raise ValueError("asset storage_path must not escape its storage adapter")
        if self.kind in {AssetKind.IMAGE, AssetKind.MASK, AssetKind.DEPTH}:
            if self.width is None or self.height is None:
                raise ValueError("image-like assets require width and height")
        return self


__all__ = ["AssetKind", "AssetRef"]

