"""Character identity, appearance, and character-sheet records."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from wan2core.base import DomainModel, Identifier, require_unique


class AdapterFamily(StrEnum):
    KREA = "krea"
    WAN = "wan"


class AdapterKind(StrEnum):
    LORA = "lora"
    LOKR = "lokr"


class AdapterRef(DomainModel):
    adapter_id: Identifier
    asset_id: Identifier
    family: AdapterFamily
    kind: AdapterKind
    model_family: str = Field(min_length=1)
    trigger: str = ""
    default_strength: float = Field(default=1.0, ge=-10.0, le=10.0)

    @model_validator(mode="after")
    def validate_model_family(self) -> "AdapterRef":
        if not self.model_family.casefold().startswith(self.family.value):
            raise ValueError(
                f"{self.family.value} adapter model family must start with "
                f"'{self.family.value}'"
            )
        return self


class CharacterIdentity(DomainModel):
    identity_id: Identifier
    name: str = Field(min_length=1)
    identity_prompt: str = Field(min_length=1)
    trigger_text: str = ""
    stable_description: str = ""
    permanent_features: tuple[str, ...] = ()
    adapter_refs: tuple[AdapterRef, ...] = ()
    face_refinement_settings: dict[str, object] = Field(default_factory=dict)
    character_sheet_ids: tuple[Identifier, ...] = ()

    @model_validator(mode="after")
    def validate_references(self) -> "CharacterIdentity":
        require_unique([item.adapter_id for item in self.adapter_refs], "identity adapter IDs")
        require_unique(self.character_sheet_ids, "character sheet IDs")
        return self


class AppearanceProfile(DomainModel):
    appearance_id: Identifier
    identity_id: Identifier
    name: str = Field(min_length=1)
    style_prompt: str = ""
    clothing_state: str = ""
    hairstyle_state: str = ""
    makeup_accessory_state: str = ""
    visible_features: tuple[str, ...] = ()
    nudity_state: str | None = None
    adapter_refs: tuple[AdapterRef, ...] = ()

    @model_validator(mode="after")
    def validate_adapter_families(self) -> "AppearanceProfile":
        if any(adapter.family is not AdapterFamily.KREA for adapter in self.adapter_refs):
            raise ValueError("appearance-profile adapters must be compatible with Krea")
        return self


class PoseViewSource(StrEnum):
    IMPORTED = "imported"
    GENERATED = "generated"
    EDITED = "edited"
    DERIVED_STYLE_COPY = "derived_style_copy"


class ApprovalState(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"
    REJECTED = "rejected"


class PoseViewEntry(DomainModel):
    entry_id: Identifier
    name: str = Field(min_length=1)
    image_asset_id: Identifier
    identity_id: Identifier
    appearance_id: Identifier
    view_label: str = ""
    pose_label: str = ""
    framing_label: str = ""
    expression_label: str = ""
    mannequin_scene_id: Identifier | None = None
    mask_asset_id: Identifier | None = None
    source_type: PoseViewSource
    parent_entry_id: Identifier | None = None
    provenance_id: Identifier
    approval_state: ApprovalState = ApprovalState.DRAFT


class CharacterSheet(DomainModel):
    sheet_id: Identifier
    name: str = Field(min_length=1)
    identity_id: Identifier
    appearance_id: Identifier
    entries: tuple[PoseViewEntry, ...] = ()

    @model_validator(mode="after")
    def validate_entries(self) -> "CharacterSheet":
        require_unique([entry.entry_id for entry in self.entries], "pose/view entry IDs")
        require_unique([entry.name for entry in self.entries], "pose/view entry names")
        for entry in self.entries:
            if entry.identity_id != self.identity_id:
                raise ValueError("every sheet entry must use the sheet identity")
            if entry.appearance_id != self.appearance_id:
                raise ValueError("every sheet entry must use the sheet appearance")
        return self


class StyleDuplicationEntry(DomainModel):
    source_entry_id: Identifier
    target_entry_id: Identifier
    target_asset_id: Identifier
    provenance_id: Identifier


def duplicate_sheet_style(
    source: CharacterSheet,
    *,
    target_sheet_id: Identifier,
    target_name: str,
    target_appearance_id: Identifier,
    replacements: tuple[StyleDuplicationEntry, ...],
) -> CharacterSheet:
    """Create a non-destructive restyled sheet after image operations complete."""

    by_source = {replacement.source_entry_id: replacement for replacement in replacements}
    if set(by_source) != {entry.entry_id for entry in source.entries}:
        raise ValueError("style duplication requires one replacement for every source entry")
    entries = tuple(
        entry.model_copy(
            update={
                "entry_id": by_source[entry.entry_id].target_entry_id,
                "image_asset_id": by_source[entry.entry_id].target_asset_id,
                "appearance_id": target_appearance_id,
                "source_type": PoseViewSource.DERIVED_STYLE_COPY,
                "parent_entry_id": entry.entry_id,
                "provenance_id": by_source[entry.entry_id].provenance_id,
                "approval_state": ApprovalState.DRAFT,
            }
        )
        for entry in source.entries
    )
    return CharacterSheet(
        sheet_id=target_sheet_id,
        name=target_name,
        identity_id=source.identity_id,
        appearance_id=target_appearance_id,
        entries=entries,
    )


__all__ = [
    "AdapterFamily",
    "AdapterKind",
    "AdapterRef",
    "AppearanceProfile",
    "ApprovalState",
    "CharacterIdentity",
    "CharacterSheet",
    "PoseViewEntry",
    "PoseViewSource",
    "StyleDuplicationEntry",
    "duplicate_sheet_style",
]
