from __future__ import annotations

import unittest
from datetime import UTC, datetime

from wan2core.assets import AssetKind, AssetRef
from wan2core.backends.mock import MockWanBackend, default_mock_capabilities
from wan2core.export import build_export_plan
from wan2core.keyframes import Keyframe, KeyframeSource
from wan2core.orchestration import WanStudioSession
from wan2core.projects import ProjectSettings, Wan2LabProject
from wan2core.projects.invalidation import invalidate_segments
from wan2core.provenance import ProvenanceRecord
from wan2core.segments import SegmentState
from wan2core.timeline import Timeline


def anchored_project() -> Wan2LabProject:
    assets = tuple(
        AssetRef(
            asset_id=f"anchor-asset-{index}",
            kind=AssetKind.IMAGE,
            storage_path=f"objects/anchor-{index}.png",
            sha256=f"{index:x}" * 64,
            width=1280,
            height=720,
        )
        for index in range(1, 8)
    )
    provenance = tuple(
        ProvenanceRecord(
            provenance_id=f"anchor-provenance-{index}",
            operation="acceptance_fixture_anchor",
            created_at=datetime(2026, 7, 22, tzinfo=UTC),
            output_asset_ids=(asset.asset_id,),
        )
        for index, asset in enumerate(assets, start=1)
    )
    keyframes = tuple(
        Keyframe(
            keyframe_id=f"anchor-{index}",
            time_ms=time_ms,
            image_asset_id=asset.asset_id,
            source_type=KeyframeSource.IMPORTED,
            provenance_id=record.provenance_id,
            approved=True,
            locked=True,
        )
        for index, (time_ms, asset, record) in enumerate(
            zip(range(0, 18_001, 3_000), assets, provenance, strict=True),
            start=1,
        )
    )
    return Wan2LabProject(
        project_id="phase1-acceptance",
        project_settings=ProjectSettings(
            default_wan_backend_id="mock-wan",
            default_wan_model_id="wan-test",
        ),
        assets=assets,
        keyframes=keyframes,
        timeline=Timeline(
            duration_ms=18_000,
            output_fps=24.0,
            keyframe_ids=tuple(item.keyframe_id for item in keyframes),
        ),
        generation_records=provenance,
    )


class Phase1AcceptanceTests(unittest.TestCase):
    def test_review_gated_anchored_timeline_reaches_export_without_losing_history(
        self,
    ) -> None:
        capabilities = default_mock_capabilities()
        backend = MockWanBackend(capabilities)
        session = WanStudioSession(anchored_project())

        plan = session.plan(capabilities, model_id="wan-test")
        self.assertEqual(len(plan.segments), 6)
        self.assertTrue(
            all(item.end_ms - item.start_ms == 3_000 for item in plan.segments)
        )

        rejected = session.generate_next_with_mock(
            backend,
            seed=1,
            progress=lambda _event: None,
        )
        session.reject_current("acceptance fixture retry")
        replacement = session.regenerate_rejected_with_mock(
            backend,
            seed=2,
            progress=lambda _event: None,
        )
        self.assertEqual(replacement.parent_revision_id, rejected.revision_id)
        session.approve_current()

        for seed in range(3, 8):
            session.generate_next_with_mock(
                backend,
                seed=seed,
                progress=lambda _event: None,
            )
            session.approve_current()

        self.assertTrue(
            all(item.state is SegmentState.APPROVED_LOCKED for item in session.project.segments)
        )
        self.assertEqual(len(session.project.segment_revisions), 7)
        source_paths = {
            revision.result_asset_id: f"/workspace/{revision.result_asset_id}.mp4"
            for revision in session.project.segment_revisions
            if revision.result_asset_id is not None
        }
        export = build_export_plan(
            export_id="phase1-export",
            segments=session.project.segments,
            revisions=session.project.segment_revisions,
            source_paths=source_paths,
            output_path="/workspace/phase1.mp4",
            output_fps=30.0,
            ffmpeg_executable="ffmpeg",
            provenance_id="phase1-export-provenance",
        )
        self.assertEqual(len(export.segment_inputs), 6)
        self.assertEqual(export.output_fps, 30.0)

        stale = invalidate_segments(
            session.project,
            (session.project.segments[0].segment_id,),
            reason="acceptance fixture invalidation",
        )
        with self.assertRaisesRegex(ValueError, "approved"):
            build_export_plan(
                export_id="blocked-export",
                segments=stale.segments,
                revisions=stale.segment_revisions,
                source_paths=source_paths,
                output_path="/workspace/blocked.mp4",
                output_fps=24.0,
                ffmpeg_executable="ffmpeg",
                provenance_id="blocked-export-provenance",
            )


if __name__ == "__main__":
    unittest.main()
