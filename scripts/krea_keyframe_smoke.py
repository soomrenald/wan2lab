#!/usr/bin/env python3
"""Generate a reproducible local Krea keyframe for Wan I2V handoff testing."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from k2core.worker.protocol import CommandKind
from k2core.worker.runtime import CriticalGpuMemoryPressure
from wan2lab.krea_worker import KreaCancellation, KreaWorkerService


TRANSFORMER_FILENAME = "krea2_turbo_fp8_scaled.safetensors"
TEXT_ENCODER_FILENAME = "qwen3vl_4b_fp8_scaled.safetensors"
VAE_FILENAME = "qwen_image_vae.safetensors"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--comfyui-root", type=Path, default=Path("~/ComfyUI"))
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("~/.cache/wan2lab/krea-results"),
    )
    parser.add_argument("--width", type=int, default=768)
    parser.add_argument("--height", type=int, default=432)
    parser.add_argument("--steps", type=int, default=4)
    parser.add_argument("--seed", type=int, default=20260726)
    parser.add_argument(
        "--prompt",
        default=(
            "Two friendly painted wooden artist mannequins stand side by side in "
            "a clean daylight studio, one blue and one orange, full body, wide shot."
        ),
    )
    parser.add_argument(
        "--filename-prefix",
        default="wan2lab-krea-wan-handoff",
    )
    args = parser.parse_args()
    if args.width <= 0 or args.width % 16:
        parser.error("--width must be a positive multiple of 16")
    if args.height <= 0 or args.height % 16:
        parser.error("--height must be a positive multiple of 16")
    if not 1 <= args.steps <= 100:
        parser.error("--steps must be between 1 and 100")
    return args


def main() -> int:
    args = parse_args()
    comfyui_root = args.comfyui_root.expanduser().resolve()
    model_root = comfyui_root / "models"
    service = KreaWorkerService(args.output_root.expanduser().resolve())
    loaded = service.load(
        {
            "comfyui_root": str(comfyui_root),
            "diffusion_model_file": str(
                model_root / "diffusion_models" / TRANSFORMER_FILENAME
            ),
            "text_encoder_file": str(
                model_root / "text_encoders" / TEXT_ENCODER_FILENAME
            ),
            "vae_file": str(model_root / "vae" / VAE_FILENAME),
            "memory_policy": "safe_16gb",
            "reserve_vram_gb": 4.0,
            "cpu_vae": True,
        }
    )
    print(
        json.dumps(
            {
                "kind": "loaded",
                "transformer": TRANSFORMER_FILENAME,
                "text_encoder": TEXT_ENCODER_FILENAME,
                "vae": VAE_FILENAME,
                "capabilities": loaded.get("capabilities", {}),
            },
            indent=2,
            default=str,
        ),
        flush=True,
    )

    last_stage: str | None = None

    def progress(
        stage: str,
        fraction: float | None,
        details: object,
    ) -> None:
        nonlocal last_stage
        if stage != last_stage:
            suffix = "" if fraction is None else f" ({fraction:.0%})"
            print(f"{stage}{suffix}", flush=True)
            last_stage = stage

    exit_code = 0
    try:
        result = service.execute(
            CommandKind.GENERATE_BASELINE,
            {
                "request": {
                    "operation": "generate_image",
                    "prompt": args.prompt,
                    "width": args.width,
                    "height": args.height,
                    "steps": args.steps,
                    "sampler": "euler",
                    "scheduler": "simple",
                    "seed": args.seed,
                    "filename_prefix": args.filename_prefix,
                }
            },
            cancellation=KreaCancellation(),
            progress=progress,
        )
        print(json.dumps({"kind": "result", **result}, indent=2, default=str))
    except CriticalGpuMemoryPressure as error:
        print(
            json.dumps(
                {
                    "kind": "error",
                    "recoverable": True,
                    "message": str(error),
                    "recovery_actions": [
                        "reduce_resolution",
                        "enable_cpu_vae",
                        "release_other_models",
                    ],
                },
                indent=2,
            )
        )
        exit_code = 2
    finally:
        service.release()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
