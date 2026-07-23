"""Canonical Wan2Lab project document and cross-record validation."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
from typing import Callable

from pydantic import Field, field_validator, model_validator

from wan2core.actions import ActionSpec
from wan2core.assets import AssetKind, AssetRef
from wan2core.base import DomainModel, Identifier, require_unique
from wan2core.characters import AppearanceProfile, CharacterIdentity, CharacterSheet
from wan2core.editing import FrameEditRecord
from wan2core.export import ExportPlan
from wan2core.identity import CheckpointProposal, IdentityDriftWarning
from wan2core.keyframes import Keyframe
from wan2core.mannequin import MannequinPose, MannequinScene
from wan2core.provenance import ProvenanceRecord
from wan2core.segments import ContinuationPolicy, Segment, SegmentRevision
from wan2core.timeline import SegmentPlan, Timeline


PROJECT_SCHEMA_VERSION = 2


ProjectDocument = dict[str, object]
ProjectMigration = Callable[[ProjectDocument], ProjectDocument]


def _migrate_v1_to_v2(document: ProjectDocument) -> ProjectDocument:
    """Add domain collections introduced after the initial project contract."""

    migrated = deepcopy(document)
    migrated["schema_version"] = 2
    for field_name, default in (
        ("mannequin_poses", []),
        ("segment_plan", None),
        ("identity_warnings", []),
        ("checkpoint_proposals", []),
    ):
        migrated.setdefault(field_name, default)
    return migrated


_PROJECT_MIGRATIONS: dict[int, ProjectMigration] = {
    1: _migrate_v1_to_v2,
}


def migrate_project_data(document: dict[str, object]) -> ProjectDocument:
    """Return a current, validated-ready copy of a historical project mapping."""

    migrated = deepcopy(document)
    raw_version = migrated.get("schema_version", 1)
    if isinstance(raw_version, bool) or not isinstance(raw_version, int):
        raise ValueError("Wan2Lab project schema_version must be an integer")
    if raw_version < 1:
        raise ValueError(f"unsupported Wan2Lab project schema: {raw_version}")
    if raw_version > PROJECT_SCHEMA_VERSION:
        raise ValueError(
            "Wan2Lab project schema "
            f"{raw_version} is newer than supported schema {PROJECT_SCHEMA_VERSION}"
        )
    version = raw_version
    while version < PROJECT_SCHEMA_VERSION:
        migration = _PROJECT_MIGRATIONS.get(version)
        if migration is None:
            raise ValueError(
                f"no Wan2Lab project migration from schema {version} is available"
            )
        migrated = migration(migrated)
        next_version = migrated.get("schema_version")
        if not isinstance(next_version, int) or next_version <= version:
            raise RuntimeError(f"project migration from schema {version} did not advance")
        version = next_version
    return migrated


class ProjectSettings(DomainModel):
    width: int = Field(default=1280, gt=0)
    height: int = Field(default=720, gt=0)
    output_fps: float = Field(default=24.0, gt=0.0)
    default_segment_duration_ms: int = Field(default=5_000, gt=0)
    default_wan_backend_id: Identifier
    default_wan_model_id: Identifier
    default_krea_backend_id: Identifier = "krea-comfyui"
    default_krea_model_id: Identifier = "krea2"
    memory_policy: str = "safe_16gb"
    default_continuation_policy: ContinuationPolicy = ContinuationPolicy.AUTHORED_ANCHOR
    ffmpeg_executable: str = "ffmpeg"
    asset_root: str = "assets"

    @field_validator("asset_root")
    @classmethod
    def validate_asset_root(cls, value: str) -> str:
        normalized = value.replace("\\", "/")
        if not normalized or normalized.startswith("/") or ".." in normalized.split("/"):
            raise ValueError("asset_root must be a project-relative directory")
        return normalized.rstrip("/")


class Wan2LabProject(DomainModel):
    schema_version: int = PROJECT_SCHEMA_VERSION
    project_id: Identifier
    project_settings: ProjectSettings
    assets: tuple[AssetRef, ...] = ()
    characters: tuple[CharacterIdentity, ...] = ()
    appearance_profiles: tuple[AppearanceProfile, ...] = ()
    character_sheets: tuple[CharacterSheet, ...] = ()
    mannequin_scenes: tuple[MannequinScene, ...] = ()
    mannequin_poses: tuple[MannequinPose, ...] = ()
    keyframes: tuple[Keyframe, ...] = ()
    actions: tuple[ActionSpec, ...] = ()
    timeline: Timeline
    segment_plan: SegmentPlan | None = None
    segments: tuple[Segment, ...] = ()
    segment_revisions: tuple[SegmentRevision, ...] = ()
    generation_records: tuple[ProvenanceRecord, ...] = ()
    frame_edit_records: tuple[FrameEditRecord, ...] = ()
    identity_warnings: tuple[IdentityDriftWarning, ...] = ()
    checkpoint_proposals: tuple[CheckpointProposal, ...] = ()
    exports: tuple[ExportPlan, ...] = ()

    @model_validator(mode="after")
    def validate_project(self) -> "Wan2LabProject":
        if self.schema_version != PROJECT_SCHEMA_VERSION:
            raise ValueError(f"unsupported Wan2Lab project schema: {self.schema_version}")
        collections = {
            "asset IDs": [item.asset_id for item in self.assets],
            "character IDs": [item.identity_id for item in self.characters],
            "appearance IDs": [item.appearance_id for item in self.appearance_profiles],
            "sheet IDs": [item.sheet_id for item in self.character_sheets],
            "mannequin scene IDs": [item.scene_id for item in self.mannequin_scenes],
            "mannequin pose IDs": [item.pose_id for item in self.mannequin_poses],
            "keyframe IDs": [item.keyframe_id for item in self.keyframes],
            "action IDs": [item.action_id for item in self.actions],
            "segment IDs": [item.segment_id for item in self.segments],
            "revision IDs": [item.revision_id for item in self.segment_revisions],
            "provenance IDs": [item.provenance_id for item in self.generation_records],
            "frame edit IDs": [item.edit_id for item in self.frame_edit_records],
            "identity warning IDs": [item.warning_id for item in self.identity_warnings],
            "checkpoint proposal IDs": [
                item.proposal_id for item in self.checkpoint_proposals
            ],
            "export IDs": [item.export_id for item in self.exports],
        }
        for label, values in collections.items():
            require_unique(values, label)

        identity_ids = set(collections["character IDs"])
        appearance_by_id = {item.appearance_id: item for item in self.appearance_profiles}
        asset_ids = set(collections["asset IDs"])
        action_ids = set(collections["action IDs"])
        keyframe_ids = set(collections["keyframe IDs"])
        segment_ids = set(collections["segment IDs"])
        revision_by_id = {item.revision_id: item for item in self.segment_revisions}
        provenance_ids = set(collections["provenance IDs"])

        adapters = tuple(
            adapter
            for identity in self.characters
            for adapter in identity.adapter_refs
        ) + tuple(
            adapter
            for appearance in self.appearance_profiles
            for adapter in appearance.adapter_refs
        )
        require_unique([item.adapter_id for item in adapters], "adapter IDs")
        assets_by_id = {item.asset_id: item for item in self.assets}
        for adapter in adapters:
            asset = assets_by_id.get(adapter.asset_id)
            if asset is None:
                raise ValueError("character adapter references a missing asset")
            if asset.kind is not AssetKind.ADAPTER:
                raise ValueError("character adapter must reference an adapter asset")

        for appearance in self.appearance_profiles:
            if appearance.identity_id not in identity_ids:
                raise ValueError("appearance profile references a missing identity")
        for sheet in self.character_sheets:
            appearance = appearance_by_id.get(sheet.appearance_id)
            if sheet.identity_id not in identity_ids or appearance is None:
                raise ValueError("character sheet references missing identity or appearance")
            if appearance.identity_id != sheet.identity_id:
                raise ValueError("character sheet appearance belongs to another identity")
            for entry in sheet.entries:
                if entry.image_asset_id not in asset_ids:
                    raise ValueError("character-sheet entry references a missing image asset")
                if entry.mask_asset_id is not None and entry.mask_asset_id not in asset_ids:
                    raise ValueError("character-sheet entry references a missing mask asset")
                if entry.provenance_id not in provenance_ids:
                    raise ValueError("character-sheet entry references missing provenance")
                if (
                    entry.mannequin_scene_id is not None
                    and entry.mannequin_scene_id not in set(collections["mannequin scene IDs"])
                ):
                    raise ValueError(
                        "character-sheet entry references a missing mannequin scene"
                    )
        sheet_ids = set(collections["sheet IDs"])
        for identity in self.characters:
            if set(identity.character_sheet_ids) - sheet_ids:
                raise ValueError("character identity references a missing character sheet")
        for scene in self.mannequin_scenes:
            referenced_assets = {
                *scene.prop_asset_ids,
                *scene.guide_asset_ids,
                *(item.asset_id for item in scene.props if item.asset_id is not None),
            }
            if scene.imported_asset_id is not None:
                referenced_assets.add(scene.imported_asset_id)
            if referenced_assets - asset_ids:
                raise ValueError("mannequin scene references a missing asset")
        for keyframe in self.keyframes:
            if keyframe.image_asset_id not in asset_ids:
                raise ValueError("keyframe references a missing image asset")
            if keyframe.provenance_id not in provenance_ids:
                raise ValueError("keyframe references missing provenance")
            for assignment in keyframe.region_assignments:
                if assignment.identity_id not in identity_ids:
                    raise ValueError("keyframe region references a missing identity")
                appearance = appearance_by_id.get(assignment.appearance_id)
                if appearance is None or appearance.identity_id != assignment.identity_id:
                    raise ValueError("keyframe region identity/appearance mismatch")
        if set(self.timeline.keyframe_ids) - keyframe_ids:
            raise ValueError("timeline references missing keyframes")
        if set(self.timeline.segment_ids) - segment_ids:
            raise ValueError("timeline references missing segments")
        if self.segment_plan is not None:
            if self.segment_plan.timeline_duration_ms != self.timeline.duration_ms:
                raise ValueError("segment plan duration differs from the timeline")
            if tuple(item.segment_id for item in self.segment_plan.segments) != tuple(
                item.segment_id for item in self.segments
            ):
                raise ValueError("persisted segment plan differs from project segments")
        for segment in self.segments:
            if segment.action_spec_id is not None and segment.action_spec_id not in action_ids:
                raise ValueError("segment references a missing action spec")
            if set(segment.character_identity_ids) - identity_ids:
                raise ValueError("segment references a missing character identity")
            segment_assets = {
                item
                for item in (
                    segment.start_image_asset_id,
                    segment.end_image_asset_id,
                    segment.reference_character_asset_id,
                    segment.driving_video_asset_id,
                    segment.source_video_asset_id,
                    segment.mask_asset_id,
                )
                if item is not None
            }
            if segment_assets - asset_ids:
                raise ValueError("segment references a missing mode input asset")
            if segment.start_keyframe_id and segment.start_keyframe_id not in keyframe_ids:
                raise ValueError("segment references a missing start keyframe")
            if segment.end_keyframe_id and segment.end_keyframe_id not in keyframe_ids:
                raise ValueError("segment references a missing end keyframe")
            for revision_id in segment.revision_ids:
                revision = revision_by_id.get(revision_id)
                if revision is None or revision.segment_id != segment.segment_id:
                    raise ValueError("segment revision linkage is inconsistent")
        for action in self.actions:
            if (
                action.driving_video_asset_id is not None
                and action.driving_video_asset_id not in asset_ids
            ):
                raise ValueError("action spec references a missing driving-video asset")
        for revision in self.segment_revisions:
            referenced_assets = {
                *revision.frame_asset_ids,
                *revision.replacement_frame_map.values(),
                *(
                    (revision.result_asset_id,)
                    if revision.result_asset_id is not None
                    else ()
                ),
                *(
                    (revision.start_frame_asset_id,)
                    if revision.start_frame_asset_id is not None
                    else ()
                ),
                *(
                    (revision.end_frame_asset_id,)
                    if revision.end_frame_asset_id is not None
                    else ()
                ),
            }
            if referenced_assets - asset_ids:
                raise ValueError("segment revision references a missing asset")
            if revision.provenance_id is not None and revision.provenance_id not in provenance_ids:
                raise ValueError("segment revision references missing provenance")
        for edit in self.frame_edit_records:
            if edit.segment_revision_id not in revision_by_id:
                raise ValueError("frame edit references a missing segment revision")
            edit_assets = {
                edit.original_frame_asset_id,
                edit.replacement_frame_asset_id,
                *((edit.mask_asset_id,) if edit.mask_asset_id is not None else ()),
            }
            if edit_assets - asset_ids:
                raise ValueError("frame edit references a missing asset")
            if edit.provenance_id not in provenance_ids:
                raise ValueError("frame edit references missing provenance")
        warning_ids = set(collections["identity warning IDs"])
        for warning in self.identity_warnings:
            if warning.segment_revision_id not in revision_by_id:
                raise ValueError("identity warning references a missing revision")
            if warning.identity_id not in identity_ids:
                raise ValueError("identity warning references a missing character")
        for proposal in self.checkpoint_proposals:
            if proposal.segment_id not in segment_ids:
                raise ValueError("checkpoint proposal references a missing segment")
            if set(proposal.warning_ids) - warning_ids:
                raise ValueError("checkpoint proposal references a missing warning")
        return self


def project_document(project: Wan2LabProject) -> str:
    return project.model_dump_json(indent=2)


def load_project_document(document: str | bytes) -> Wan2LabProject:
    decoded = json.loads(document)
    if not isinstance(decoded, dict):
        raise ValueError("Wan2Lab project document must contain a JSON object")
    return Wan2LabProject.model_validate(migrate_project_data(decoded))


def save_project(project: Wan2LabProject, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(project_document(project) + "\n")
            handle.flush()
        temporary_path.replace(path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def load_project(path: Path) -> Wan2LabProject:
    return load_project_document(path.read_text(encoding="utf-8"))


__all__ = [
    "PROJECT_SCHEMA_VERSION",
    "ProjectSettings",
    "Wan2LabProject",
    "load_project",
    "load_project_document",
    "migrate_project_data",
    "project_document",
    "save_project",
]

# Imported last to avoid a cycle while invalidation type-checks canonical projects.
from wan2core.projects.invalidation import (  # noqa: E402
    change_output_fps,
    invalidate_for_boundary_assets,
    invalidate_for_keyframe,
    invalidate_segments,
)

__all__ += [
    "change_output_fps",
    "invalidate_for_boundary_assets",
    "invalidate_for_keyframe",
    "invalidate_segments",
]
