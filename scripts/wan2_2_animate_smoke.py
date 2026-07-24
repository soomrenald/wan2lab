#!/usr/bin/env python3
"""Run reproducible Wan2.2 Animate or Replace hardware acceptance."""

from __future__ import annotations

import argparse
import json

from wan2core.backends import WanMode
from wan2core.segments import SegmentRequest
from wan2core.workers import GenerateSegmentRequest, LoadModelRequest
from wan2lab.backends.comfyui import BACKEND_ID, ComfyUIClient
from wan2lab.worker import ComfyWorkerService, ThreadCancellation, WanOutOfMemory


MODEL_FILENAME = "Wan2_2-Animate-14B_fp8_e4m3fn_scaled_KJ.safetensors"
VAE_FILENAME = "Wan2_1_VAE_bf16.safetensors"
TEXT_ENCODER_FILENAME = "umt5-xxl-enc-fp8_e4m3fn.safetensors"
CLIP_VISION_FILENAME = "clip_vision_h.safetensors"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8188")
    parser.add_argument(
        "--mode",
        choices=(WanMode.ANIMATE.value, WanMode.REPLACE.value),
        default=WanMode.ANIMATE.value,
    )
    parser.add_argument("--width", type=int, default=832)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--frames", type=int, default=17)
    parser.add_argument("--steps", type=int, default=20)
    parser.add_argument("--seed", type=int, default=20260731)
    parser.add_argument("--reference-image")
    parser.add_argument("--source-video")
    parser.add_argument("--prompt")
    parser.add_argument(
        "--negative-prompt",
        default=(
            "色调艳丽，过曝，静态，细节模糊，字幕，水印，最差质量，低质量，"
            "畸形肢体，多余手指，人物身份漂移，闪烁"
        ),
    )
    parser.add_argument("--output-prefix")
    parser.add_argument(
        "--release",
        action="store_true",
        help="Release ComfyUI model memory after recording the result",
    )
    args = parser.parse_args()
    mode = WanMode(args.mode)
    if args.reference_image is None:
        args.reference_image = (
            "wan2lab/official/animate-reference.jpeg"
            if mode is WanMode.ANIMATE
            else "wan2lab/official/replace-reference.jpeg"
        )
    if args.source_video is None:
        args.source_video = (
            "wan2lab/official/animate-driving.mp4"
            if mode is WanMode.ANIMATE
            else "wan2lab/official/replace-source.mp4"
        )
    if args.prompt is None:
        args.prompt = (
            "参考图中的人物按照驱动视频自然地完成动作，身份、服装和面部特征保持一致。"
            if mode is WanMode.ANIMATE
            else "用参考图中的人物自然替换源视频主体，保留原始动作、镜头和背景。"
        )
    if args.output_prefix is None:
        args.output_prefix = f"wan2lab/hardware/wan2_2_{mode.value}_17f_20step"
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
    mode = WanMode(args.mode)
    client = ComfyUIClient(args.base_url, timeout_seconds=30)
    service = ComfyWorkerService(client, poll_interval_seconds=1.0)
    capabilities_event = service.inspect(f"wan2-2-{mode.value}-smoke-inspect")
    capabilities = service.capabilities
    assert capabilities is not None
    model = next(
        (item for item in capabilities.model_variants if item.display_name == MODEL_FILENAME),
        None,
    )
    if model is None:
        raise RuntimeError(f"ComfyUI does not expose the required model: {MODEL_FILENAME}")
    if mode not in model.supported_modes:
        raise RuntimeError(f"the installed pipeline does not expose {mode.value} mode")

    components = capabilities_event.capabilities["component_models"]
    required_components = {
        "vae": VAE_FILENAME,
        "text_encoder": TEXT_ENCODER_FILENAME,
        "clip_vision": CLIP_VISION_FILENAME,
    }
    for component, filename in required_components.items():
        if filename not in components[component]:
            raise RuntimeError(f"ComfyUI does not expose the required {component}: {filename}")
    if not components["vitpose"] or not components["yolo"]:
        raise RuntimeError("ComfyUI does not expose the required pose preprocessors")
    if mode is WanMode.REPLACE and not components["sam2"]:
        raise RuntimeError("ComfyUI does not expose the required SAM2 model declaration")

    service.load(
        LoadModelRequest(
            command_id=f"wan2-2-{mode.value}-smoke-load",
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
                "mode": mode.value,
                "model": selection.model_filename,
                "vae": selection.vae_filename,
                "text_encoder": selection.text_encoder_filename,
                "clip_vision": selection.clip_vision_filename,
                "vitpose": selection.vitpose_filename,
                "yolo": selection.yolo_filename,
                "sam2": selection.sam2_filename,
                "onnx_device": selection.onnx_device,
                "blocks_to_swap": selection.blocks_to_swap,
            },
            indent=2,
        ),
        flush=True,
    )

    reference_id = "smoke-reference-character"
    video_id = "smoke-driving-video" if mode is WanMode.ANIMATE else "smoke-source-video"
    request = SegmentRequest(
        request_id=f"wan2-2-{mode.value}-smoke-request",
        segment_id=f"wan2-2-{mode.value}-smoke-segment",
        mode=mode,
        backend_id=BACKEND_ID,
        model_id=model.model_id,
        start_ms=0,
        end_ms=round((args.frames - 1) / 16 * 1000),
        width=args.width,
        height=args.height,
        generation_fps=16,
        frame_count=args.frames,
        reference_character_asset_id=reference_id,
        driving_video_asset_id=video_id if mode is WanMode.ANIMATE else None,
        source_video_asset_id=video_id if mode is WanMode.REPLACE else None,
        prompt=args.prompt,
        negative_prompt=args.negative_prompt,
        parameters={
            "steps": args.steps,
            "cfg": 1.0,
            "shift": 5.0,
            "scheduler": "dpm++_sde",
            "force_offload": True,
            "frame_window_size": args.frames,
            "colormatch": "disabled",
            "pose_strength": 1.0,
            "face_strength": 1.0,
            "tiled_vae": False,
            "enable_vae_tiling": True,
            "tile_x": 128,
            "tile_y": 128,
            "tile_stride_x": 64,
            "tile_stride_y": 64,
            "normalization": "default",
            "use_disk_cache": False,
            "device": "cpu",
            "batched_cfg": False,
            "rope_function": "comfy",
            "riflex_freq_index": 0,
            "start_step": 0,
            "end_step": -1,
            "add_noise_to_samples": False,
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
                command_id=f"wan2-2-{mode.value}-smoke-generate",
                job_id=f"wan2-2-{mode.value}-smoke-job",
                request=request,
                asset_inputs={
                    reference_id: args.reference_image,
                    video_id: args.source_video,
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
            service.release(f"wan2-2-{mode.value}-smoke-release")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
