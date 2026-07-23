"""Backend/model capability records and normalized request validation."""

from __future__ import annotations

from enum import StrEnum
from math import ceil, floor
from typing import Annotated, Literal

from pydantic import Field, model_validator

from wan2core.base import DomainModel, Identifier


class WanMode(StrEnum):
    PROMPT = "prompt"
    I2V = "i2v"
    FIRST_LAST = "first_last"
    ANIMATE = "animate"
    REPLACE = "replace"


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
    "WanMode",
]
