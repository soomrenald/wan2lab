#!/usr/bin/env python3
"""Run a reproducible Wan2.1 14B first/last-frame hardware smoke test."""

from __future__ import annotations

import argparse
import json

from wan2core.backends import WanMode
from wan2core.segments import SegmentRequest
from wan2core.workers import GenerateSegmentRequest, LoadModelRequest
from wan2lab.backends.comfyui import BACKEND_ID, ComfyUIClient
from wan2lab.worker import ComfyWorkerService, ThreadCancellation, WanOutOfMemory


MODEL_FILENAME = "Wan2_1-FLF2V-14B-720P_fp8_e4m3fn.safetensors"
VAE_FILENAME = "Wan2_1_VAE_bf16.safetensors"
TEXT_ENCODER_FILENAME = "umt5-xxl-enc-fp8_e4m3fn.safetensors"
CLIP_VISION_FILENAME = "clip_vision_h.safetensors"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8188")
    parser.add_argument(
        "--start-image",
        default="wan2lab/krea-wan-handoff.png",
        help="ComfyUI input-relative first-frame path",
    )
    parser.add_argument(
        "--end-image",
        default="wan2lab/krea-wan-handoff-end.png",
        help="ComfyUI input-relative last-frame path",
    )
    parser.add_argument("--width", type=int, default=832)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--frames", type=int, default=17)
    parser.add_argument("--steps", type=int, default=30)
    parser.add_argument("--seed", type=int, default=20260730)
    parser.add_argument(
        "--prompt",
        default=(
            "干净明亮的摄影棚里，蓝色木制艺术人体模型平稳地抬起右臂挥手，"
            "橙色木制人体模型安静地站在旁边。镜头固定，全身广角，动作连贯自然。"
        ),
    )
    parser.add_argument(
        "--negative-prompt",
        default=(
            "过曝，静态，模糊，低质量，畸形，额外肢体，闪烁，跳帧，字幕，水印"
        ),
    )
    parser.add_argument(
        "--output-prefix",
        default="wan2lab/hardware/wan2_1_flf2v_17f_30step",
    )
    parser.add_argument(
        "--release",
        action="store_true",
        help="Release ComfyUI model memory after the result is recorded",
    )
    args = parser.parse_args()
    if args.width <= 0 or args.width % 8:
        parser.error("--width must be a positive multiple of 8")
    if args.height <= 0 or args.height % 8:
        parser.error("--height must be a positive multiple of 8")
    if args.frames < 5 or (args.frames - 1) % 4:
        parser.error("--frames must be at least 5 and satisfy 4n+1")
    if args.steps < 1:
        parser.error("--steps must be positive")
    return args


def main() -> int:
    args = parse_args()
    client = ComfyUIClient(args.base_url, timeout_seconds=30)
    service = ComfyWorkerService(client, poll_interval_seconds=1.0)
    capabilities_event = service.inspect("wan2-1-flf2v-smoke-inspect")
    capabilities = service.capabilities
    assert capabilities is not None
    model = next(
        (item for item in capabilities.model_variants if item.display_name == MODEL_FILENAME),
        None,
    )
    if model is None:
        raise RuntimeError(f"ComfyUI does not expose the required model: {MODEL_FILENAME}")
    if WanMode.FIRST_LAST not in model.supported_modes:
        raise RuntimeError("the discovered model does not expose first/last-frame mode")

    components = capabilities_event.capabilities["component_models"]
    required_components = {
        "vae": VAE_FILENAME,
        "text_encoder": TEXT_ENCODER_FILENAME,
        "clip_vision": CLIP_VISION_FILENAME,
    }
    for component, filename in required_components.items():
        if filename not in components[component]:
            raise RuntimeError(f"ComfyUI does not expose the required {component}: {filename}")

    service.load(
        LoadModelRequest(
            command_id="wan2-1-flf2v-smoke-load",
            backend_id=BACKEND_ID,
            model_id=model.model_id,
            precision="bf16",
            quantization="disabled",
            offload_mode="offload_device",
            component_model_ids=required_components,
        )
    )
    selection = service.selections[model.model_id]
    print(
        json.dumps(
            {
                "kind": "loaded",
                "model": selection.model_filename,
                "vae": selection.vae_filename,
                "text_encoder": selection.text_encoder_filename,
                "clip_vision": selection.clip_vision_filename,
                "blocks_to_swap": selection.blocks_to_swap,
            },
            indent=2,
        ),
        flush=True,
    )

    request = SegmentRequest(
        request_id="wan2-1-flf2v-smoke-request",
        segment_id="wan2-1-flf2v-smoke-segment",
        mode=WanMode.FIRST_LAST,
        backend_id=BACKEND_ID,
        model_id=model.model_id,
        start_ms=0,
        end_ms=round((args.frames - 1) / 16 * 1000),
        width=args.width,
        height=args.height,
        generation_fps=16,
        frame_count=args.frames,
        start_image_asset_id="smoke-first-frame",
        end_image_asset_id="smoke-last-frame",
        prompt=args.prompt,
        negative_prompt=args.negative_prompt,
        parameters={
            "steps": args.steps,
            "cfg": 6.0,
            "shift": 5.0,
            "force_offload": True,
            "enable_vae_tiling": True,
            "tiled_vae": True,
            "tile_x": 128,
            "tile_y": 128,
            "tile_stride_x": 64,
            "tile_stride_y": 64,
            "device": "cpu",
        },
    )
    last_stage: str | None = None

    def progress(event) -> None:
        nonlocal last_stage
        item = event.progress
        if item.stage != last_stage:
            print(f"{item.stage}: {item.message}", flush=True)
            last_stage = item.stage

    exit_code = 0
    try:
        result = service.generate(
            GenerateSegmentRequest(
                command_id="wan2-1-flf2v-smoke-generate",
                job_id="wan2-1-flf2v-smoke-job",
                request=request,
                asset_inputs={
                    "smoke-first-frame": args.start_image,
                    "smoke-last-frame": args.end_image,
                },
                seed=args.seed,
                output_prefix=args.output_prefix,
            ),
            ThreadCancellation(),
            progress,
        )
        print(json.dumps(result.model_dump(mode="json"), indent=2))
    except WanOutOfMemory as error:
        print(
            json.dumps(
                {
                    "kind": "error",
                    "recoverable": True,
                    "message": str(error),
                    "recovery_actions": list(error.recovery_actions),
                },
                indent=2,
            )
        )
        exit_code = 2
    finally:
        if args.release:
            service.release("wan2-1-flf2v-smoke-release")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
