#!/usr/bin/env python3
"""Refine one project-owner-confirmed face region through the typed batch path."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from k2core.worker.protocol import CommandKind
from k2core.worker.runtime import CriticalGpuMemoryPressure
from PIL import Image
from wan2core.editing import BatchFrameSelection, FrameEditOperation
from wan2core.editing.faces import (
    FaceProposal,
    FaceRefinementBatchPlan,
    confirm_face_proposal,
)
from wan2core.editing.workflows import BatchFrameEditPlan, NormalizedFrameEditRequest
from wan2core.keyframes import AdapterSelection, Rectangle
from wan2core.keyframes.composition import KreaAdapterRouteSpec
from wan2lab.krea_worker import KreaCancellation, KreaWorkerService


TRANSFORMER_FILENAME = "krea2_turbo_fp8_scaled.safetensors"
TEXT_ENCODER_FILENAME = "qwen3vl_4b_fp8_scaled.safetensors"
VAE_FILENAME = "qwen_image_vae.safetensors"
CONFIRMED_BOX = Rectangle(
    x0=511.5229,
    y0=113.5447,
    x1=700.8113,
    y1=308.3947,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_image", type=Path)
    parser.add_argument(
        "--adapter",
        required=True,
        type=Path,
        help="Compatible Krea identity LoRA/LoKr associated with the confirmed character",
    )
    parser.add_argument(
        "--adapter-trigger",
        required=True,
        help="Identity trigger text declared by the selected character adapter",
    )
    parser.add_argument("--adapter-strength", type=float, default=1.0)
    parser.add_argument("--comfyui-root", type=Path, default=Path("~/ComfyUI"))
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("~/.cache/wan2lab/krea-face-refinement"),
    )
    parser.add_argument("--steps", type=int, default=4)
    parser.add_argument("--denoise", type=float, default=0.12)
    parser.add_argument("--seed", type=int, default=20260807)
    parser.add_argument(
        "--prompt",
        default=(
            "Preserve the exact same character identity, face shape, hairstyle, "
            "expression, camera, lighting, clothing, and background. Refine only "
            "natural facial detail inside the confirmed face region, with clear "
            "eyes and coherent features."
        ),
    )
    args = parser.parse_args()
    if not 1 <= args.steps <= 100:
        parser.error("--steps must be between 1 and 100")
    if not 0.0 < args.denoise <= 1.0:
        parser.error("--denoise must be in (0, 1]")
    if not -10.0 <= args.adapter_strength <= 10.0:
        parser.error("--adapter-strength must be between -10 and 10")
    return args


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    args = parse_args()
    source = args.source_image.expanduser().resolve()
    adapter = args.adapter.expanduser().resolve()
    comfyui_root = args.comfyui_root.expanduser().resolve()
    output_root = args.output_root.expanduser().resolve()
    if not source.is_file() or source.stat().st_size == 0:
        raise FileNotFoundError(source)
    if not adapter.is_file() or adapter.stat().st_size == 0:
        raise FileNotFoundError(adapter)
    normalized_source = output_root / "inputs" / "confirmed-source.png"
    normalized_source.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as image:
        image.convert("RGB").save(normalized_source)

    proposal = confirm_face_proposal(
        FaceProposal(
            proposal_id="official-animate-face-0",
            frame_index=0,
            identity_id="official-animate-character",
            region_id="official-animate-face-region",
            box=CONFIRMED_BOX,
            score=0.7487187,
            prompt=args.prompt,
        )
    )
    refinement = FaceRefinementBatchPlan(
        identity_id=proposal.identity_id,
        proposals=(proposal,),
    )
    request = NormalizedFrameEditRequest(
        source_frame_asset_id="official-animate-reference",
        operation_type=FrameEditOperation.FACE_REFINEMENT,
        prompt=args.prompt,
        settings={
            "seed": args.seed,
            "steps": args.steps,
            "denoise": args.denoise,
            "preserve_identity": True,
        },
        region=proposal.box,
        identity_id=proposal.identity_id,
        adapters=(
            AdapterSelection(
                adapter_id="confirmed-identity-adapter",
                strength=args.adapter_strength,
            ),
        ),
        adapter_routes=(
            KreaAdapterRouteSpec(
                route_id="confirmed-identity-adapter:confirmed-face",
                adapter_id="confirmed-identity-adapter",
                asset_id="confirmed-identity-adapter-asset",
                model_family="krea2",
                strength=args.adapter_strength,
                region_ids=("confirmed-face",),
                routing_mode="character_identity",
                trigger_phrase=args.adapter_trigger,
            ),
        ),
        user_confirmed_face_region=proposal.confirmed,
    )
    batch = BatchFrameEditPlan(
        selection=BatchFrameSelection(frame_indices=(proposal.frame_index,)),
        requests=(request,),
    )

    model_root = comfyui_root / "models"
    service = KreaWorkerService(output_root / "results")
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
            CommandKind.REFINE_FACES,
            {
                "request": batch.requests[0].to_k2_request(),
                "asset_paths": {
                    batch.requests[0].source_frame_asset_id: str(normalized_source),
                    "confirmed-identity-adapter-asset": str(adapter),
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
    evidence = {
        "source_image": str(source),
        "source_sha256": sha256(source),
        "normalized_source_image": str(normalized_source),
        "normalized_source_sha256": sha256(normalized_source),
        "identity_adapter": str(adapter),
        "identity_adapter_sha256": sha256(adapter),
        "output_image": str(output),
        "output_bytes": output.stat().st_size,
        "output_sha256": sha256(output),
        "project_owner_confirmation_date": "2026-07-24",
        "proposal": refinement.proposals[0].model_dump(mode="json"),
        "batch": batch.model_dump(mode="json"),
        "load": load_result,
        "metadata": result["metadata"],
        "warnings": result["warnings"],
    }
    face_detail = result["metadata"].get("face_detail", {})
    evidence["ok"] = (
        isinstance(face_detail, dict)
        and face_detail.get("status") == "complete"
        and face_detail.get("refined_count") == 1
    )
    output_root.mkdir(parents=True, exist_ok=True)
    evidence_path = output_root / "face-refinement-evidence.json"
    evidence_path.write_text(
        json.dumps(evidence, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({**evidence, "evidence": str(evidence_path)}, indent=2, default=str))
    return 0 if evidence["ok"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
