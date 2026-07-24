#!/usr/bin/env python3
"""Exercise mannequin guide rendering and the capability-gated Krea i2i fallback."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from k2core.worker.protocol import CommandKind
from k2core.worker.runtime import CriticalGpuMemoryPressure
from wan2core.mannequin.workflows import (
    ConditioningPath,
    GuideKind,
    KreaMannequinCapabilities,
    default_mannequin_scene,
    plan_krea_conditioning,
)
from wan2lab.krea_worker import KreaCancellation, KreaWorkerService
from wan2lab.mannequin import render_mannequin_guides


TRANSFORMER_FILENAME = "krea2_turbo_fp8_scaled.safetensors"
TEXT_ENCODER_FILENAME = "qwen3vl_4b_fp8_scaled.safetensors"
VAE_FILENAME = "qwen_image_vae.safetensors"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--comfyui-root", type=Path, default=Path("~/ComfyUI"))
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("~/.cache/wan2lab/krea-mannequin-fallback"),
    )
    parser.add_argument("--width", type=int, default=832)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--steps", type=int, default=4)
    parser.add_argument("--edit-strength", type=float, default=0.45)
    parser.add_argument("--seed", type=int, default=20260802)
    parser.add_argument(
        "--prompt",
        default=(
            "Create a clean cinematic studio photograph of a single articulated "
            "wooden artist mannequin. Preserve the exact full-body pose, camera, "
            "framing, and proportions from the guide. Warm neutral background, "
            "soft key light, detailed wood grain, no extra people or limbs."
        ),
    )
    args = parser.parse_args()
    if args.width <= 0 or args.height <= 0:
        parser.error("--width and --height must be positive")
    if not 1 <= args.steps <= 100:
        parser.error("--steps must be between 1 and 100")
    if not 0.0 < args.edit_strength <= 1.0:
        parser.error("--edit-strength must be in (0, 1]")
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
    output_root = args.output_root.expanduser().resolve()
    guide_root = output_root / "guides"
    scene = default_mannequin_scene(
        scene_id="hardware-mannequin",
        name="Hardware acceptance mannequin",
        width=args.width,
        height=args.height,
    )
    guides = render_mannequin_guides(scene, guide_root)
    guide_paths = {guide.kind: guide.path.resolve() for guide in guides}
    guide_asset_ids = {
        kind: f"hardware-mannequin-{kind.value}" for kind in guide_paths
    }

    service = KreaWorkerService(output_root / "results")
    probe = service.probe({"comfyui_root": str(comfyui_root)})
    depth_models = tuple(
        str(item) for item in probe.get("depth_control_model_ids", ())
    )
    plan = plan_krea_conditioning(
        scene=scene,
        capabilities=KreaMannequinCapabilities(
            depth_control_model_ids=depth_models,
            supports_i2i=bool(probe.get("comfyui_krea_support", False)),
        ),
        guide_assets=guide_asset_ids,
        fallback_edit_strength=args.edit_strength,
    )
    if plan.path is not ConditioningPath.I2I_SCAFFOLD:
        raise RuntimeError(
            "this runner verifies the fallback path, but depth control was advertised"
        )

    model_root = comfyui_root / "models"
    load_result = service.load(
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

    try:
        result = service.execute(
            CommandKind.EDIT_IMAGE,
            {
                "request": {
                    "operation": "edit_image",
                    "source_asset_id": plan.guide_asset_id,
                    "prompt": args.prompt,
                    "steps": args.steps,
                    "seed": args.seed,
                    "edit_strength": plan.edit_strength,
                    "settings": {"preserve_identity": False},
                },
                "asset_paths": {
                    plan.guide_asset_id: str(guide_paths[GuideKind.SHADED])
                },
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
                    "recovery_actions": [
                        "reduce_resolution",
                        "enable_cpu_vae",
                        "release_other_models",
                    ],
                },
                indent=2,
            )
        )
        return 2
    finally:
        service.release()

    output = Path(str(result["asset_paths"][0])).resolve()
    print(
        json.dumps(
            {
                "conditioning_plan": plan.model_dump(mode="json"),
                "runtime_probe": probe,
                "load": load_result,
                "guides": {
                    kind.value: {
                        "path": str(path),
                        "bytes": path.stat().st_size,
                        "sha256": sha256(path),
                    }
                    for kind, path in guide_paths.items()
                },
                "output": {
                    "path": str(output),
                    "bytes": output.stat().st_size,
                    "sha256": sha256(output),
                },
                "seed": args.seed,
                "steps": args.steps,
                "edit_strength": args.edit_strength,
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
