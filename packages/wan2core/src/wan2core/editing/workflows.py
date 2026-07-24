"""Immutable frame extraction, Krea edit, revision, and assembly workflows."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Mapping

from pydantic import Field, model_validator

from k2core.backends import (
    BackendResult,
    CancellationToken,
    FrameEditorBackend,
    ProgressCallback,
)
from wan2core.assets import AssetKind, AssetRef
from wan2core.base import DomainModel, Identifier
from wan2core.editing import (
    BatchFrameSelection,
    BoundaryPropagation,
    FrameEditOperation,
    FrameEditRecord,
)
from wan2core.keyframes import AdapterSelection, Rectangle
from wan2core.keyframes.composition import KreaAdapterRouteSpec
from wan2core.projects import Wan2LabProject
from wan2core.provenance import ProvenanceRecord
from wan2core.review import begin_modification, complete_modification


class FrameExtractionPlan(DomainModel):
    source_video_path: str = Field(min_length=1)
    frame_index: int = Field(ge=0)
    output_path: str = Field(min_length=1)
    arguments: tuple[str, ...]


def plan_frame_extraction(
    *,
    ffmpeg_executable: str,
    source_video_path: str,
    frame_index: int,
    frame_count: int,
    output_path: str,
) -> FrameExtractionPlan:
    if not 0 <= frame_index < frame_count:
        raise ValueError("frame index is outside the source revision")
    if not ffmpeg_executable.strip():
        raise ValueError("FFmpeg executable must not be empty")
    return FrameExtractionPlan(
        source_video_path=source_video_path,
        frame_index=frame_index,
        output_path=output_path,
        arguments=(
            ffmpeg_executable,
            "-loglevel",
            "error",
            "-y",
            "-i",
            source_video_path,
            "-vf",
            f"select=eq(n\\,{frame_index})",
            "-frames:v",
            "1",
            output_path,
        ),
    )


class NormalizedFrameEditRequest(DomainModel):
    source_frame_asset_id: Identifier
    operation_type: FrameEditOperation
    prompt: str = ""
    settings: dict[str, object] = Field(default_factory=dict)
    region: Rectangle | None = None
    mask_asset_id: Identifier | None = None
    identity_id: Identifier | None = None
    appearance_id: Identifier | None = None
    adapters: tuple[AdapterSelection, ...] = ()
    adapter_routes: tuple[KreaAdapterRouteSpec, ...] = ()
    user_confirmed_face_region: bool = False

    @model_validator(mode="after")
    def validate_face_confirmation(self) -> "NormalizedFrameEditRequest":
        if (
            self.operation_type is FrameEditOperation.FACE_REFINEMENT
            and not self.user_confirmed_face_region
        ):
            raise ValueError("face refinement requires a user-confirmed region")
        if (
            self.operation_type is FrameEditOperation.FACE_REFINEMENT
            and not self.adapter_routes
        ):
            raise ValueError(
                "identity face refinement requires a resolved compatible adapter route"
            )
        selected = {item.adapter_id for item in self.adapters}
        routed = {item.adapter_id for item in self.adapter_routes}
        if not routed.issubset(selected):
            raise ValueError("frame adapter routes must reference selected adapters")
        return self

    def to_k2_request(self) -> dict[str, object]:
        adapter_payload = (
            [item.to_k2_payload() for item in self.adapter_routes]
            if self.adapter_routes
            else [item.model_dump(mode="json") for item in self.adapters]
        )
        return {
            "operation": self.operation_type.value,
            "source_asset_id": self.source_frame_asset_id,
            "prompt": self.prompt,
            "settings": self.settings,
            "region": None if self.region is None else self.region.model_dump(mode="json"),
            "mask_asset_id": self.mask_asset_id,
            "identity_id": self.identity_id,
            "appearance_id": self.appearance_id,
            "adapters": adapter_payload,
            "user_confirmed_face_region": self.user_confirmed_face_region,
        }


class BatchFrameEditPlan(DomainModel):
    selection: BatchFrameSelection
    requests: tuple[NormalizedFrameEditRequest, ...]

    @model_validator(mode="after")
    def validate_cardinality(self) -> "BatchFrameEditPlan":
        if len(self.selection.frame_indices) != len(self.requests):
            raise ValueError("batch edit requires exactly one request per selected frame")
        return self


@dataclass(frozen=True, slots=True)
class KreaFrameEditService:
    backend: FrameEditorBackend

    def execute(
        self,
        request: NormalizedFrameEditRequest,
        *,
        progress: ProgressCallback,
        cancellation: CancellationToken,
    ) -> BackendResult:
        payload: Mapping[str, object] = request.to_k2_request()
        errors = self.backend.validate_edit_request(payload)
        if errors:
            raise ValueError("; ".join(errors))
        operation = self.backend.refine_faces if request.operation_type is FrameEditOperation.FACE_REFINEMENT else self.backend.edit_frame
        return operation(payload, progress=progress, cancellation=cancellation)


class FrameReplacementCopy(DomainModel):
    frame_index: int = Field(ge=0)
    source_path: str = Field(min_length=1)
    destination_path: str = Field(min_length=1)


class FrameRevisionAssemblyPlan(DomainModel):
    extract_arguments: tuple[str, ...]
    replacements: tuple[FrameReplacementCopy, ...]
    encode_arguments: tuple[str, ...]
    frame_directory: str
    output_path: str


def plan_frame_revision_assembly(
    *,
    ffmpeg_executable: str,
    source_video_path: str,
    replacement_paths: dict[int, str],
    generation_fps: float,
    frame_count: int,
    output_path: str,
    work_directory: str,
) -> FrameRevisionAssemblyPlan:
    if generation_fps <= 0 or frame_count <= 0:
        raise ValueError("generation FPS and frame count must be positive")
    if any(not 0 <= index < frame_count for index in replacement_paths):
        raise ValueError("replacement frame index is outside the source revision")
    frame_directory = str(PurePosixPath(work_directory) / "frames")
    pattern = str(PurePosixPath(frame_directory) / "%08d.png")
    replacements = tuple(
        FrameReplacementCopy(
            frame_index=index,
            source_path=replacement_paths[index],
            destination_path=str(PurePosixPath(frame_directory) / f"{index + 1:08d}.png"),
        )
        for index in sorted(replacement_paths)
    )
    return FrameRevisionAssemblyPlan(
        extract_arguments=(
            ffmpeg_executable,
            "-loglevel",
            "error",
            "-y",
            "-i",
            source_video_path,
            "-vsync",
            "0",
            pattern,
        ),
        replacements=replacements,
        encode_arguments=(
            ffmpeg_executable,
            "-loglevel",
            "error",
            "-y",
            "-framerate",
            f"{generation_fps:g}",
            "-i",
            pattern,
            "-frames:v",
            str(frame_count),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            output_path,
        ),
        frame_directory=frame_directory,
        output_path=output_path,
    )


def commit_frame_edit_revision(
    project: Wan2LabProject,
    *,
    segment_id: Identifier,
    source_revision_id: Identifier,
    edit_records: tuple[FrameEditRecord, ...],
    replacement_assets: tuple[AssetRef, ...],
    revised_video_asset: AssetRef,
    provenance: tuple[ProvenanceRecord, ...],
    assembly_provenance_id: Identifier,
    new_revision_id: Identifier,
) -> Wan2LabProject:
    segment = next((item for item in project.segments if item.segment_id == segment_id), None)
    source = next(
        (item for item in project.segment_revisions if item.revision_id == source_revision_id),
        None,
    )
    if segment is None or source is None or source.segment_id != segment_id:
        raise KeyError("segment or source revision is missing")
    if not edit_records:
        raise ValueError("frame modification requires at least one edit record")
    if any(item.segment_revision_id != source_revision_id for item in edit_records):
        raise ValueError("frame edit records must reference the source revision")
    replacement_by_id = {asset.asset_id: asset for asset in replacement_assets}
    replacement_map = {item.frame_index: item.replacement_frame_asset_id for item in edit_records}
    if set(replacement_map.values()) != set(replacement_by_id):
        raise ValueError("edit records and replacement assets do not match")
    if any(asset.kind is not AssetKind.IMAGE for asset in replacement_assets):
        raise ValueError("replacement frames must be image assets")
    if revised_video_asset.kind is not AssetKind.VIDEO:
        raise ValueError("revised segment output must be a video asset")
    provenance_by_id = {item.provenance_id: item for item in provenance}
    if any(item.provenance_id not in provenance_by_id for item in edit_records):
        raise ValueError("every edit record requires matching provenance")
    if assembly_provenance_id not in provenance_by_id:
        raise ValueError("revised video requires assembly provenance")
    segment, modifying = begin_modification(segment, source)
    propagate = tuple(
        item.frame_index
        for item in edit_records
        if item.boundary_propagation is BoundaryPropagation.PROPAGATE_AS_ANCHOR
    )
    segment, superseded, revised = complete_modification(
        segment,
        modifying,
        revision_id=new_revision_id,
        result_asset_id=revised_video_asset.asset_id,
        replacement_frame_map=replacement_map,
        provenance_id=assembly_provenance_id,
        propagate_boundary_indices=propagate,
    )
    segments = tuple(segment if item.segment_id == segment_id else item for item in project.segments)
    revisions = tuple(
        superseded if item.revision_id == source_revision_id else item
        for item in project.segment_revisions
    ) + (revised,)
    updated = project.model_copy(
        update={
            "segments": segments,
            "segment_revisions": revisions,
            "assets": (*project.assets, *replacement_assets, revised_video_asset),
            "generation_records": (*project.generation_records, *provenance),
            "frame_edit_records": (*project.frame_edit_records, *edit_records),
        }
    )
    committed = Wan2LabProject.model_validate(updated.model_dump())
    propagated_source_assets = []
    if 0 in propagate and source.start_frame_asset_id is not None:
        propagated_source_assets.append(source.start_frame_asset_id)
    if (
        source.source_request.frame_count - 1 in propagate
        and source.end_frame_asset_id is not None
    ):
        propagated_source_assets.append(source.end_frame_asset_id)
    if propagated_source_assets:
        # Imported locally to retain the projects/editing dependency boundary.
        from wan2core.projects.invalidation import invalidate_for_boundary_assets

        committed = invalidate_for_boundary_assets(
            committed,
            source_segment_id=segment_id,
            replaced_boundary_asset_ids=propagated_source_assets,
        )
    return Wan2LabProject.model_validate(committed.model_dump())


__all__ = [
    "BatchFrameEditPlan",
    "FrameExtractionPlan",
    "FrameReplacementCopy",
    "FrameRevisionAssemblyPlan",
    "KreaFrameEditService",
    "NormalizedFrameEditRequest",
    "commit_frame_edit_revision",
    "plan_frame_extraction",
    "plan_frame_revision_assembly",
]
