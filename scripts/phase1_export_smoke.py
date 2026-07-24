#!/usr/bin/env python3
"""Assemble two approved Wan revisions through the production export path."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from wan2core.backends import WanMode
from wan2core.export import build_export_plan
from wan2core.review import (
    approve_revision,
    complete_generation,
    queue_revision,
    start_generation,
)
from wan2core.segments import ContinuationPolicy, Segment, SegmentRequest, SegmentRevision
from wan2lab.media import execute_export_plan


class NeverCancelled:
    @property
    def cancelled(self) -> bool:
        return False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Approved 121-frame, 24 FPS source MP4")
    parser.add_argument("output", type=Path, help="Final assembled MP4")
    parser.add_argument(
        "--continuation",
        type=Path,
        help=(
            "Distinct approved 121-frame continuation; defaults to the source "
            "to retain the original isolated assembly smoke"
        ),
    )
    parser.add_argument(
        "--work-directory",
        type=Path,
        default=Path("/tmp/wan2lab-phase1-export"),
    )
    return parser.parse_args()


def approved_revision(
    *,
    number: int,
    start_ms: int,
    end_ms: int,
    start_asset_id: str,
    end_frame_asset_id: str,
    seed: int,
) -> tuple[Segment, SegmentRevision]:
    segment_id = f"phase1-export-segment-{number}"
    segment = Segment(
        segment_id=segment_id,
        start_ms=start_ms,
        end_ms=end_ms,
        mode=WanMode.I2V,
        backend_id="comfyui-wan-video-wrapper",
        model_id="wan2.2-ti2v-5b-hardware",
        continuation_policy=(
            ContinuationPolicy.AUTHORED_ANCHOR
            if number == 1
            else ContinuationPolicy.GENERATED_LAST_FRAME
        ),
    )
    request = SegmentRequest(
        request_id=f"phase1-export-request-{number}",
        segment_id=segment_id,
        mode=WanMode.I2V,
        backend_id=segment.backend_id,
        model_id=segment.model_id,
        start_ms=start_ms,
        end_ms=end_ms,
        width=1280,
        height=704,
        generation_fps=24,
        frame_count=121,
        start_image_asset_id=start_asset_id,
    )
    segment, revision = queue_revision(
        segment,
        revision_id=f"phase1-export-revision-{number}",
        request=request,
        seed=seed,
    )
    segment, revision = start_generation(segment, revision)
    segment, revision = complete_generation(
        segment,
        revision,
        result_asset_id=f"phase1-export-video-{number}",
        start_frame_asset_id=start_asset_id,
        end_frame_asset_id=end_frame_asset_id,
        provenance_id=f"phase1-export-generation-provenance-{number}",
    )
    return approve_revision(segment, revision)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    args = parse_args()
    source = args.source.resolve()
    continuation = (
        args.continuation.resolve() if args.continuation is not None else source
    )
    output = args.output.resolve()
    work_directory = args.work_directory.resolve()
    if not source.is_file() or source.stat().st_size == 0:
        raise FileNotFoundError(source)
    if not continuation.is_file() or continuation.stat().st_size == 0:
        raise FileNotFoundError(continuation)

    first, first_revision = approved_revision(
        number=1,
        start_ms=0,
        end_ms=5_000,
        start_asset_id="phase1-authored-anchor",
        end_frame_asset_id="phase1-shared-boundary",
        seed=20260729,
    )
    second, second_revision = approved_revision(
        number=2,
        start_ms=5_000,
        end_ms=10_000,
        start_asset_id="phase1-shared-boundary",
        end_frame_asset_id="phase1-final-boundary",
        seed=20260806,
    )
    plan = build_export_plan(
        export_id="phase1-hardware-export",
        segments=(first, second),
        revisions=(first_revision, second_revision),
        source_paths={
            "phase1-export-video-1": str(source),
            "phase1-export-video-2": str(continuation),
        },
        output_path=str(output),
        output_fps=24,
        ffmpeg_executable="ffmpeg",
        work_directory=str(work_directory),
        provenance_id="phase1-hardware-export-provenance",
    )

    stages: list[dict[str, object]] = []

    def progress(stage: str, current: int, total: int) -> None:
        stages.append({"stage": stage, "current": current, "total": total})
        print(f"{stage}: {current}/{total}", flush=True)

    result = execute_export_plan(
        plan,
        cancellation=NeverCancelled(),
        progress=progress,
    )
    print(
        json.dumps(
            {
                "output": str(result),
                "bytes": result.stat().st_size,
                "sha256": sha256(result),
                "sources": [
                    {
                        "path": str(source),
                        "sha256": sha256(source),
                        "seed": first_revision.seed,
                    },
                    {
                        "path": str(continuation),
                        "sha256": sha256(continuation),
                        "seed": second_revision.seed,
                    },
                ],
                "output_fps": plan.output_fps,
                "segment_count": len(plan.segment_inputs),
                "drop_leading_boundary": [
                    item.drop_leading_boundary_frame for item in plan.segment_inputs
                ],
                "fps_output_frames": [
                    item.output_frame_count for item in plan.fps_plans
                ],
                "stages": stages,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
