"""Non-destructive character-sheet and timeline keyframe workflows."""

from __future__ import annotations

from wan2core.assets import AssetKind, AssetRef
from wan2core.characters import CharacterSheet, PoseViewEntry
from wan2core.keyframes import Keyframe
from wan2core.projects import Wan2LabProject
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


__all__ = ["add_timeline_keyframe", "register_pose_view_entry"]

