"""Provider-neutral Wan workload and GPU recommendation contracts."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from wan2core.backends import WanAccelerationKind, WanMode
from wan2core.base import DomainModel, Identifier, require_unique


class WanWorkloadProfile(StrEnum):
    TI2V_5B = "wan2.2-ti2v-5b"
    GENERAL_14B = "wan-14b-general"
    SPECIALIZED_14B = "wan-14b-animate-replace"
    MINIMUM_LATENCY_14B = "wan-14b-minimum-latency"


class GpuRecommendationTier(StrEnum):
    VALUE = "value"
    SPEED = "speed"
    FULL_MEMORY = "full_memory"
    FALLBACK = "fallback"
    MINIMUM_LATENCY = "minimum_latency"


class GpuRecommendation(DomainModel):
    recommendation_id: Identifier
    workload: WanWorkloadProfile
    gpu_id: Identifier
    display_name: str = Field(min_length=1)
    vram_gib: int = Field(gt=0)
    tier: GpuRecommendationTier
    applicable_modes: frozenset[WanMode]
    acceleration_kinds: frozenset[WanAccelerationKind] = frozenset()
    requires_quantization: bool = False
    requires_offload: bool = False
    benchmark_required: bool = False
    priority: int = Field(default=100, ge=0)
    rationale: str = Field(min_length=1)
    evidence_version: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_modes(self) -> "GpuRecommendation":
        if not self.applicable_modes:
            raise ValueError("GPU recommendation must apply to at least one Wan mode")
        return self


class GpuRecommendationCatalog(DomainModel):
    catalog_version: str = Field(min_length=1)
    recommendations: tuple[GpuRecommendation, ...]

    @model_validator(mode="after")
    def validate_catalog(self) -> "GpuRecommendationCatalog":
        if not self.recommendations:
            raise ValueError("GPU recommendation catalog cannot be empty")
        require_unique(
            [item.recommendation_id for item in self.recommendations],
            "GPU recommendation IDs",
        )
        return self

    def for_workload(
        self,
        workload: WanWorkloadProfile,
        modes: frozenset[WanMode],
    ) -> tuple[GpuRecommendation, ...]:
        return tuple(
            item
            for item in self.recommendations
            if item.workload is workload and modes <= item.applicable_modes
        )


class AvailableGpuCandidate(DomainModel):
    """Normalized live provider inventory supplied by a provider integration."""

    gpu_id: Identifier
    display_name: str = Field(min_length=1)
    vram_gib: int = Field(gt=0)
    available: bool
    hourly_price_usd: float | None = Field(default=None, gt=0.0)
    cloud_type: str = ""
    location: str = ""
    supported_acceleration_method_ids: tuple[Identifier, ...] = ()


class GpuSelectionRequest(DomainModel):
    workload: WanWorkloadProfile
    modes: frozenset[WanMode]
    preferred_tiers: tuple[GpuRecommendationTier, ...] = (
        GpuRecommendationTier.VALUE,
        GpuRecommendationTier.SPEED,
        GpuRecommendationTier.FULL_MEMORY,
        GpuRecommendationTier.FALLBACK,
        GpuRecommendationTier.MINIMUM_LATENCY,
    )
    require_full_memory: bool = False
    allow_quantization: bool = True
    allow_offload: bool = True

    @model_validator(mode="after")
    def validate_request(self) -> "GpuSelectionRequest":
        if not self.modes:
            raise ValueError("GPU selection requires at least one Wan mode")
        require_unique(self.preferred_tiers, "preferred GPU recommendation tiers")
        return self


class RankedGpuCandidate(DomainModel):
    candidate: AvailableGpuCandidate
    suitable: bool
    score: int = Field(ge=0)
    recommendation_ids: tuple[Identifier, ...] = ()
    tiers: tuple[GpuRecommendationTier, ...] = ()
    requires_quantization: bool = False
    requires_offload: bool = False
    rationale: str


class WanBenchmarkConfiguration(DomainModel):
    model_id: Identifier
    mode: WanMode
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    frame_count: int = Field(gt=0)
    steps: int = Field(gt=0)
    precision: str = Field(min_length=1)
    quantization: str = Field(min_length=1)
    offload_mode: str = Field(min_length=1)
    scheduler: str = Field(min_length=1)
    acceleration_method_id: Identifier | None = None
    runtime_version: str = Field(min_length=1)


class GpuBenchmarkEvidence(DomainModel):
    benchmark_id: Identifier
    workload: WanWorkloadProfile
    gpu_id: Identifier
    configuration: WanBenchmarkConfiguration
    generation_seconds: float = Field(gt=0.0)
    measured_at: str = Field(min_length=1)
    evidence_ref: str = Field(min_length=1)


class GpuCostEstimate(DomainModel):
    benchmark_id: Identifier
    gpu_id: Identifier
    generation_seconds: float = Field(gt=0.0)
    hourly_price_usd: float = Field(gt=0.0)
    estimated_generation_cost_usd: float = Field(ge=0.0)
    exact_configuration_match: bool = True
    warning: str = (
        "Estimate uses matching historical benchmark evidence; live runtime may vary."
    )


def estimate_generation_cost(
    *,
    candidate: AvailableGpuCandidate,
    request: WanBenchmarkConfiguration,
    benchmark: GpuBenchmarkEvidence,
) -> GpuCostEstimate | None:
    """Estimate cost only from an available exact-SKU, exact-configuration match."""

    if (
        not candidate.available
        or candidate.hourly_price_usd is None
        or candidate.gpu_id != benchmark.gpu_id
        or request != benchmark.configuration
    ):
        return None
    hourly_price = candidate.hourly_price_usd
    return GpuCostEstimate(
        benchmark_id=benchmark.benchmark_id,
        gpu_id=candidate.gpu_id,
        generation_seconds=benchmark.generation_seconds,
        hourly_price_usd=hourly_price,
        estimated_generation_cost_usd=(
            benchmark.generation_seconds * hourly_price / 3600.0
        ),
    )


def rank_gpu_candidates(
    *,
    catalog: GpuRecommendationCatalog,
    request: GpuSelectionRequest,
    candidates: tuple[AvailableGpuCandidate, ...],
) -> tuple[RankedGpuCandidate, ...]:
    """Rank normalized inventory by workload suitability before hourly price."""

    recommendations = catalog.for_workload(request.workload, request.modes)
    tier_position = {
        tier: index for index, tier in enumerate(request.preferred_tiers)
    }
    ranked: list[RankedGpuCandidate] = []
    for candidate in candidates:
        matching = tuple(
            item for item in recommendations if item.gpu_id == candidate.gpu_id
        )
        permitted = tuple(
            item
            for item in matching
            if (request.allow_quantization or not item.requires_quantization)
            and (request.allow_offload or not item.requires_offload)
            and (
                not request.require_full_memory
                or item.tier is GpuRecommendationTier.FULL_MEMORY
            )
        )
        if not candidate.available:
            suitable = False
            score = 0
            rationale = "Currently unavailable in the normalized provider inventory."
        elif not permitted:
            suitable = False
            score = 0
            rationale = "No approved recommendation matches the selected workload policy."
        else:
            suitable = True
            best = min(
                permitted,
                key=lambda item: (
                    tier_position.get(item.tier, len(tier_position)),
                    -item.priority,
                    item.recommendation_id,
                ),
            )
            tier_score = max(
                0,
                len(request.preferred_tiers)
                - tier_position.get(best.tier, len(request.preferred_tiers)),
            )
            score = tier_score * 10_000 + best.priority
            rationale = best.rationale
        ranked.append(
            RankedGpuCandidate(
                candidate=candidate,
                suitable=suitable,
                score=score,
                recommendation_ids=tuple(item.recommendation_id for item in permitted),
                tiers=tuple(item.tier for item in permitted),
                requires_quantization=any(
                    item.requires_quantization for item in permitted
                ),
                requires_offload=any(item.requires_offload for item in permitted),
                rationale=rationale,
            )
        )
    return tuple(
        sorted(
            ranked,
            key=lambda item: (
                not item.suitable,
                -item.score,
                item.candidate.hourly_price_usd
                if item.candidate.hourly_price_usd is not None
                else float("inf"),
                item.candidate.gpu_id,
            ),
        )
    )


def approved_gpu_recommendation_catalog() -> GpuRecommendationCatalog:
    """Return the versioned initial recommendation matrix approved in July 2026."""

    prompt_i2v = frozenset({WanMode.PROMPT, WanMode.I2V})
    general = frozenset({WanMode.PROMPT, WanMode.I2V, WanMode.FIRST_LAST})
    specialized = frozenset({WanMode.ANIMATE, WanMode.REPLACE})
    all_acceleration = frozenset(WanAccelerationKind)
    return GpuRecommendationCatalog(
        catalog_version="2026-07-23",
        recommendations=(
            GpuRecommendation(
                recommendation_id="ti2v5b-rtx4090-value",
                workload=WanWorkloadProfile.TI2V_5B,
                gpu_id="nvidia-rtx-4090",
                display_name="NVIDIA RTX 4090",
                vram_gib=24,
                tier=GpuRecommendationTier.VALUE,
                applicable_modes=prompt_i2v,
                acceleration_kinds=all_acceleration,
                priority=300,
                rationale="Best initial value target for TI2V-5B Prompt and I2V.",
                evidence_version="approved-spec-2026-07-23",
            ),
            GpuRecommendation(
                recommendation_id="ti2v5b-rtx5090-speed",
                workload=WanWorkloadProfile.TI2V_5B,
                gpu_id="nvidia-rtx-5090",
                display_name="NVIDIA RTX 5090",
                vram_gib=32,
                tier=GpuRecommendationTier.SPEED,
                applicable_modes=prompt_i2v,
                acceleration_kinds=all_acceleration,
                priority=300,
                rationale="Preferred TI2V-5B speed choice with additional VRAM headroom.",
                evidence_version="approved-spec-2026-07-23",
            ),
            GpuRecommendation(
                recommendation_id="ti2v5b-rtx6000ada-production",
                workload=WanWorkloadProfile.TI2V_5B,
                gpu_id="nvidia-rtx-6000-ada",
                display_name="NVIDIA RTX 6000 Ada",
                vram_gib=48,
                tier=GpuRecommendationTier.FULL_MEMORY,
                applicable_modes=prompt_i2v,
                acceleration_kinds=all_acceleration,
                priority=300,
                rationale="Production-headroom choice for TI2V-5B.",
                evidence_version="approved-spec-2026-07-23",
            ),
            GpuRecommendation(
                recommendation_id="wan14b-rtx6000ada-value",
                workload=WanWorkloadProfile.GENERAL_14B,
                gpu_id="nvidia-rtx-6000-ada",
                display_name="NVIDIA RTX 6000 Ada",
                vram_gib=48,
                tier=GpuRecommendationTier.VALUE,
                applicable_modes=general,
                acceleration_kinds=all_acceleration,
                requires_quantization=True,
                requires_offload=True,
                priority=300,
                rationale="Value choice for quantized or offloaded 14B generation.",
                evidence_version="approved-spec-2026-07-23",
            ),
            GpuRecommendation(
                recommendation_id="wan14b-pro6000-speed",
                workload=WanWorkloadProfile.GENERAL_14B,
                gpu_id="nvidia-rtx-pro-6000-blackwell",
                display_name="NVIDIA RTX PRO 6000 Blackwell",
                vram_gib=96,
                tier=GpuRecommendationTier.SPEED,
                applicable_modes=general,
                acceleration_kinds=all_acceleration,
                priority=300,
                rationale="Preferred fast 14B choice with full-memory headroom.",
                evidence_version="approved-spec-2026-07-23",
            ),
            GpuRecommendation(
                recommendation_id="wan14b-pro6000-production",
                workload=WanWorkloadProfile.GENERAL_14B,
                gpu_id="nvidia-rtx-pro-6000-blackwell",
                display_name="NVIDIA RTX PRO 6000 Blackwell",
                vram_gib=96,
                tier=GpuRecommendationTier.FULL_MEMORY,
                applicable_modes=general,
                acceleration_kinds=all_acceleration,
                priority=290,
                rationale="Preferred full-memory production choice for 14B workflows.",
                evidence_version="approved-spec-2026-07-23",
            ),
            GpuRecommendation(
                recommendation_id="wan14b-a40-fallback",
                workload=WanWorkloadProfile.GENERAL_14B,
                gpu_id="nvidia-a40",
                display_name="NVIDIA A40",
                vram_gib=48,
                tier=GpuRecommendationTier.FALLBACK,
                applicable_modes=general,
                acceleration_kinds=all_acceleration,
                requires_quantization=True,
                requires_offload=True,
                priority=150,
                rationale="Cost-oriented non-interactive 14B batch fallback.",
                evidence_version="approved-spec-2026-07-23",
            ),
            GpuRecommendation(
                recommendation_id="wan14b-a6000-fallback",
                workload=WanWorkloadProfile.GENERAL_14B,
                gpu_id="nvidia-rtx-a6000",
                display_name="NVIDIA RTX A6000",
                vram_gib=48,
                tier=GpuRecommendationTier.FALLBACK,
                applicable_modes=general,
                acceleration_kinds=all_acceleration,
                requires_quantization=True,
                requires_offload=True,
                priority=140,
                rationale="Cost-oriented non-interactive 14B batch fallback.",
                evidence_version="approved-spec-2026-07-23",
            ),
            GpuRecommendation(
                recommendation_id="wan14b-specialized-a100-value",
                workload=WanWorkloadProfile.SPECIALIZED_14B,
                gpu_id="nvidia-a100-80gb",
                display_name="NVIDIA A100 80 GB",
                vram_gib=80,
                tier=GpuRecommendationTier.VALUE,
                applicable_modes=specialized,
                acceleration_kinds=all_acceleration,
                priority=300,
                rationale="Initial value baseline for full-memory Animate and Replace.",
                evidence_version="approved-spec-2026-07-23",
            ),
            GpuRecommendation(
                recommendation_id="wan14b-specialized-pro6000-speed",
                workload=WanWorkloadProfile.SPECIALIZED_14B,
                gpu_id="nvidia-rtx-pro-6000-blackwell",
                display_name="NVIDIA RTX PRO 6000 Blackwell",
                vram_gib=96,
                tier=GpuRecommendationTier.SPEED,
                applicable_modes=specialized,
                acceleration_kinds=all_acceleration,
                priority=300,
                rationale="Preferred fast Animate and Replace choice.",
                evidence_version="approved-spec-2026-07-23",
            ),
            GpuRecommendation(
                recommendation_id="wan14b-specialized-pro6000-production",
                workload=WanWorkloadProfile.SPECIALIZED_14B,
                gpu_id="nvidia-rtx-pro-6000-blackwell",
                display_name="NVIDIA RTX PRO 6000 Blackwell",
                vram_gib=96,
                tier=GpuRecommendationTier.FULL_MEMORY,
                applicable_modes=specialized,
                acceleration_kinds=all_acceleration,
                priority=290,
                rationale="Preferred full-memory production Animate and Replace choice.",
                evidence_version="approved-spec-2026-07-23",
            ),
            GpuRecommendation(
                recommendation_id="wan14b-h100-minimum-latency",
                workload=WanWorkloadProfile.MINIMUM_LATENCY_14B,
                gpu_id="nvidia-h100-80gb",
                display_name="NVIDIA H100 80 GB",
                vram_gib=80,
                tier=GpuRecommendationTier.MINIMUM_LATENCY,
                applicable_modes=frozenset(WanMode),
                acceleration_kinds=all_acceleration,
                benchmark_required=True,
                priority=300,
                rationale="Use only when a matching benchmark justifies the latency premium.",
                evidence_version="approved-spec-2026-07-23",
            ),
        ),
    )


__all__ = [
    "AvailableGpuCandidate",
    "GpuBenchmarkEvidence",
    "GpuCostEstimate",
    "GpuRecommendation",
    "GpuRecommendationCatalog",
    "GpuRecommendationTier",
    "GpuSelectionRequest",
    "RankedGpuCandidate",
    "WanWorkloadProfile",
    "WanBenchmarkConfiguration",
    "approved_gpu_recommendation_catalog",
    "estimate_generation_cost",
    "rank_gpu_candidates",
]
