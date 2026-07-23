"""Canonical Wan2Lab project document and cross-record validation."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, model_validator

from wan2core.actions import ActionSpec
from wan2core.assets import AssetRef
from wan2core.base import DomainModel, Identifier, require_unique
from wan2core.characters import AppearanceProfile, CharacterIdentity, CharacterSheet
from wan2core.editing import FrameEditRecord
from wan2core.export import ExportPlan
from wan2core.keyframes import Keyframe
from wan2core.mannequin import MannequinScene
from wan2core.provenance import ProvenanceRecord
from wan2core.segments import ContinuationPolicy, Segment, SegmentRevision
from wan2core.timeline import Timeline


PROJECT_SCHEMA_VERSION = 1


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


class Wan2LabProject(DomainModel):
    schema_version: int = PROJECT_SCHEMA_VERSION
    project_id: Identifier
    project_settings: ProjectSettings
    assets: tuple[AssetRef, ...] = ()
    characters: tuple[CharacterIdentity, ...] = ()
    appearance_profiles: tuple[AppearanceProfile, ...] = ()
    character_sheets: tuple[CharacterSheet, ...] = ()
    mannequin_scenes: tuple[MannequinScene, ...] = ()
    keyframes: tuple[Keyframe, ...] = ()
    actions: tuple[ActionSpec, ...] = ()
    timeline: Timeline
    segments: tuple[Segment, ...] = ()
    segment_revisions: tuple[SegmentRevision, ...] = ()
    generation_records: tuple[ProvenanceRecord, ...] = ()
    frame_edit_records: tuple[FrameEditRecord, ...] = ()
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
            "keyframe IDs": [item.keyframe_id for item in self.keyframes],
            "action IDs": [item.action_id for item in self.actions],
            "segment IDs": [item.segment_id for item in self.segments],
            "revision IDs": [item.revision_id for item in self.segment_revisions],
            "provenance IDs": [item.provenance_id for item in self.generation_records],
            "frame edit IDs": [item.edit_id for item in self.frame_edit_records],
            "export IDs": [item.export_id for item in self.exports],
        }
        for label, values in collections.items():
            require_unique(values, label)

        identity_ids = set(collections["character IDs"])
        appearance_by_id = {item.appearance_id: item for item in self.appearance_profiles}
        asset_ids = set(collections["asset IDs"])
        keyframe_ids = set(collections["keyframe IDs"])
        segment_ids = set(collections["segment IDs"])
        revision_by_id = {item.revision_id: item for item in self.segment_revisions}
        provenance_ids = set(collections["provenance IDs"])

        for appearance in self.appearance_profiles:
            if appearance.identity_id not in identity_ids:
                raise ValueError("appearance profile references a missing identity")
        for sheet in self.character_sheets:
            appearance = appearance_by_id.get(sheet.appearance_id)
            if sheet.identity_id not in identity_ids or appearance is None:
                raise ValueError("character sheet references missing identity or appearance")
            if appearance.identity_id != sheet.identity_id:
                raise ValueError("character sheet appearance belongs to another identity")
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
        for segment in self.segments:
            if segment.start_keyframe_id and segment.start_keyframe_id not in keyframe_ids:
                raise ValueError("segment references a missing start keyframe")
            if segment.end_keyframe_id and segment.end_keyframe_id not in keyframe_ids:
                raise ValueError("segment references a missing end keyframe")
            for revision_id in segment.revision_ids:
                revision = revision_by_id.get(revision_id)
                if revision is None or revision.segment_id != segment.segment_id:
                    raise ValueError("segment revision linkage is inconsistent")
        return self


def project_document(project: Wan2LabProject) -> str:
    return project.model_dump_json(indent=2)


def load_project_document(document: str | bytes) -> Wan2LabProject:
    return Wan2LabProject.model_validate_json(document)


def save_project(project: Wan2LabProject, path: Path) -> None:
    path.write_text(project_document(project) + "\n", encoding="utf-8")


def load_project(path: Path) -> Wan2LabProject:
    return load_project_document(path.read_text(encoding="utf-8"))


__all__ = [
    "PROJECT_SCHEMA_VERSION",
    "ProjectSettings",
    "Wan2LabProject",
    "load_project",
    "load_project_document",
    "project_document",
    "save_project",
]

