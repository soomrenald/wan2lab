"""Reproducibility records shared by generated and edited assets."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from wan2core.base import DomainModel, Identifier


class ProvenanceRecord(DomainModel):
    provenance_id: Identifier
    operation: str = Field(min_length=1)
    created_at: datetime
    model_identifiers: tuple[str, ...] = ()
    model_hashes: dict[str, str] = Field(default_factory=dict)
    backend_id: str = ""
    backend_version: str = ""
    parameters: dict[str, object] = Field(default_factory=dict)
    prompts: dict[str, str] = Field(default_factory=dict)
    seed: int | None = None
    input_asset_ids: tuple[Identifier, ...] = ()
    output_asset_ids: tuple[Identifier, ...] = ()
    parent_provenance_ids: tuple[Identifier, ...] = ()
    warnings: tuple[str, ...] = ()
    runtime: dict[str, object] = Field(default_factory=dict)


__all__ = ["ProvenanceRecord"]

