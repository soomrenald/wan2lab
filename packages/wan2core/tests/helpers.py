from __future__ import annotations

from wan2core.backends import (
    BackendCapabilities,
    FrameDurationBasis,
    ModelVariantCapabilities,
    MultiplePlusOffsetFrameCount,
    Resolution,
    WanMode,
)


def backend_capabilities(*, first_last: bool = True) -> BackendCapabilities:
    modes = {WanMode.PROMPT, WanMode.I2V}
    if first_last:
        modes.add(WanMode.FIRST_LAST)
    required = {
        WanMode.PROMPT: (),
        WanMode.I2V: ("start_image_asset_id",),
    }
    if first_last:
        required[WanMode.FIRST_LAST] = ("start_image_asset_id", "end_image_asset_id")
    model = ModelVariantCapabilities(
        model_id="wan-test",
        display_name="Deterministic test Wan",
        supported_modes=frozenset(modes),
        required_inputs_by_mode=required,
        supported_resolutions=(Resolution(width=1280, height=720),),
        default_resolution=Resolution(width=1280, height=720),
        frame_count_rule=MultiplePlusOffsetFrameCount(multiple=4, offset=1),
        duration_basis=FrameDurationBasis.INTERVALS,
        default_frame_count=81,
        min_frame_count=5,
        max_frame_count=81,
        default_generation_fps=16.0,
        supported_generation_fps=(16.0,),
        supported_precisions=("bf16",),
    )
    return BackendCapabilities(
        backend_id="mock-wan",
        backend_version="1.0",
        accelerator_vendors=frozenset({"cpu", "cuda", "rocm"}),
        model_variants=(model,),
        runtime_features=frozenset({"mock"}),
    )

