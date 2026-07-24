#!/usr/bin/env python3
"""Validate a standalone Wan2Lab RunPod CLI workspace and emit JSON evidence."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


EXPECTED_REVISIONS = {
    "k2core": "a82b0b32a891e19eac5c5f6e35f8a9bfb715f9dc",
    "ComfyUI": "285a98944c397a4a81f15ac63d69fa3dbc0a27b9",
    "ComfyUI/custom_nodes/ComfyUI-WanVideoWrapper": (
        "088128b224242e110d3906c6750e9a3a348a659b"
    ),
    "ComfyUI/custom_nodes/ComfyUI-VideoHelperSuite": (
        "4ee72c065db22c9d96c2427954dc69e7b908444b"
    ),
}
EXPECTED_MODELS = {
    "ComfyUI/models/diffusion_models/"
    "Wan2_2-TI2V-5B_fp8_e4m3fn_scaled_KJ.safetensors": (
        5_277_255_650,
        "a83f54a2450d5471e5721e59ab556afa2d8793e30280713e3796b254c5286b48",
    ),
    "ComfyUI/models/vae/Wan2_2_VAE_bf16.safetensors": (
        1_409_401_152,
        "0e913a2ca571c75fcb63385a8edadcca73454af5842596cb1ad11e4142590996",
    ),
    "ComfyUI/models/text_encoders/umt5-xxl-enc-fp8_e4m3fn.safetensors": (
        6_731_333_792,
        "3fe5173588270c22505d4f9158bb1644b78331b8614206a97e92760b960c9ffa",
    ),
}


@dataclass(frozen=True)
class Check:
    id: str
    ok: bool
    detail: str


def run(command: list[str], *, timeout: float = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def sha256(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def check_command(command: str) -> Check:
    resolved = shutil.which(command)
    return Check(f"command.{command}", resolved is not None, resolved or "not found")


def check_git_revision(root: Path, relative_path: str, expected: str) -> Check:
    checkout = root / relative_path
    result = run(["git", "-C", str(checkout), "rev-parse", "HEAD"])
    actual = result.stdout.strip() if result.returncode == 0 else result.stderr.strip()
    return Check(
        f"revision.{relative_path.replace('/', '.')}",
        result.returncode == 0 and actual == expected,
        f"expected={expected} actual={actual or 'unavailable'}",
    )


def check_python() -> Check:
    actual = ".".join(str(item) for item in sys.version_info[:3])
    return Check("python.version", sys.version_info[:2] == (3, 12), actual)


def check_torch() -> list[Check]:
    try:
        import torch
    except ImportError as error:
        return [Check("torch.import", False, str(error))]
    checks = [Check("torch.import", True, torch.__version__)]
    cuda_available = torch.cuda.is_available()
    checks.append(Check("torch.cuda", cuda_available, f"available={cuda_available}"))
    if cuda_available:
        device = torch.cuda.get_device_properties(0)
        checks.append(
            Check(
                "torch.device",
                True,
                f"{device.name}; vram_bytes={device.total_memory}",
            )
        )
    return checks


def check_nvidia_smi() -> Check:
    if shutil.which("nvidia-smi") is None:
        return Check("nvidia-smi", False, "not found")
    result = run(
        [
            "nvidia-smi",
            "--query-gpu=name,memory.total,driver_version",
            "--format=csv,noheader,nounits",
        ]
    )
    detail = result.stdout.strip() if result.returncode == 0 else result.stderr.strip()
    return Check("nvidia-smi", result.returncode == 0, detail)


def check_disk(root: Path, minimum_gib: int) -> Check:
    usage = shutil.disk_usage(root)
    total_gib = usage.total / 1024**3
    free_gib = usage.free / 1024**3
    return Check(
        "storage.workspace",
        total_gib >= minimum_gib,
        f"total_gib={total_gib:.1f} free_gib={free_gib:.1f} minimum_gib={minimum_gib}",
    )


def check_import(distribution: str) -> Check:
    try:
        version = importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return Check(f"package.{distribution}", False, "not installed")
    return Check(f"package.{distribution}", True, version)


def check_models(root: Path, verify_hashes: bool) -> list[Check]:
    checks = []
    for relative_path, (expected_bytes, expected_hash) in EXPECTED_MODELS.items():
        path = root / relative_path
        if not path.is_file():
            checks.append(Check(f"model.{path.name}", False, "not found"))
            continue
        actual_bytes = path.stat().st_size
        if actual_bytes != expected_bytes:
            checks.append(
                Check(
                    f"model.{path.name}",
                    False,
                    f"bytes expected={expected_bytes} actual={actual_bytes}",
                )
            )
            continue
        if verify_hashes:
            actual_hash = sha256(path)
            checks.append(
                Check(
                    f"model.{path.name}",
                    actual_hash == expected_hash,
                    f"sha256={actual_hash}",
                )
            )
        else:
            checks.append(Check(f"model.{path.name}", True, f"bytes={actual_bytes}"))
    return checks


def check_comfy(base_url: str) -> Check:
    url = f"{base_url.rstrip('/')}/object_info"
    try:
        with urllib.request.urlopen(url, timeout=15) as response:
            payload: Any = json.load(response)
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as error:
        return Check("comfy.object_info", False, str(error))
    required_nodes = {
        "LoadWanVideoT5TextEncoder",
        "VHS_VideoCombine",
        "WanVideoModelLoader",
        "WanVideoSampler",
        "WanVideoVAELoader",
    }
    missing = sorted(required_nodes.difference(payload))
    return Check(
        "comfy.object_info",
        not missing,
        "required nodes present" if not missing else f"missing={','.join(missing)}",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workspace-root",
        type=Path,
        default=Path("/workspace"),
        help="Persistent RunPod workspace root",
    )
    parser.add_argument("--minimum-workspace-gib", type=int, default=100)
    parser.add_argument(
        "--require-models",
        action="store_true",
        help="Require the three TI2V-5B model artifacts",
    )
    parser.add_argument(
        "--verify-model-hashes",
        action="store_true",
        help="Hash all model files; implies --require-models",
    )
    parser.add_argument(
        "--comfy-url",
        help="Also query a running ComfyUI server, for example http://127.0.0.1:8188",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Write the same JSON evidence to this path",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.workspace_root.resolve()
    checks = [
        check_python(),
        check_command("curl"),
        check_command("ffmpeg"),
        check_command("git"),
        check_command("tmux"),
        check_nvidia_smi(),
    ]
    checks.extend(check_torch())
    if root.is_dir():
        checks.append(check_disk(root, args.minimum_workspace_gib))
    else:
        checks.append(Check("storage.workspace", False, f"not found: {root}"))
    checks.extend(
        check_git_revision(root, relative_path, expected)
        for relative_path, expected in EXPECTED_REVISIONS.items()
    )
    checks.append(check_import("k2core"))
    checks.append(check_import("wan2core"))
    if args.require_models or args.verify_model_hashes:
        checks.extend(check_models(root, args.verify_model_hashes))
    if args.comfy_url:
        checks.append(check_comfy(args.comfy_url))

    passed = sum(item.ok for item in checks)
    evidence = {
        "ok": passed == len(checks),
        "summary": {"passed": passed, "failed": len(checks) - passed},
        "workspace_root": str(root),
        "checks": [asdict(item) for item in checks],
    }
    rendered = json.dumps(evidence, indent=2, sort_keys=True)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(f"{rendered}\n", encoding="utf-8")
    return 0 if evidence["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
