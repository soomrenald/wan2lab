from __future__ import annotations

import unittest

from wan2core.backends import WanMode
from wan2core.keyframes import Keyframe, KeyframeSource
from wan2core.segments import ContinuationPolicy
from wan2core.timeline import Timeline, plan_segments

from helpers import backend_capabilities


def keyframe(index: int, time_ms: int) -> Keyframe:
    return Keyframe(
        keyframe_id=f"keyframe-{index}",
        time_ms=time_ms,
        image_asset_id=f"image-{index}",
        source_type=KeyframeSource.IMPORTED,
        provenance_id=f"provenance-{index}",
        approved=True,
        locked=True,
    )


class TimelinePlannerTests(unittest.TestCase):
    def test_unapproved_keyframe_cannot_drive_wan_planning(self) -> None:
        draft = Keyframe(
            keyframe_id="draft-keyframe",
            time_ms=0,
            image_asset_id="draft-image",
            source_type=KeyframeSource.KREA_GENERATED,
            provenance_id="draft-provenance",
        )
        with self.assertRaisesRegex(ValueError, "not approved and locked"):
            plan_segments(
                Timeline(
                    duration_ms=5_000,
                    output_fps=24,
                    keyframe_ids=(draft.keyframe_id,),
                ),
                (draft,),
                backend_capabilities(),
                model_id="wan-test",
            )

    def test_eighteen_seconds_with_three_second_anchors_is_six_segments(self) -> None:
        keyframes = tuple(keyframe(index, index * 3_000) for index in range(7))
        timeline = Timeline(
            duration_ms=18_000,
            output_fps=24.0,
            keyframe_ids=tuple(item.keyframe_id for item in keyframes),
        )
        plan = plan_segments(
            timeline,
            keyframes,
            backend_capabilities(),
            model_id="wan-test",
        )
        self.assertEqual(len(plan.segments), 6)
        self.assertEqual([item.start_ms for item in plan.segments], [0, 3000, 6000, 9000, 12000, 15000])
        self.assertTrue(all(item.mode is WanMode.FIRST_LAST for item in plan.segments))
        self.assertTrue(
            all(item.continuation_policy is ContinuationPolicy.DUAL_BOUNDARY for item in plan.segments)
        )

    def test_long_authored_interval_uses_generated_boundaries_then_final_anchor(self) -> None:
        keyframes = (keyframe(0, 0), keyframe(1, 12_000))
        timeline = Timeline(
            duration_ms=12_000,
            output_fps=24.0,
            keyframe_ids=("keyframe-0", "keyframe-1"),
        )
        plan = plan_segments(
            timeline,
            keyframes,
            backend_capabilities(),
            model_id="wan-test",
        )
        self.assertEqual([(item.start_ms, item.end_ms) for item in plan.segments], [(0, 5000), (5000, 10000), (10000, 12000)])
        self.assertEqual([item.mode for item in plan.segments], [WanMode.I2V, WanMode.I2V, WanMode.FIRST_LAST])
        self.assertEqual(plan.segments[-1].end_keyframe_id, "keyframe-1")

    def test_end_anchor_is_review_target_when_first_last_is_unsupported(self) -> None:
        keyframes = (keyframe(0, 0), keyframe(1, 3_000))
        timeline = Timeline(
            duration_ms=3_000,
            output_fps=24.0,
            keyframe_ids=("keyframe-0", "keyframe-1"),
        )
        plan = plan_segments(
            timeline,
            keyframes,
            backend_capabilities(first_last=False),
            model_id="wan-test",
        )
        self.assertEqual(plan.segments[0].mode, WanMode.I2V)
        self.assertTrue(plan.segments[0].end_anchor_is_review_target)

    def test_no_keyframes_requires_prompt_support_and_splits_at_budget(self) -> None:
        plan = plan_segments(
            Timeline(duration_ms=11_000, output_fps=30.0),
            (),
            backend_capabilities(),
            model_id="wan-test",
        )
        self.assertEqual([(item.start_ms, item.end_ms) for item in plan.segments], [(0, 5000), (5000, 10000), (10000, 11000)])
        self.assertEqual(plan.segments[0].mode, WanMode.PROMPT)
        self.assertEqual(plan.segments[1].mode, WanMode.I2V)

    def test_wan_81_frames_at_16_fps_represents_five_seconds(self) -> None:
        model = backend_capabilities().model("wan-test")
        self.assertEqual(model.resolve_frame_count(5_000, 16.0), 81)
        self.assertEqual(model.frame_duration_ms(81, 16.0), 5_000)


if __name__ == "__main__":
    unittest.main()
