#!/usr/bin/env python3
"""Run production Krea face detection without implicitly approving refinement."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from k2core.worker.protocol import CommandKind
from wan2lab.krea_worker import KreaCancellation, KreaWorkerService


TRANSFORMER_FILENAME = "krea2_turbo_fp8_scaled.safetensors"
TEXT_ENCODER_FILENAME = "qwen3vl_4b_fp8_scaled.safetensors"
VAE_FILENAME = "qwen_image_vae.safetensors"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_image", type=Path)
    parser.add_argument("--comfyui-root", type=Path, default=Path("~/ComfyUI"))
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("~/.cache/wan2lab/krea-face-detection"),
    )
    parser.add_argument("--threshold", type=float, default=0.4)
    parser.add_argument("--provider", default="auto")
    args = parser.parse_args()
    if not 0.0 < args.threshold < 1.0:
        parser.error("--threshold must be in (0, 1)")
    return args


def main() -> int:
    args = parse_args()
    source = args.source_image.expanduser().resolve()
    comfyui_root = args.comfyui_root.expanduser().resolve()
    if not source.is_file() or source.stat().st_size == 0:
        raise FileNotFoundError(source)

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

    try:
        result = service.execute(
            CommandKind.DETECT_FACES,
            {
                "request": {
                    "source_asset_id": "face-source",
                    "threshold": args.threshold,
                    "provider": args.provider,
                },
                "asset_paths": {"face-source": str(source)},
            },
            cancellation=KreaCancellation(),
            progress=lambda stage, fraction, details: print(
                json.dumps(
                    {
                        "stage": stage,
                        "fraction": fraction,
                        "details": dict(details),
                    }
                ),
                flush=True,
            ),
        )
    finally:
        service.release()

    print(
        json.dumps(
            {
                "source_image": str(source),
                "threshold": args.threshold,
                "requested_provider": args.provider,
                **result,
                "refinement_approved": False,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
