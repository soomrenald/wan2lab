#!/usr/bin/env python3
"""Generate and stage a region-routed two-character Krea keyframe."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
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
        default=Path("~/.cache/wan2lab/krea-adapter-keyframes"),
    )
    parser.add_argument(
        "--adapter",
        type=Path,
        default=Path("~/ComfyUI/models/loras/krea_lface_tonly.safetensors"),
    )
    parser.add_argument(
        "--staged-name",
        default="wan2lab/hardware/krea-adapter-two-character.png",
    )
    parser.add_argument("--width", type=int, default=768)
    parser.add_argument("--height", type=int, default=432)
    parser.add_argument("--steps", type=int, default=4)
    parser.add_argument("--seed", type=int, default=20260804)
    parser.add_argument("--adapter-strength", type=float, default=0.7)
    args = parser.parse_args()
    if args.width <= 0 or args.width % 16:
        parser.error("--width must be a positive multiple of 16")
    if args.height <= 0 or args.height % 16:
        parser.error("--height must be a positive multiple of 16")
    if not 1 <= args.steps <= 100:
        parser.error("--steps must be between 1 and 100")
    if not 0.0 < args.adapter_strength <= 2.0:
        parser.error("--adapter-strength must be in (0, 2]")
    return args


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    args = parse_args()
    comfyui_root = args.comfyui_root.expanduser().resolve()
    adapter = args.adapter.expanduser().resolve()
    if not adapter.is_file():
        raise FileNotFoundError(adapter)
    model_root = comfyui_root / "models"
    service = KreaWorkerService(args.output_root.expanduser().resolve())
    service.load(
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

    last_stage: str | None = None

    def progress(stage: str, fraction: float | None, details: object) -> None:
        del details
        nonlocal last_stage
        if stage != last_stage:
            suffix = "" if fraction is None else f" ({fraction:.0%})"
            print(f"{stage}{suffix}", flush=True)
            last_stage = stage

    request = {
        "operation": "generate_image",
        "prompt": (
            "A clean cinematic daylight studio, neutral background, full-body "
            "portrait framing, symmetrical composition, and a locked wide camera."
        ),
        "width": args.width,
        "height": args.height,
        "steps": args.steps,
        "sampler": "euler",
        "scheduler": "simple",
        "seed": args.seed,
        "filename_prefix": "wan2lab-krea-adapter-two-character",
        "regions": [
            {
                "region_id": "left-character",
                "name": "Left character",
                "box": {"x0": 60, "y0": 35, "x1": 380, "y1": 425},
                "prompt": (
                    "exactly one adult woman, lface, in simple blue clothing, full "
                    "body, standing alone on the left"
                ),
                "priority": 10,
            },
            {
                "region_id": "right-character",
                "name": "Right character",
                "box": {"x0": 388, "y0": 35, "x1": 708, "y1": 425},
                "prompt": (
                    "exactly one distinct adult woman with short curly black hair "
                    "in simple orange clothing, full body, standing alone on the right"
                ),
                "priority": 9,
            },
        ],
        "regional_prompt_strength": 1.0,
        "regional_outside_penalty": 1.0,
        "regional_feather_pixels": 64.0,
        "adapter_routes": [
            {
                "adapter_id": "lface-adapter",
                "id": "lface-adapter",
                "name": "lface identity",
                "strength": args.adapter_strength,
                "global": False,
                "region_ids": ["left-character"],
                "trigger_phrase": "lface",
                "routing_mode": "standard",
            }
        ],
    }
    try:
        result = service.execute(
            CommandKind.GENERATE_BASELINE,
            {
                "request": request,
                "asset_paths": {"lface-adapter": str(adapter)},
            },
            cancellation=KreaCancellation(),
            progress=progress,
        )
    except CriticalGpuMemoryPressure as error:
        print(
            json.dumps(
                {
                    "kind": "error",
                    "recoverable": True,
                    "message": str(error),
                },
                indent=2,
            )
        )
        return 2
    finally:
        service.release()

    output = Path(str(result["asset_paths"][0])).resolve()
    staged = (comfyui_root / "input" / args.staged_name).resolve()
    staged.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(output, staged)
    if sha256(output) != sha256(staged):
        raise RuntimeError("staged keyframe does not match Krea output")
    print(
        json.dumps(
            {
                "output": str(output),
                "staged": str(staged),
                "bytes": output.stat().st_size,
                "sha256": sha256(output),
                "seed": args.seed,
                "steps": args.steps,
                "adapter": str(adapter),
                "adapter_sha256": sha256(adapter),
                "adapter_strength": args.adapter_strength,
                "metadata": result["metadata"],
                "warnings": result["warnings"],
            },
            indent=2,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
