"""Strict immutable model base and shared validation helpers."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


Identifier = Annotated[str, Field(min_length=1, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")]
Milliseconds = Annotated[int, Field(ge=0)]


class DomainModel(BaseModel):
    """Base for canonical records: immutable, strict about unknown fields."""

    model_config = ConfigDict(extra="forbid", frozen=True, validate_assignment=True)


def require_unique(values: list[str] | tuple[str, ...], label: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{label} must be unique")

