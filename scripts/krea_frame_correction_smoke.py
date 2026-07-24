#!/usr/bin/env python3
"""Correct one real Wan frame with Krea and reassemble its immutable revision."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from k2core.worker.protocol import CommandKind
from k2core.worker.runtime import CriticalGpuMemoryPressure
from wan2core.editing.workflows import plan_frame_extraction, plan_frame_revision_assembly
from wan2lab.krea_worker import KreaCancellation, KreaWorkerService
from wan2lab.media import execute_frame_extraction, execute_frame_revision_assembly


TRANSFORMER_FILENAME = "krea2_turbo_fp8_scaled.safetensors"
TEXT_ENCODER_FILENAME = "qwen3vl_4b_fp8_scaled.safetensors"
VAE_FILENAME = "qwen_image_vae.safetensors"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_video", type=Path)
    parser.add_argument("output_video", type=Path)
    parser.add_argument("--comfyui-root", type=Path, default=Path("~/ComfyUI"))
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("~/.cache/wan2lab/krea-frame-corrections"),
    )
    parser.add_argument(
        "--work-directory",
        type=Path,
        default=Path("~/.cache/wan2lab/krea-frame-correction-work"),
    )
    parser.add_argument("--frame-index", type=int, default=8)
    parser.add_argument("--frame-count", type=int, default=17)
    parser.add_argument("--generation-fps", type=float, default=16)
    parser.add_argument("--steps", type=int, default=4)
    parser.add_argument("--edit-strength", type=float, default=0.15)
    parser.add_argument("--seed", type=int, default=20260801)
    parser.add_argument(
        "--prompt",
        default=(
            "Preserve the exact blue and orange wooden artist mannequins, studio, "
            "lighting, camera, and composition. Repair only the crossing arms so "
            "each mannequin raises its own outer hand naturally without touching."
        ),
    )
    args = parser.parse_args()
    if not 0 <= args.frame_index < args.frame_count:
        parser.error("--frame-index must be within --frame-count")
    if args.generation_fps <= 0:
        parser.error("--generation-fps must be positive")
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
    source_video = args.source_video.expanduser().resolve()
    output_video = args.output_video.expanduser().resolve()
    comfyui_root = args.comfyui_root.expanduser().resolve()
    output_root = args.output_root.expanduser().resolve()
    work_directory = args.work_directory.expanduser().resolve()
    if not source_video.is_file() or source_video.stat().st_size == 0:
        raise FileNotFoundError(source_video)

    extracted = work_directory / f"source-frame-{args.frame_index:08d}.png"
    execute_frame_extraction(
        plan_frame_extraction(
            ffmpeg_executable="ffmpeg",
            source_video_path=str(source_video),
            frame_index=args.frame_index,
            frame_count=args.frame_count,
            output_path=str(extracted),
        ),
        cancellation=KreaCancellation(),
    )
    print(f"extracted: {extracted}", flush=True)

    model_root = comfyui_root / "models"
    service = KreaWorkerService(output_root)
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

    try:
        edit_result = service.execute(
            CommandKind.EDIT_IMAGE,
            {
                "request": {
                    "operation": "edit_image",
                    "source_asset_id": "source-frame",
                    "prompt": args.prompt,
                    "steps": args.steps,
                    "seed": args.seed,
                    "edit_strength": args.edit_strength,
                    "settings": {"preserve_identity": True},
                },
                "asset_paths": {"source-frame": str(extracted)},
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

    corrected = Path(str(edit_result["asset_paths"][0])).resolve()
    execute_frame_revision_assembly(
        plan_frame_revision_assembly(
            ffmpeg_executable="ffmpeg",
            source_video_path=str(source_video),
            replacement_paths={args.frame_index: str(corrected)},
            generation_fps=args.generation_fps,
            frame_count=args.frame_count,
            output_path=str(output_video),
            work_directory=str(work_directory / "revision"),
        ),
        cancellation=KreaCancellation(),
    )
    print(
        json.dumps(
            {
                "source_video": str(source_video),
                "source_frame": str(extracted),
                "source_frame_sha256": sha256(extracted),
                "corrected_frame": str(corrected),
                "corrected_frame_sha256": sha256(corrected),
                "output_video": str(output_video),
                "output_bytes": output_video.stat().st_size,
                "output_sha256": sha256(output_video),
                "frame_index": args.frame_index,
                "seed": args.seed,
                "steps": args.steps,
                "edit_strength": args.edit_strength,
                "metadata": edit_result["metadata"],
            },
            indent=2,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
