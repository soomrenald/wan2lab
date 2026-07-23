from __future__ import annotations

import unittest
from datetime import UTC, datetime

from wan2core.assets import AssetKind, AssetRef
from wan2core.backends import WanMode
from wan2core.export import ExportState, build_export_plan
from wan2core.keyframes import Keyframe, KeyframeSource
from wan2core.projects import (
    ProjectSettings,
    Wan2LabProject,
    change_output_fps,
    invalidate_for_boundary_assets,
    invalidate_for_keyframe,
)
from wan2core.provenance import ProvenanceRecord
from wan2core.review import approve_revision, complete_generation, queue_revision, start_generation
from wan2core.segments import ContinuationPolicy, Segment, SegmentRequest, SegmentState
from wan2core.timeline import Timeline


def project_with_export() -> Wan2LabProject:
    keyframe = Keyframe(
        keyframe_id="keyframe-1",
        time_ms=0,
        image_asset_id="image-1",
        source_type=KeyframeSource.IMPORTED,
        provenance_id="prov-keyframe",
        approved=True,
        locked=True,
    )
    segment = Segment(
        segment_id="segment-1",
        start_ms=0,
        end_ms=5_000,
        start_keyframe_id=keyframe.keyframe_id,
        mode=WanMode.I2V,
        backend_id="mock-wan",
        model_id="wan-test",
        continuation_policy=ContinuationPolicy.AUTHORED_ANCHOR,
    )
    request = SegmentRequest(
        request_id="request-1",
        segment_id=segment.segment_id,
        mode=WanMode.I2V,
        backend_id="mock-wan",
        model_id="wan-test",
        start_ms=0,
        end_ms=5_000,
        width=1280,
        height=720,
        generation_fps=16.0,
        frame_count=81,
        start_image_asset_id="image-1",
    )
    segment, revision = queue_revision(
        segment, revision_id="revision-1", request=request, seed=1
    )
    segment, revision = start_generation(segment, revision)
    segment, revision = complete_generation(
        segment,
        revision,
        result_asset_id="video-1",
        provenance_id="prov-video",
    )
    segment, revision = approve_revision(segment, revision)
    export = build_export_plan(
        export_id="export-1",
        segments=(segment,),
        revisions=(revision,),
        source_paths={"video-1": "assets/video.mp4"},
        output_path="outputs/final.mp4",
        output_fps=24,
        ffmpeg_executable="ffmpeg",
        provenance_id="prov-export",
    )
    assets = (
        AssetRef(
            asset_id="image-1",
            kind=AssetKind.IMAGE,
            storage_path="assets/image.png",
            sha256="a" * 64,
            width=1280,
            height=720,
        ),
        AssetRef(
            asset_id="video-1",
            kind=AssetKind.VIDEO,
            storage_path="assets/video.mp4",
            sha256="b" * 64,
            width=1280,
            height=720,
            frame_count=81,
            duration_ms=5_000,
        ),
    )
    records = tuple(
        ProvenanceRecord(
            provenance_id=identifier,
            operation="test",
            created_at=datetime(2026, 7, 22, tzinfo=UTC),
        )
        for identifier in ("prov-keyframe", "prov-video", "prov-export")
    )
    return Wan2LabProject(
        project_id="project-1",
        project_settings=ProjectSettings(
            default_wan_backend_id="mock-wan",
            default_wan_model_id="wan-test",
        ),
        assets=assets,
        keyframes=(keyframe,),
        timeline=Timeline(
            duration_ms=5_000,
            output_fps=24,
            keyframe_ids=(keyframe.keyframe_id,),
            segment_ids=(segment.segment_id,),
        ),
        segments=(segment,),
        segment_revisions=(revision,),
        generation_records=records,
        exports=(export,),
    )


class InvalidationTests(unittest.TestCase):
    def test_propagated_boundary_stales_revision_that_consumed_old_asset(self) -> None:
        project = project_with_export()
        dependent = Segment(
            segment_id="segment-2",
            start_ms=5_000,
            end_ms=10_000,
            mode=WanMode.I2V,
            backend_id="mock-wan",
            model_id="wan-test",
            continuation_policy=ContinuationPolicy.GENERATED_LAST_FRAME,
        )
        request = SegmentRequest(
            request_id="request-2",
            segment_id=dependent.segment_id,
            mode=WanMode.I2V,
            backend_id="mock-wan",
            model_id="wan-test",
            start_ms=5_000,
            end_ms=10_000,
            width=1280,
            height=720,
            generation_fps=16,
            frame_count=81,
            start_image_asset_id="image-1",
        )
        dependent, revision = queue_revision(
            dependent,
            revision_id="revision-2",
            request=request,
            seed=2,
        )
        dependent, revision = start_generation(dependent, revision)
        dependent, revision = complete_generation(
            dependent,
            revision,
            result_asset_id="video-2",
            provenance_id="prov-video-2",
        )
        dependent, revision = approve_revision(dependent, revision)
        video = AssetRef(
            asset_id="video-2",
            kind=AssetKind.VIDEO,
            storage_path="assets/video-2.mp4",
            sha256="c" * 64,
            width=1280,
            height=720,
            frame_count=81,
            duration_ms=5_000,
        )
        provenance = ProvenanceRecord(
            provenance_id="prov-video-2",
            operation="generate_segment",
            created_at=datetime(2026, 7, 22, tzinfo=UTC),
            output_asset_ids=(video.asset_id,),
        )
        project = Wan2LabProject.model_validate(
            project.model_copy(
                update={
                    "assets": (*project.assets, video),
                    "segments": (*project.segments, dependent),
                    "segment_revisions": (*project.segment_revisions, revision),
                    "generation_records": (*project.generation_records, provenance),
                    "timeline": project.timeline.model_copy(
                        update={"segment_ids": ("segment-1", "segment-2")}
                    ),
                }
            ).model_dump()
        )

        updated = invalidate_for_boundary_assets(
            project,
            source_segment_id="segment-1",
            replaced_boundary_asset_ids=("image-1",),
        )

        self.assertEqual(updated.segments[0].state, SegmentState.APPROVED_LOCKED)
        self.assertEqual(updated.segments[1].state, SegmentState.STALE)
        self.assertIn("boundary", updated.segments[1].stale_reason)
        self.assertEqual(updated.exports[0].state, ExportState.STALE)

    def test_replacing_authored_keyframe_stales_dependent_segment_and_export(self) -> None:
        project = project_with_export()
        updated = invalidate_for_keyframe(project, "keyframe-1")
        self.assertEqual(updated.segments[0].state, SegmentState.STALE)
        self.assertIsNone(updated.segments[0].current_approved_revision_id)
        self.assertEqual(updated.exports[0].state, ExportState.STALE)
        self.assertEqual(project.segments[0].state, SegmentState.APPROVED_LOCKED)
        Wan2LabProject.model_validate(updated.model_dump())

    def test_output_fps_only_invalidates_export(self) -> None:
        project = project_with_export()
        updated = change_output_fps(project, 30.0)
        self.assertEqual(updated.project_settings.output_fps, 30.0)
        self.assertEqual(updated.timeline.output_fps, 30.0)
        self.assertEqual(updated.segments[0].state, SegmentState.APPROVED_LOCKED)
        self.assertEqual(updated.exports[0].state, ExportState.STALE)


if __name__ == "__main__":
    unittest.main()
