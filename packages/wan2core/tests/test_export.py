from __future__ import annotations

import unittest

from wan2core.backends import WanMode
from wan2core.export import build_export_plan
from wan2core.review import approve_revision, complete_generation, queue_revision, start_generation
from wan2core.segments import ContinuationPolicy, Segment, SegmentRequest


def approved(number: int, start_ms: int, end_ms: int):
    segment = Segment(
        segment_id=f"segment-{number}",
        start_ms=start_ms,
        end_ms=end_ms,
        mode=WanMode.PROMPT,
        backend_id="mock-wan",
        model_id="wan-test",
        continuation_policy=ContinuationPolicy.GENERATED_LAST_FRAME,
    )
    request = SegmentRequest(
        request_id=f"request-{number}",
        segment_id=segment.segment_id,
        mode=WanMode.PROMPT,
        backend_id="mock-wan",
        model_id="wan-test",
        start_ms=start_ms,
        end_ms=end_ms,
        width=1280,
        height=720,
        generation_fps=16.0,
        frame_count=81,
    )
    segment, revision = queue_revision(
        segment,
        revision_id=f"revision-{number}",
        request=request,
        seed=number,
    )
    segment, revision = start_generation(segment, revision)
    segment, revision = complete_generation(
        segment,
        revision,
        result_asset_id=f"video-{number}",
        provenance_id=f"prov-{number}",
    )
    return approve_revision(segment, revision)


class ExportPlanTests(unittest.TestCase):
    def test_export_uses_argument_arrays_and_only_approved_revisions(self) -> None:
        first, first_revision = approved(1, 0, 5_000)
        second, second_revision = approved(2, 5_000, 10_000)
        plan = build_export_plan(
            export_id="export-1",
            segments=(first, second),
            revisions=(first_revision, second_revision),
            source_paths={"video-1": "assets/one.mp4", "video-2": "assets/two.mp4"},
            output_path="outputs/final.mp4",
            output_fps=24.0,
            ffmpeg_executable="ffmpeg",
            provenance_id="prov-export",
        )
        self.assertEqual(len(plan.commands), 3)
        self.assertEqual(plan.commands[-1].arguments[0], "ffmpeg")
        self.assertNotIn(";", plan.commands[-1].arguments)
        self.assertEqual(plan.fps_plans[0].output_frame_count, 120)

    def test_export_rejects_unapproved_segments(self) -> None:
        draft = Segment(
            segment_id="draft",
            start_ms=0,
            end_ms=1_000,
            mode=WanMode.PROMPT,
            backend_id="mock-wan",
            model_id="wan-test",
            continuation_policy=ContinuationPolicy.AUTHORED_ANCHOR,
        )
        with self.assertRaisesRegex(ValueError, "not approved"):
            build_export_plan(
                export_id="export-1",
                segments=(draft,),
                revisions=(),
                source_paths={},
                output_path="outputs/final.mp4",
                output_fps=24.0,
                ffmpeg_executable="ffmpeg",
                provenance_id="prov-export",
            )


if __name__ == "__main__":
    unittest.main()

