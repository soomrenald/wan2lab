from __future__ import annotations

import unittest

from pydantic import ValidationError

from wan2core.backends import (
    SegmentAccelerationMode,
    SegmentAccelerationPolicy,
    WanAccelerationKind,
    WanAccelerationMethodCapabilities,
    WanAccelerationPolicy,
    WanAccelerationQuality,
    WanAccelerationSelection,
    WanMode,
    resolve_wan_acceleration,
)
from wan2core.hardware import (
    AvailableGpuCandidate,
    GpuRecommendationTier,
    GpuSelectionRequest,
    WanWorkloadProfile,
    approved_gpu_recommendation_catalog,
    rank_gpu_candidates,
)
from wan2core.projects import ProjectSettings


def method(
    method_id: str,
    *,
    modes: frozenset[WanMode] = frozenset({WanMode.PROMPT, WanMode.I2V}),
    rank: int = 100,
    artifacts: tuple[str, ...] = (),
) -> WanAccelerationMethodCapabilities:
    return WanAccelerationMethodCapabilities(
        method_id=method_id,
        display_name=method_id,
        kind=WanAccelerationKind.LIGHTNING,
        supported_modes=modes,
        supported_model_families=("wan2.2-ti2v-5b",),
        accelerator_vendors=frozenset({"cuda"}),
        required_artifact_ids=artifacts,
        rank=rank,
    )


class AccelerationPolicyTests(unittest.TestCase):
    def test_project_defaults_to_enabled_auto_balanced(self) -> None:
        settings = ProjectSettings(
            default_wan_backend_id="comfyui",
            default_wan_model_id="wan2.2-ti2v-5b",
        )
        self.assertTrue(settings.wan_acceleration.enabled)
        self.assertEqual(
            settings.wan_acceleration.selection,
            WanAccelerationSelection.AUTO,
        )
        self.assertEqual(
            settings.wan_acceleration.quality,
            WanAccelerationQuality.BALANCED,
        )

    def test_auto_selects_preference_before_rank(self) -> None:
        resolution = resolve_wan_acceleration(
            project_policy=WanAccelerationPolicy(
                preferred_method_ids=("preferred",)
            ),
            segment_policy=SegmentAccelerationPolicy(),
            methods=(
                method("highest-rank", rank=500),
                method("preferred", rank=100),
            ),
            model_id="wan-model",
            model_family="wan2.2-ti2v-5b",
            mode=WanMode.I2V,
            accelerator_vendor="cuda",
        )
        self.assertTrue(resolution.active)
        self.assertEqual(resolution.method_id, "preferred")

    def test_specialized_mode_never_infers_generic_method_compatibility(self) -> None:
        resolution = resolve_wan_acceleration(
            project_policy=WanAccelerationPolicy(),
            segment_policy=SegmentAccelerationPolicy(),
            methods=(method("generic-lightning"),),
            model_id="wan-model",
            model_family="wan2.2-ti2v-5b",
            mode=WanMode.ANIMATE,
            accelerator_vendor="cuda",
        )
        self.assertFalse(resolution.active)
        self.assertIn("base inference", resolution.fallback_reason or "")

    def test_missing_artifact_is_an_explicit_base_inference_fallback(self) -> None:
        resolution = resolve_wan_acceleration(
            project_policy=WanAccelerationPolicy(),
            segment_policy=SegmentAccelerationPolicy(),
            methods=(method("artifact-method", artifacts=("lightning-lora",)),),
            model_id="wan-model",
            model_family="wan2.2-ti2v-5b",
            mode=WanMode.PROMPT,
            accelerator_vendor="cuda",
        )
        self.assertFalse(resolution.active)
        self.assertIn("No installed compatible", resolution.fallback_reason or "")

    def test_segment_disable_overrides_project_default(self) -> None:
        resolution = resolve_wan_acceleration(
            project_policy=WanAccelerationPolicy(),
            segment_policy=SegmentAccelerationPolicy(
                mode=SegmentAccelerationMode.DISABLED
            ),
            methods=(method("lightning"),),
            model_id="wan-model",
            model_family="wan2.2-ti2v-5b",
            mode=WanMode.PROMPT,
            accelerator_vendor="cuda",
        )
        self.assertFalse(resolution.active)
        self.assertIn("disabled", resolution.fallback_reason or "")

    def test_specific_policy_requires_a_method(self) -> None:
        with self.assertRaises(ValidationError):
            WanAccelerationPolicy(selection=WanAccelerationSelection.SPECIFIC)


class GpuRecommendationTests(unittest.TestCase):
    def test_catalog_exposes_approved_choices_by_workload(self) -> None:
        catalog = approved_gpu_recommendation_catalog()
        ti2v = catalog.for_workload(
            WanWorkloadProfile.TI2V_5B,
            frozenset({WanMode.I2V}),
        )
        by_tier = {item.tier: item.display_name for item in ti2v}
        self.assertEqual(by_tier[GpuRecommendationTier.VALUE], "NVIDIA RTX 4090")
        self.assertEqual(by_tier[GpuRecommendationTier.SPEED], "NVIDIA RTX 5090")
        self.assertEqual(
            by_tier[GpuRecommendationTier.FULL_MEMORY],
            "NVIDIA RTX 6000 Ada",
        )

    def test_ranking_uses_suitability_before_price(self) -> None:
        ranked = rank_gpu_candidates(
            catalog=approved_gpu_recommendation_catalog(),
            request=GpuSelectionRequest(
                workload=WanWorkloadProfile.TI2V_5B,
                modes=frozenset({WanMode.PROMPT, WanMode.I2V}),
                preferred_tiers=(
                    GpuRecommendationTier.SPEED,
                    GpuRecommendationTier.VALUE,
                ),
            ),
            candidates=(
                AvailableGpuCandidate(
                    gpu_id="nvidia-rtx-4090",
                    display_name="RTX 4090",
                    vram_gib=24,
                    available=True,
                    hourly_price_usd=0.10,
                ),
                AvailableGpuCandidate(
                    gpu_id="nvidia-rtx-5090",
                    display_name="RTX 5090",
                    vram_gib=32,
                    available=True,
                    hourly_price_usd=10.00,
                ),
            ),
        )
        self.assertEqual(ranked[0].candidate.gpu_id, "nvidia-rtx-5090")
        self.assertTrue(ranked[0].suitable)

    def test_unavailable_recommendation_cannot_be_selected_first(self) -> None:
        ranked = rank_gpu_candidates(
            catalog=approved_gpu_recommendation_catalog(),
            request=GpuSelectionRequest(
                workload=WanWorkloadProfile.SPECIALIZED_14B,
                modes=frozenset({WanMode.ANIMATE}),
            ),
            candidates=(
                AvailableGpuCandidate(
                    gpu_id="nvidia-a100-80gb",
                    display_name="A100 80 GB",
                    vram_gib=80,
                    available=False,
                    hourly_price_usd=1.0,
                ),
                AvailableGpuCandidate(
                    gpu_id="nvidia-rtx-pro-6000-blackwell",
                    display_name="RTX PRO 6000 Blackwell",
                    vram_gib=96,
                    available=True,
                    hourly_price_usd=2.0,
                ),
            ),
        )
        self.assertEqual(
            ranked[0].candidate.gpu_id,
            "nvidia-rtx-pro-6000-blackwell",
        )
        self.assertFalse(ranked[-1].suitable)


if __name__ == "__main__":
    unittest.main()
