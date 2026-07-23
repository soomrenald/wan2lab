"""Backend/model capability records and normalized request validation."""

from __future__ import annotations

from enum import StrEnum
from math import ceil, floor
from typing import Annotated, Literal

from pydantic import Field, model_validator

from wan2core.base import DomainModel, Identifier, require_unique


class WanMode(StrEnum):
    PROMPT = "prompt"
    I2V = "i2v"
    FIRST_LAST = "first_last"
    ANIMATE = "animate"
    REPLACE = "replace"


class WanAccelerationKind(StrEnum):
    LIGHTX2V = "lightx2v"
    FASTVIDEO = "fastvideo"
    LIGHTNING = "lightning"
    CACHE = "cache"


class WanAccelerationSelection(StrEnum):
    AUTO = "auto"
    SPECIFIC = "specific"


class WanAccelerationQuality(StrEnum):
    PREVIEW = "preview"
    BALANCED = "balanced"
    QUALITY = "quality"


class SegmentAccelerationMode(StrEnum):
    INHERIT = "inherit"
    ENABLED = "enabled"
    DISABLED = "disabled"
    SPECIFIC = "specific"


class WanAccelerationPolicy(DomainModel):
    enabled: bool = True
    selection: WanAccelerationSelection = WanAccelerationSelection.AUTO
    quality: WanAccelerationQuality = WanAccelerationQuality.BALANCED
    preferred_method_ids: tuple[Identifier, ...] = ()
    specific_method_id: Identifier | None = None

    @model_validator(mode="after")
    def validate_selection(self) -> "WanAccelerationPolicy":
        require_unique(self.preferred_method_ids, "preferred acceleration method IDs")
        if (
            self.selection is WanAccelerationSelection.SPECIFIC
            and self.specific_method_id is None
        ):
            raise ValueError("specific acceleration selection requires a method ID")
        if (
            self.selection is WanAccelerationSelection.AUTO
            and self.specific_method_id is not None
        ):
            raise ValueError("automatic acceleration selection cannot name a specific method")
        return self


class SegmentAccelerationPolicy(DomainModel):
    mode: SegmentAccelerationMode = SegmentAccelerationMode.INHERIT
    method_id: Identifier | None = None

    @model_validator(mode="after")
    def validate_override(self) -> "SegmentAccelerationPolicy":
        if self.mode is SegmentAccelerationMode.SPECIFIC and self.method_id is None:
            raise ValueError("specific segment acceleration requires a method ID")
        if self.mode is not SegmentAccelerationMode.SPECIFIC and self.method_id is not None:
            raise ValueError("only a specific segment override may name a method")
        return self


class WanAccelerationMethodCapabilities(DomainModel):
    method_id: Identifier
    display_name: str = Field(min_length=1)
    kind: WanAccelerationKind
    supported_modes: frozenset[WanMode]
    supported_model_families: tuple[str, ...] = ()
    supported_model_ids: tuple[Identifier, ...] = ()
    accelerator_vendors: frozenset[str] = frozenset()
    required_artifact_ids: tuple[Identifier, ...] = ()
    mutually_exclusive_method_ids: tuple[Identifier, ...] = ()
    incompatible_adapter_kinds: tuple[str, ...] = ()
    supported_quality_profiles: frozenset[WanAccelerationQuality] = frozenset(
        {
            WanAccelerationQuality.PREVIEW,
            WanAccelerationQuality.BALANCED,
            WanAccelerationQuality.QUALITY,
        }
    )
    rank: int = Field(default=100, ge=0)
    deterministic: bool = True
    default_parameters: dict[str, object] = Field(default_factory=dict)
    schedule_description: str = ""
    speed_summary: str = ""
    quality_tradeoff: str = ""

    @model_validator(mode="after")
    def validate_compatibility(self) -> "WanAccelerationMethodCapabilities":
        if not self.supported_modes:
            raise ValueError("acceleration method must support at least one Wan mode")
        if not self.supported_model_families and not self.supported_model_ids:
            raise ValueError("acceleration method must declare compatible models")
        if not self.supported_quality_profiles:
            raise ValueError("acceleration method must support at least one quality profile")
        require_unique(self.supported_model_families, "acceleration model families")
        require_unique(self.supported_model_ids, "acceleration model IDs")
        require_unique(self.required_artifact_ids, "acceleration artifact IDs")
        require_unique(
            self.mutually_exclusive_method_ids,
            "mutually exclusive acceleration method IDs",
        )
        if self.method_id in self.mutually_exclusive_method_ids:
            raise ValueError("acceleration method cannot be mutually exclusive with itself")
        return self

    def supports(
        self,
        *,
        model_id: str,
        model_family: str,
        mode: WanMode,
        accelerator_vendor: str,
        quality: WanAccelerationQuality,
        installed_artifact_ids: frozenset[str],
    ) -> bool:
        model_matches = (
            model_id in self.supported_model_ids
            or model_family in self.supported_model_families
        )
        vendor_matches = (
            not self.accelerator_vendors
            or accelerator_vendor in self.accelerator_vendors
        )
        return (
            model_matches
            and mode in self.supported_modes
            and vendor_matches
            and quality in self.supported_quality_profiles
            and set(self.required_artifact_ids) <= installed_artifact_ids
        )


class ResolvedWanAcceleration(DomainModel):
    requested_enabled: bool
    requested_selection: WanAccelerationSelection
    requested_method_id: Identifier | None = None
    quality: WanAccelerationQuality
    active: bool
    method_id: Identifier | None = None
    method_kind: WanAccelerationKind | None = None
    artifact_ids: tuple[Identifier, ...] = ()
    resolved_parameters: dict[str, object] = Field(default_factory=dict)
    schedule_description: str = ""
    warnings: tuple[str, ...] = ()
    fallback_reason: str | None = None

    @model_validator(mode="after")
    def validate_resolution(self) -> "ResolvedWanAcceleration":
        if self.active and (self.method_id is None or self.method_kind is None):
            raise ValueError("active acceleration requires a resolved method")
        if self.active and self.fallback_reason is not None:
            raise ValueError("active acceleration cannot have a fallback reason")
        if not self.active and self.fallback_reason is None:
            raise ValueError("inactive acceleration requires an explicit reason")
        return self


def resolve_wan_acceleration(
    *,
    project_policy: WanAccelerationPolicy,
    segment_policy: SegmentAccelerationPolicy,
    methods: tuple[WanAccelerationMethodCapabilities, ...],
    model_id: str,
    model_family: str,
    mode: WanMode,
    accelerator_vendor: str,
    installed_artifact_ids: frozenset[str] = frozenset(),
) -> ResolvedWanAcceleration:
    """Resolve requested policy without ever misreporting base inference as accelerated."""

    requested_enabled = project_policy.enabled
    requested_selection = project_policy.selection
    requested_method_id = project_policy.specific_method_id
    if segment_policy.mode is SegmentAccelerationMode.DISABLED:
        requested_enabled = False
        requested_method_id = None
    elif segment_policy.mode is SegmentAccelerationMode.ENABLED:
        requested_enabled = True
        requested_selection = WanAccelerationSelection.AUTO
        requested_method_id = None
    elif segment_policy.mode is SegmentAccelerationMode.SPECIFIC:
        requested_enabled = True
        requested_selection = WanAccelerationSelection.SPECIFIC
        requested_method_id = segment_policy.method_id

    if not requested_enabled:
        return ResolvedWanAcceleration(
            requested_enabled=False,
            requested_selection=requested_selection,
            requested_method_id=requested_method_id,
            quality=project_policy.quality,
            active=False,
            fallback_reason="Acceleration is disabled by project or segment policy.",
        )

    compatible = tuple(
        method
        for method in methods
        if method.supports(
            model_id=model_id,
            model_family=model_family,
            mode=mode,
            accelerator_vendor=accelerator_vendor,
            quality=project_policy.quality,
            installed_artifact_ids=installed_artifact_ids,
        )
    )
    if requested_method_id is not None:
        compatible = tuple(
            method for method in compatible if method.method_id == requested_method_id
        )
    if not compatible:
        requested = (
            f"requested method {requested_method_id!r}"
            if requested_method_id is not None
            else "automatic selection"
        )
        return ResolvedWanAcceleration(
            requested_enabled=True,
            requested_selection=requested_selection,
            requested_method_id=requested_method_id,
            quality=project_policy.quality,
            active=False,
            fallback_reason=(
                f"No installed compatible Wan acceleration method satisfied {requested} "
                f"for model {model_id!r} in {mode.value} mode; using base inference."
            ),
        )

    preference = {
        method_id: index
        for index, method_id in enumerate(project_policy.preferred_method_ids)
    }
    selected = min(
        compatible,
        key=lambda method: (
            preference.get(method.method_id, len(preference)),
            -method.rank,
            method.method_id,
        ),
    )
    return ResolvedWanAcceleration(
        requested_enabled=True,
        requested_selection=requested_selection,
        requested_method_id=requested_method_id,
        quality=project_policy.quality,
        active=True,
        method_id=selected.method_id,
        method_kind=selected.kind,
        artifact_ids=selected.required_artifact_ids,
        resolved_parameters=selected.default_parameters,
        schedule_description=selected.schedule_description,
    )


class ParameterType(StrEnum):
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"
    STRING = "string"
    ENUM = "enum"


class ParameterGroup(StrEnum):
    COMMON = "common"
    ADVANCED = "advanced"


class ParameterDescriptor(DomainModel):
    key: Identifier
    display_name: str = Field(min_length=1)
    parameter_type: ParameterType
    default: object
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple[object, ...] = ()
    applicable_modes: frozenset[WanMode]
    group: ParameterGroup = ParameterGroup.COMMON
    backend_key: str = Field(min_length=1)
    hardware_restrictions: tuple[str, ...] = ()
    help_text: str = ""

    @model_validator(mode="after")
    def validate_descriptor(self) -> "ParameterDescriptor":
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("parameter minimum must not exceed maximum")
        if self.parameter_type is ParameterType.ENUM and not self.choices:
            raise ValueError("enum parameters require choices")
        if not self.applicable_modes:
            raise ValueError("parameter must apply to at least one mode")
        self.validate_value(self.default)
        return self

    def validate_value(self, value: object) -> object:
        """Validate a resolved value without coupling consumers to a UI toolkit."""

        valid_type = {
            ParameterType.INTEGER: isinstance(value, int) and not isinstance(value, bool),
            ParameterType.NUMBER: isinstance(value, (int, float)) and not isinstance(value, bool),
            ParameterType.BOOLEAN: isinstance(value, bool),
            ParameterType.STRING: isinstance(value, str),
            ParameterType.ENUM: value in self.choices,
        }[self.parameter_type]
        if not valid_type:
            raise ValueError(f"{self.key} must have type {self.parameter_type.value}")
        if self.choices and value not in self.choices:
            raise ValueError(f"{self.key} must be one of {self.choices}")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if self.minimum is not None and value < self.minimum:
                raise ValueError(f"{self.key} must be at least {self.minimum:g}")
            if self.maximum is not None and value > self.maximum:
                raise ValueError(f"{self.key} must be at most {self.maximum:g}")
        return value


class Resolution(DomainModel):
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class FrameDurationBasis(StrEnum):
    FRAMES = "frames"
    INTERVALS = "intervals"


class FrameRounding(StrEnum):
    NEAREST = "nearest"
    FLOOR = "floor"
    CEIL = "ceil"


class AnyFrameCount(DomainModel):
    kind: Literal["any"] = "any"


class MultiplePlusOffsetFrameCount(DomainModel):
    kind: Literal["multiple_plus_offset"] = "multiple_plus_offset"
    multiple: int = Field(gt=0)
    offset: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_offset(self) -> "MultiplePlusOffsetFrameCount":
        if self.offset >= self.multiple:
            raise ValueError("frame-count offset must be smaller than the multiple")
        return self


class ExplicitFrameCount(DomainModel):
    kind: Literal["explicit"] = "explicit"
    values: tuple[int, ...]

    @model_validator(mode="after")
    def validate_values(self) -> "ExplicitFrameCount":
        if not self.values or any(value <= 0 for value in self.values):
            raise ValueError("explicit frame counts must be positive")
        if tuple(sorted(set(self.values))) != self.values:
            raise ValueError("explicit frame counts must be sorted and unique")
        return self


FrameCountRule = Annotated[
    AnyFrameCount | MultiplePlusOffsetFrameCount | ExplicitFrameCount,
    Field(discriminator="kind"),
]


class AdapterCompatibility(DomainModel):
    mode: WanMode
    model_families: tuple[str, ...] = ()
    supported_adapter_kinds: tuple[str, ...] = ()
    maximum_reference_characters: int | None = Field(default=None, gt=0)


class ModelVariantCapabilities(DomainModel):
    model_id: Identifier
    display_name: str = Field(min_length=1)
    model_family: str = ""
    supported_modes: frozenset[WanMode]
    required_inputs_by_mode: dict[WanMode, tuple[str, ...]]
    optional_inputs_by_mode: dict[WanMode, tuple[str, ...]] = Field(default_factory=dict)
    supported_resolutions: tuple[Resolution, ...]
    default_resolution: Resolution
    frame_count_rule: FrameCountRule
    duration_basis: FrameDurationBasis = FrameDurationBasis.INTERVALS
    default_frame_count: int = Field(gt=0)
    min_frame_count: int = Field(gt=0)
    max_frame_count: int = Field(gt=0)
    default_generation_fps: float = Field(gt=0.0)
    supported_generation_fps: tuple[float, ...]
    supported_precisions: tuple[str, ...]
    supported_quantizations: tuple[str, ...] = ()
    supported_offload_modes: tuple[str, ...] = ()
    adapter_compatibility: tuple[AdapterCompatibility, ...] = ()
    estimated_memory_profiles: dict[str, float] = Field(default_factory=dict)
    parameter_descriptors: tuple[ParameterDescriptor, ...] = ()
    acceleration_methods: tuple[WanAccelerationMethodCapabilities, ...] = ()

    @model_validator(mode="after")
    def validate_capabilities(self) -> "ModelVariantCapabilities":
        if not self.supported_modes:
            raise ValueError("model variant must support at least one mode")
        if self.min_frame_count > self.default_frame_count:
            raise ValueError("default frame count is below the minimum")
        if self.default_frame_count > self.max_frame_count:
            raise ValueError("default frame count exceeds the maximum")
        if self.default_generation_fps not in self.supported_generation_fps:
            raise ValueError("default generation FPS must be supported")
        if self.default_resolution not in self.supported_resolutions:
            raise ValueError("default resolution must be supported")
        if set(self.required_inputs_by_mode) != set(self.supported_modes):
            raise ValueError("every supported mode requires an input declaration")
        method_ids = [method.method_id for method in self.acceleration_methods]
        if len(method_ids) != len(set(method_ids)):
            raise ValueError("model acceleration method IDs must be unique")
        if any(
            not method.supported_modes <= self.supported_modes
            for method in self.acceleration_methods
        ):
            raise ValueError("acceleration method declares a mode unsupported by the model")
        if any(
            method.supported_model_ids
            and self.model_id not in method.supported_model_ids
            and (
                not self.model_family
                or self.model_family not in method.supported_model_families
            )
            for method in self.acceleration_methods
        ):
            raise ValueError("acceleration method is incompatible with its model variant")
        return self

    def supports_resolution(self, width: int, height: int) -> bool:
        return Resolution(width=width, height=height) in self.supported_resolutions

    def frame_duration_ms(self, frame_count: int, generation_fps: float) -> int:
        units = frame_count if self.duration_basis is FrameDurationBasis.FRAMES else frame_count - 1
        return round(max(0, units) * 1000.0 / generation_fps)

    def requested_frame_count(self, duration_ms: int, generation_fps: float) -> float:
        units = duration_ms * generation_fps / 1000.0
        return units if self.duration_basis is FrameDurationBasis.FRAMES else units + 1.0

    def resolve_frame_count(
        self,
        duration_ms: int,
        generation_fps: float,
        rounding: FrameRounding = FrameRounding.NEAREST,
    ) -> int:
        if generation_fps not in self.supported_generation_fps:
            raise ValueError(f"unsupported generation FPS: {generation_fps}")
        requested = self.requested_frame_count(duration_ms, generation_fps)
        candidates = self._valid_counts()
        if rounding is FrameRounding.FLOOR:
            valid = [value for value in candidates if value <= requested]
            return valid[-1] if valid else candidates[0]
        if rounding is FrameRounding.CEIL:
            valid = [value for value in candidates if value >= requested]
            return valid[0] if valid else candidates[-1]
        return min(candidates, key=lambda value: (abs(value - requested), value))

    def _valid_counts(self) -> tuple[int, ...]:
        rule = self.frame_count_rule
        if isinstance(rule, AnyFrameCount):
            return tuple(range(self.min_frame_count, self.max_frame_count + 1))
        if isinstance(rule, ExplicitFrameCount):
            return tuple(
                value
                for value in rule.values
                if self.min_frame_count <= value <= self.max_frame_count
            )
        first_index = ceil((self.min_frame_count - rule.offset) / rule.multiple)
        last_index = floor((self.max_frame_count - rule.offset) / rule.multiple)
        values = tuple(
            index * rule.multiple + rule.offset
            for index in range(max(0, first_index), last_index + 1)
        )
        if not values:
            raise ValueError("frame-count rule has no values inside model bounds")
        return values

    def valid_frame_counts(self) -> tuple[int, ...]:
        return self._valid_counts()


class BackendCapabilities(DomainModel):
    backend_id: Identifier
    backend_version: str = Field(min_length=1)
    accelerator_vendors: frozenset[str]
    model_variants: tuple[ModelVariantCapabilities, ...]
    runtime_features: frozenset[str] = frozenset()
    parameter_descriptors: tuple[ParameterDescriptor, ...] = ()
    wrapper_version: str = ""
    package_versions: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_models(self) -> "BackendCapabilities":
        ids = [model.model_id for model in self.model_variants]
        if len(ids) != len(set(ids)):
            raise ValueError("backend model IDs must be unique")
        return self

    def model(self, model_id: str) -> ModelVariantCapabilities:
        for model in self.model_variants:
            if model.model_id == model_id:
                return model
        raise KeyError(model_id)

    def parameters_for(
        self,
        model_id: str,
        mode: WanMode,
    ) -> tuple[ParameterDescriptor, ...]:
        """Return effective descriptors with model declarations overriding backend defaults."""

        model = self.model(model_id)
        if mode not in model.supported_modes:
            raise ValueError(f"model {model_id} does not support {mode.value}")
        descriptors: dict[str, ParameterDescriptor] = {}
        for descriptor in (*self.parameter_descriptors, *model.parameter_descriptors):
            if mode in descriptor.applicable_modes:
                descriptors[descriptor.key] = descriptor
        return tuple(descriptors.values())


__all__ = [
    "AdapterCompatibility",
    "AnyFrameCount",
    "BackendCapabilities",
    "ExplicitFrameCount",
    "FrameCountRule",
    "FrameDurationBasis",
    "FrameRounding",
    "ModelVariantCapabilities",
    "MultiplePlusOffsetFrameCount",
    "ParameterDescriptor",
    "ParameterGroup",
    "ParameterType",
    "Resolution",
    "ResolvedWanAcceleration",
    "SegmentAccelerationMode",
    "SegmentAccelerationPolicy",
    "WanAccelerationKind",
    "WanAccelerationMethodCapabilities",
    "WanAccelerationPolicy",
    "WanAccelerationQuality",
    "WanAccelerationSelection",
    "WanMode",
    "resolve_wan_acceleration",
]
