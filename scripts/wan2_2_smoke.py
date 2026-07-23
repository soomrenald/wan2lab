#!/usr/bin/env python3
"""Run a reproducible Wan2.2 TI2V-5B Prompt or I2V hardware smoke test."""

from __future__ import annotations

import argparse
import json

from wan2core.backends import WanMode
from wan2core.segments import SegmentRequest
from wan2core.workers import GenerateSegmentRequest, LoadModelRequest
from wan2lab.backends.comfyui import BACKEND_ID, ComfyUIClient
from wan2lab.worker import ComfyWorkerService, ThreadCancellation


MODEL_FILENAME = "Wan2_2-TI2V-5B_fp8_e4m3fn_scaled_KJ.safetensors"
VAE_FILENAME = "Wan2_2_VAE_bf16.safetensors"
TEXT_ENCODER_FILENAME = "umt5-xxl-enc-fp8_e4m3fn.safetensors"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8188")
    parser.add_argument("--mode", choices=("prompt", "i2v"), default="prompt")
    parser.add_argument(
        "--start-image",
        help="ComfyUI input-relative image path; required for --mode i2v",
    )
    parser.add_argument("--frames", type=int, default=5)
    parser.add_argument("--steps", type=int, default=4)
    parser.add_argument("--seed", type=int, default=20260723)
    parser.add_argument("--prompt", default="A paper windmill turns gently in a clean studio.")
    parser.add_argument("--negative-prompt", default="flicker, distortion, text, watermark")
    parser.add_argument("--output-prefix", default="wan2lab/hardware/wan2_2_ti2v_smoke")
    parser.add_argument(
        "--release",
        action="store_true",
        help="Release ComfyUI model memory after the result is recorded",
    )
    args = parser.parse_args()
    if args.mode == "i2v" and not args.start_image:
        parser.error("--start-image is required for --mode i2v")
    return args


def main() -> int:
    args = parse_args()
    client = ComfyUIClient(args.base_url, timeout_seconds=30)
    service = ComfyWorkerService(client, poll_interval_seconds=1.0)
    capabilities_event = service.inspect("wan2-2-smoke-inspect")
    capabilities = service.capabilities
    assert capabilities is not None
    model = next(
        (item for item in capabilities.model_variants if item.display_name == MODEL_FILENAME),
        None,
    )
    if model is None:
        raise RuntimeError(f"ComfyUI does not expose the required model: {MODEL_FILENAME}")
    components = capabilities_event.capabilities["component_models"]
    for component, filename in (
        ("vae", VAE_FILENAME),
        ("text_encoder", TEXT_ENCODER_FILENAME),
    ):
        if filename not in components[component]:
            raise RuntimeError(f"ComfyUI does not expose the required {component}: {filename}")

    service.load(
        LoadModelRequest(
            command_id="wan2-2-smoke-load",
            backend_id=BACKEND_ID,
            model_id=model.model_id,
            precision="bf16",
            quantization="disabled",
            offload_mode="offload_device",
            component_model_ids={
                "vae": VAE_FILENAME,
                "text_encoder": TEXT_ENCODER_FILENAME,
            },
        )
    )
    mode = WanMode(args.mode)
    start_image_asset_id = "smoke-start-image" if mode is WanMode.I2V else None
    parameters: dict[str, object] = {
        "steps": args.steps,
        "cfg": 6.0,
        "shift": 5.0,
        "force_offload": True,
        "enable_vae_tiling": True,
        "rope_function": "comfy_chunked",
        "device": "cpu",
    }
    if mode is WanMode.I2V:
        parameters["tiled_vae"] = True
    request = SegmentRequest(
        request_id="wan2-2-smoke-request",
        segment_id="wan2-2-smoke-segment",
        mode=mode,
        backend_id=BACKEND_ID,
        model_id=model.model_id,
        start_ms=0,
        end_ms=max(1, round((args.frames - 1) / 24 * 1000)),
        width=1280,
        height=704,
        generation_fps=24,
        frame_count=args.frames,
        start_image_asset_id=start_image_asset_id,
        prompt=args.prompt,
        negative_prompt=args.negative_prompt,
        parameters=parameters,
    )
    asset_inputs = (
        {start_image_asset_id: args.start_image}
        if start_image_asset_id is not None
        else {}
    )
    last_stage: str | None = None

    def progress(event) -> None:
        nonlocal last_stage
        item = event.progress
        if item.stage != last_stage:
            print(f"{item.stage}: {item.message}", flush=True)
            last_stage = item.stage

    try:
        result = service.generate(
            GenerateSegmentRequest(
                command_id="wan2-2-smoke-generate",
                job_id="wan2-2-smoke-job",
                request=request,
                asset_inputs=asset_inputs,
                seed=args.seed,
                output_prefix=args.output_prefix,
            ),
            ThreadCancellation(),
            progress,
        )
        print(json.dumps(result.model_dump(mode="json"), indent=2))
    finally:
        if args.release:
            service.release("wan2-2-smoke-release")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
