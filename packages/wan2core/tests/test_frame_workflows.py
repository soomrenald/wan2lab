from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from k2core.backends import BackendCapabilities, BackendResult
from wan2core.assets import AssetKind, AssetRef
from wan2core.backends import WanMode
from wan2core.editing import BoundaryPropagation, FrameEditOperation, FrameEditRecord
from wan2core.editing.workflows import (
    KreaFrameEditService,
    NormalizedFrameEditRequest,
    commit_frame_edit_revision,
    plan_frame_extraction,
    plan_frame_revision_assembly,
)
from wan2core.keyframes import AdapterSelection, Rectangle
from wan2core.keyframes.composition import KreaAdapterRouteSpec
from wan2core.projects import ProjectSettings, Wan2LabProject
from wan2core.provenance import ProvenanceRecord
from wan2core.segments import (
    ContinuationPolicy,
    RevisionReviewState,
    Segment,
    SegmentRequest,
    SegmentRevision,
    SegmentState,
)
from wan2core.timeline import Timeline


def asset(asset_id: str, kind: AssetKind, *, parent: tuple[str, ...] = ()) -> AssetRef:
    return AssetRef(
        asset_id=asset_id,
        kind=kind,
        storage_path=f"assets/{asset_id}.{'mp4' if kind is AssetKind.VIDEO else 'png'}",
        sha256=(asset_id[0] if asset_id[0] in "abcdef" else "a") * 64,
        width=1280,
        height=720,
        frame_count=5 if kind is AssetKind.VIDEO else None,
        duration_ms=250 if kind is AssetKind.VIDEO else None,
        parent_asset_ids=parent,
    )


def provenance(provenance_id: str, operation: str, outputs: tuple[str, ...]) -> ProvenanceRecord:
    return ProvenanceRecord(
        provenance_id=provenance_id,
        operation=operation,
        created_at=datetime(2026, 7, 22, tzinfo=UTC),
        output_asset_ids=outputs,
    )


def source_project() -> Wan2LabProject:
    request = SegmentRequest(
        request_id="request-1",
        segment_id="segment-1",
        mode=WanMode.PROMPT,
        backend_id="mock-wan",
        model_id="wan-test",
        start_ms=0,
        end_ms=250,
        width=1280,
        height=720,
        generation_fps=16,
        frame_count=5,
    )
    frames = tuple(asset(f"frame-{index}", AssetKind.IMAGE, parent=("video-source",)) for index in range(5))
    source_video = asset("video-source", AssetKind.VIDEO)
    source_provenance = provenance(
        "provenance-source",
        "generate_segment",
        (source_video.asset_id, *(item.asset_id for item in frames)),
    )
    revision = SegmentRevision(
        revision_id="revision-1",
        segment_id="segment-1",
        revision_number=1,
        source_request=request,
        seed=1,
        result_asset_id=source_video.asset_id,
        frame_asset_ids=tuple(item.asset_id for item in frames),
        start_frame_asset_id=frames[0].asset_id,
        end_frame_asset_id=frames[-1].asset_id,
        review_state=RevisionReviewState.READY_FOR_REVIEW,
        provenance_id=source_provenance.provenance_id,
    )
    segment = Segment(
        segment_id="segment-1",
        start_ms=0,
        end_ms=250,
        mode=WanMode.PROMPT,
        backend_id="mock-wan",
        model_id="wan-test",
        continuation_policy=ContinuationPolicy.GENERATED_LAST_FRAME,
        state=SegmentState.READY_FOR_REVIEW,
        revision_ids=(revision.revision_id,),
    )
    return Wan2LabProject(
        project_id="project-1",
        project_settings=ProjectSettings(
            default_wan_backend_id="mock-wan", default_wan_model_id="wan-test"
        ),
        assets=(source_video, *frames),
        segments=(segment,),
        segment_revisions=(revision,),
        generation_records=(source_provenance,),
        timeline=Timeline(duration_ms=250, output_fps=24, segment_ids=(segment.segment_id,)),
    )


class Token:
    cancelled = False

    def raise_if_cancelled(self) -> None:
        return None


class FrameBackend:
    backend_id = "frame-test"

    def __init__(self, result: Path) -> None:
        self.result = result
        self.face_calls = 0

    def capabilities(self):
        return BackendCapabilities(
            backend_id=self.backend_id,
            modes=frozenset({"edit"}),
            accelerator_vendors=frozenset({"cpu"}),
        )

    def validate_edit_request(self, request):
        return ()

    def edit_frame(self, request, *, progress, cancellation):
        return BackendResult(asset_paths=(self.result,))

    def refine_faces(self, request, *, progress, cancellation):
        self.face_calls += 1
        return BackendResult(asset_paths=(self.result,))

    def release(self):
        return None


class FrameWorkflowTests(unittest.TestCase):
    def test_extraction_and_revision_assembly_are_argument_arrays(self) -> None:
        extraction = plan_frame_extraction(
            ffmpeg_executable="ffmpeg",
            source_video_path="assets/source.mp4",
            frame_index=4,
            frame_count=5,
            output_path="work/frame.png",
        )
        assembly = plan_frame_revision_assembly(
            ffmpeg_executable="ffmpeg",
            source_video_path="assets/source.mp4",
            replacement_paths={0: "assets/replacement.png", 4: "assets/end.png"},
            generation_fps=16,
            frame_count=5,
            output_path="work/revised.mp4",
            work_directory="work/revision-2",
        )
        self.assertIn("select=eq(n\\,4)", extraction.arguments)
        self.assertEqual(assembly.replacements[0].destination_path[-12:], "00000001.png")
        self.assertIn("libx264", assembly.encode_arguments)

    def test_batch_commit_preserves_source_and_propagates_only_selected_boundary(self) -> None:
        project = source_project()
        dependent_video = asset("dependent-video", AssetKind.VIDEO)
        dependent_request = SegmentRequest(
            request_id="request-dependent",
            segment_id="segment-dependent",
            mode=WanMode.I2V,
            backend_id="mock-wan",
            model_id="wan-test",
            start_ms=250,
            end_ms=500,
            width=1280,
            height=720,
            generation_fps=16,
            frame_count=5,
            start_image_asset_id="frame-4",
        )
        dependent_revision = SegmentRevision(
            revision_id="revision-dependent",
            segment_id="segment-dependent",
            revision_number=1,
            source_request=dependent_request,
            seed=2,
            result_asset_id=dependent_video.asset_id,
            review_state=RevisionReviewState.READY_FOR_REVIEW,
            provenance_id="provenance-dependent",
        )
        dependent_segment = Segment(
            segment_id="segment-dependent",
            start_ms=250,
            end_ms=500,
            mode=WanMode.I2V,
            backend_id="mock-wan",
            model_id="wan-test",
            continuation_policy=ContinuationPolicy.GENERATED_LAST_FRAME,
            state=SegmentState.READY_FOR_REVIEW,
            revision_ids=(dependent_revision.revision_id,),
        )
        project = Wan2LabProject.model_validate(
            project.model_copy(
                update={
                    "assets": (*project.assets, dependent_video),
                    "segments": (*project.segments, dependent_segment),
                    "segment_revisions": (
                        *project.segment_revisions,
                        dependent_revision,
                    ),
                    "generation_records": (
                        *project.generation_records,
                        provenance(
                            "provenance-dependent",
                            "generate_segment",
                            (dependent_video.asset_id,),
                        ),
                    ),
                    "timeline": project.timeline.model_copy(
                        update={
                            "duration_ms": 500,
                            "segment_ids": ("segment-1", "segment-dependent"),
                        }
                    ),
                }
            ).model_dump()
        )
        first = asset("edited-first", AssetKind.IMAGE, parent=("frame-0",))
        last = asset("edited-last", AssetKind.IMAGE, parent=("frame-4",))
        revised_video = asset("video-revised", AssetKind.VIDEO, parent=("video-source",))
        records = (
            FrameEditRecord(
                edit_id="edit-first",
                segment_revision_id="revision-1",
                original_frame_asset_id="frame-0",
                replacement_frame_asset_id=first.asset_id,
                frame_index=0,
                operation_type=FrameEditOperation.IMAGE_EDIT,
                boundary_propagation=BoundaryPropagation.LOCAL_REPAIR,
                provenance_id="provenance-first",
            ),
            FrameEditRecord(
                edit_id="edit-last",
                segment_revision_id="revision-1",
                original_frame_asset_id="frame-4",
                replacement_frame_asset_id=last.asset_id,
                frame_index=4,
                operation_type=FrameEditOperation.FACE_REFINEMENT,
                user_confirmed_face_region=True,
                boundary_propagation=BoundaryPropagation.PROPAGATE_AS_ANCHOR,
                provenance_id="provenance-last",
            ),
        )
        provenance_records = (
            provenance("provenance-first", "edit_frame", (first.asset_id,)),
            provenance("provenance-last", "refine_face", (last.asset_id,)),
            provenance("provenance-assembly", "assemble_revision", (revised_video.asset_id,)),
        )
        updated = commit_frame_edit_revision(
            project,
            segment_id="segment-1",
            source_revision_id="revision-1",
            edit_records=records,
            replacement_assets=(first, last),
            revised_video_asset=revised_video,
            provenance=provenance_records,
            assembly_provenance_id="provenance-assembly",
            new_revision_id="revision-2",
        )
        source = next(
            item for item in updated.segment_revisions if item.revision_id == "revision-1"
        )
        revised = next(
            item for item in updated.segment_revisions if item.revision_id == "revision-2"
        )
        self.assertEqual(source.review_state, RevisionReviewState.SUPERSEDED)
        self.assertEqual(revised.start_frame_asset_id, "frame-0")
        self.assertEqual(revised.end_frame_asset_id, "edited-last")
        self.assertEqual(updated.segments[0].state, SegmentState.READY_FOR_REVIEW)
        self.assertEqual(updated.segments[1].state, SegmentState.STALE)
        self.assertIn("video-source", {item.asset_id for item in updated.assets})

    def test_face_edit_requires_confirmation_and_uses_k2core_frame_backend(self) -> None:
        with self.assertRaises(ValueError):
            NormalizedFrameEditRequest(
                source_frame_asset_id="frame-1",
                operation_type=FrameEditOperation.FACE_REFINEMENT,
            )
        with tempfile.TemporaryDirectory() as directory:
            result = Path(directory) / "face.png"
            result.touch()
            backend = FrameBackend(result)
            service = KreaFrameEditService(backend)
            output = service.execute(
                NormalizedFrameEditRequest(
                    source_frame_asset_id="frame-1",
                    operation_type=FrameEditOperation.FACE_REFINEMENT,
                    adapters=(
                        AdapterSelection(adapter_id="identity-adapter", strength=0.8),
                    ),
                    adapter_routes=(
                        KreaAdapterRouteSpec(
                            route_id="identity-adapter:confirmed-face",
                            adapter_id="identity-adapter",
                            asset_id="identity-adapter-asset",
                            model_family="krea2",
                            strength=0.8,
                            region_ids=("confirmed-face",),
                            routing_mode="character_identity",
                            trigger_phrase="avery_token",
                        ),
                    ),
                    user_confirmed_face_region=True,
                ),
                progress=lambda *_args: None,
                cancellation=Token(),
            )
        self.assertEqual(output.asset_paths, (result,))
        self.assertEqual(backend.face_calls, 1)

    def test_face_edit_preserves_resolved_identity_adapter_route(self) -> None:
        request = NormalizedFrameEditRequest(
            source_frame_asset_id="frame-1",
            operation_type=FrameEditOperation.FACE_REFINEMENT,
            region=Rectangle(x0=10, y0=12, x1=80, y1=90),
            adapters=(AdapterSelection(adapter_id="identity-adapter", strength=0.8),),
            adapter_routes=(
                KreaAdapterRouteSpec(
                    route_id="identity-adapter:confirmed-face",
                    adapter_id="identity-adapter",
                    asset_id="identity-adapter-asset",
                    model_family="krea2",
                    strength=0.8,
                    region_ids=("confirmed-face",),
                    routing_mode="character_identity",
                    trigger_phrase="avery_token",
                ),
            ),
            user_confirmed_face_region=True,
        )

        payload = request.to_k2_request()

        self.assertEqual(
            payload["adapters"],
            [
                {
                    "id": "identity-adapter:confirmed-face",
                    "name": "identity-adapter",
                    "path": "identity-adapter-asset",
                    "strength": 0.8,
                    "global": False,
                    "region_ids": ["confirmed-face"],
                    "routing_mode": "character_identity",
                    "trigger_phrase": "avery_token",
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()
