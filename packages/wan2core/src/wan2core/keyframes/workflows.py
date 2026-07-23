"""Non-destructive character-sheet and timeline keyframe workflows."""

from __future__ import annotations

from wan2core.assets import AssetKind, AssetRef
from wan2core.characters import (
    AppearanceProfile,
    ApprovalState,
    CharacterSheet,
    PoseViewEntry,
    StyleDuplicationEntry,
    duplicate_sheet_style,
)
from wan2core.keyframes import Keyframe
from wan2core.projects import Wan2LabProject
from wan2core.projects.invalidation import invalidate_segments
from wan2core.provenance import ProvenanceRecord


def register_pose_view_entry(
    project: Wan2LabProject,
    *,
    sheet_id: str,
    entry: PoseViewEntry,
    asset: AssetRef,
    provenance: ProvenanceRecord,
) -> Wan2LabProject:
    if asset.kind is not AssetKind.IMAGE:
        raise ValueError("character-sheet entries require image assets")
    if entry.image_asset_id != asset.asset_id or entry.provenance_id != provenance.provenance_id:
        raise ValueError("entry, asset, and provenance references do not match")
    sheets: list[CharacterSheet] = []
    found = False
    for sheet in project.character_sheets:
        if sheet.sheet_id != sheet_id:
            sheets.append(sheet)
            continue
        found = True
        sheets.append(sheet.model_copy(update={"entries": (*sheet.entries, entry)}))
    if not found:
        raise KeyError(sheet_id)
    updated = project.model_copy(
        update={
            "assets": _append_asset(project.assets, asset),
            "generation_records": _append_provenance(
                project.generation_records, provenance
            ),
            "character_sheets": tuple(sheets),
        }
    )
    return Wan2LabProject.model_validate(updated.model_dump())


def update_pose_view_entry(
    project: Wan2LabProject,
    *,
    sheet_id: str,
    entry_id: str,
    name: str | None = None,
    approval_state: ApprovalState | None = None,
) -> Wan2LabProject:
    """Rename or review one entry without modifying its immutable image asset."""

    sheets: list[CharacterSheet] = []
    found = False
    for sheet in project.character_sheets:
        if sheet.sheet_id != sheet_id:
            sheets.append(sheet)
            continue
        entries: list[PoseViewEntry] = []
        for entry in sheet.entries:
            if entry.entry_id != entry_id:
                entries.append(entry)
                continue
            found = True
            changes: dict[str, object] = {}
            if name is not None:
                stripped = name.strip()
                if not stripped:
                    raise ValueError("pose/view entry name cannot be empty")
                changes["name"] = stripped
            if approval_state is not None:
                changes["approval_state"] = approval_state
            entries.append(entry.model_copy(update=changes))
        sheets.append(sheet.model_copy(update={"entries": tuple(entries)}))
    if not found:
        raise KeyError(entry_id)
    updated = project.model_copy(update={"character_sheets": tuple(sheets)})
    return Wan2LabProject.model_validate(updated.model_dump())


def remove_pose_view_entry(
    project: Wan2LabProject,
    *,
    sheet_id: str,
    entry_id: str,
) -> Wan2LabProject:
    """Remove a library reference while preserving its immutable asset/provenance."""

    sheets: list[CharacterSheet] = []
    found = False
    for sheet in project.character_sheets:
        if sheet.sheet_id != sheet_id:
            sheets.append(sheet)
            continue
        entries = tuple(entry for entry in sheet.entries if entry.entry_id != entry_id)
        found = len(entries) != len(sheet.entries)
        sheets.append(sheet.model_copy(update={"entries": entries}))
    if not found:
        raise KeyError(entry_id)
    updated = project.model_copy(update={"character_sheets": tuple(sheets)})
    return Wan2LabProject.model_validate(updated.model_dump())


def register_style_duplication(
    project: Wan2LabProject,
    *,
    source_sheet_id: str,
    target_profile: AppearanceProfile,
    target_sheet_id: str,
    target_name: str,
    replacements: tuple[StyleDuplicationEntry, ...],
    assets: tuple[AssetRef, ...],
    provenance: tuple[ProvenanceRecord, ...],
) -> Wan2LabProject:
    """Commit completed Krea restyles as a new sheet; never mutate the source."""

    source = next(
        (sheet for sheet in project.character_sheets if sheet.sheet_id == source_sheet_id),
        None,
    )
    if source is None:
        raise KeyError(source_sheet_id)
    if target_profile.identity_id != source.identity_id:
        raise ValueError("restyled appearance must belong to the source identity")
    replacement_assets = {item.target_asset_id for item in replacements}
    supplied_assets = {item.asset_id for item in assets}
    if replacement_assets != supplied_assets:
        raise ValueError("restyle replacements and output assets do not match")
    replacement_provenance = {item.provenance_id for item in replacements}
    supplied_provenance = {item.provenance_id for item in provenance}
    if replacement_provenance != supplied_provenance:
        raise ValueError("restyle replacements and provenance do not match")
    if any(asset.kind is not AssetKind.IMAGE for asset in assets):
        raise ValueError("restyled character-sheet outputs must be images")

    target = duplicate_sheet_style(
        source,
        target_sheet_id=target_sheet_id,
        target_name=target_name,
        target_appearance_id=target_profile.appearance_id,
        replacements=replacements,
    )
    identities = tuple(
        identity.model_copy(
            update={"character_sheet_ids": (*identity.character_sheet_ids, target.sheet_id)}
        )
        if identity.identity_id == source.identity_id
        else identity
        for identity in project.characters
    )
    updated = project.model_copy(
        update={
            "assets": (*project.assets, *assets),
            "appearance_profiles": (*project.appearance_profiles, target_profile),
            "character_sheets": (*project.character_sheets, target),
            "characters": identities,
            "generation_records": (*project.generation_records, *provenance),
        }
    )
    return Wan2LabProject.model_validate(updated.model_dump())


def add_timeline_keyframe(
    project: Wan2LabProject,
    *,
    keyframe: Keyframe,
    asset: AssetRef,
    provenance: ProvenanceRecord,
) -> Wan2LabProject:
    if asset.kind is not AssetKind.IMAGE:
        raise ValueError("keyframes require image assets")
    if keyframe.time_ms > project.timeline.duration_ms:
        raise ValueError("keyframe time exceeds timeline duration")
    if keyframe.image_asset_id != asset.asset_id:
        raise ValueError("keyframe and image asset IDs do not match")
    if keyframe.provenance_id != provenance.provenance_id:
        raise ValueError("keyframe and provenance IDs do not match")
    keyframes = tuple(sorted((*project.keyframes, keyframe), key=lambda item: item.time_ms))
    times = [item.time_ms for item in keyframes]
    if len(times) != len(set(times)):
        raise ValueError("only one keyframe may occupy an exact timeline time")
    timeline = project.timeline.model_copy(
        update={"keyframe_ids": tuple(item.keyframe_id for item in keyframes)}
    )
    updated = project.model_copy(
        update={
            "assets": _append_asset(project.assets, asset),
            "generation_records": _append_provenance(
                project.generation_records, provenance
            ),
            "keyframes": keyframes,
            "timeline": timeline,
        }
    )
    return Wan2LabProject.model_validate(updated.model_dump())


def revise_timeline_keyframe(
    project: Wan2LabProject,
    *,
    source_keyframe_id: str,
    revised_keyframe: Keyframe,
    asset: AssetRef,
    provenance: ProvenanceRecord,
) -> Wan2LabProject:
    source = next(
        (item for item in project.keyframes if item.keyframe_id == source_keyframe_id),
        None,
    )
    if source is None:
        raise KeyError(source_keyframe_id)
    if revised_keyframe.parent_keyframe_id != source.keyframe_id:
        raise ValueError("revised keyframe must reference its immutable parent")
    if revised_keyframe.time_ms != source.time_ms:
        raise ValueError("keyframe refinement must preserve exact timeline time")
    if revised_keyframe.approved or revised_keyframe.locked:
        raise ValueError("a revised keyframe requires explicit review")
    if asset.kind is not AssetKind.IMAGE or asset.asset_id != revised_keyframe.image_asset_id:
        raise ValueError("revised keyframe requires its matching image asset")
    if provenance.provenance_id != revised_keyframe.provenance_id:
        raise ValueError("revised keyframe requires matching provenance")
    keyframes = tuple(
        revised_keyframe if item.keyframe_id == source.keyframe_id else item
        for item in project.keyframes
    )
    segments = tuple(
        item.model_copy(
            update={
                "start_keyframe_id": (
                    revised_keyframe.keyframe_id
                    if item.start_keyframe_id == source.keyframe_id
                    else item.start_keyframe_id
                ),
                "end_keyframe_id": (
                    revised_keyframe.keyframe_id
                    if item.end_keyframe_id == source.keyframe_id
                    else item.end_keyframe_id
                ),
            }
        )
        for item in project.segments
    )
    timeline = project.timeline.model_copy(
        update={
            "keyframe_ids": tuple(
                revised_keyframe.keyframe_id
                if item == source.keyframe_id
                else item
                for item in project.timeline.keyframe_ids
            )
        }
    )
    updated = Wan2LabProject.model_validate(
        project.model_copy(
            update={
                "assets": (*project.assets, asset),
                "keyframes": keyframes,
                "segments": segments,
                "timeline": timeline,
                "generation_records": (*project.generation_records, provenance),
            }
        ).model_dump()
    )
    affected = tuple(
        item.segment_id
        for item in updated.segments
        if item.start_keyframe_id == revised_keyframe.keyframe_id
        or item.end_keyframe_id == revised_keyframe.keyframe_id
    )
    return Wan2LabProject.model_validate(
        invalidate_segments(
            updated,
            affected,
            reason="authored keyframe refined and requires segment replanning",
        ).model_dump()
    )


def _append_asset(existing: tuple[AssetRef, ...], item: AssetRef) -> tuple[AssetRef, ...]:
    if any(current.asset_id == item.asset_id for current in existing):
        raise ValueError(f"asset ID already exists: {item.asset_id}")
    return (*existing, item)


def _append_provenance(
    existing: tuple[ProvenanceRecord, ...], item: ProvenanceRecord
) -> tuple[ProvenanceRecord, ...]:
    if any(current.provenance_id == item.provenance_id for current in existing):
        raise ValueError(f"provenance ID already exists: {item.provenance_id}")
    return (*existing, item)


__all__ = [
    "add_timeline_keyframe",
    "register_pose_view_entry",
    "register_style_duplication",
    "revise_timeline_keyframe",
    "remove_pose_view_entry",
    "update_pose_view_entry",
]
