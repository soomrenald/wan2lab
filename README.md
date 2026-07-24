# Wan2Lab

Wan2Lab is a review-gated local video-production studio built around Wan video
generation, K2/Krea keyframes and corrections, exact timeline planning, and
non-destructive revisions.

The repository contains two independently importable layers:

- `packages/wan2core`: UI- and provider-neutral project, timeline, review,
  editing, capability, and export behavior shared with the future RunPod product.
- `apps/desktop`: the PySide6/Qt Quick desktop presentation and local adapters.

The implementation follows `wan2lab_detailed_implementation_spec_updated.md`.
Product Phase 1 (desktop) is implemented before Product Phase 2 (RunPod).

## Development

Python 3.12 is required.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e ../k2core
python -m pip install -e '.[dev]'
pytest
```

The desktop distribution includes the matching `wan2core` package. Core-only
consumers may instead install `packages/wan2core` independently.

Model weights are external assets and are never included in this repository.

The current desktop software acceptance status and remaining hardware gates are
tracked in [docs/phase1-acceptance.md](docs/phase1-acceptance.md).

The first local hardware family, Wan2.2 TI2V-5B, has a reproducible setup and
Prompt/I2V smoke procedure in
[docs/wan2.2-ti2v-5b-hardware.md](docs/wan2.2-ti2v-5b-hardware.md).
The constrained-VRAM Krea keyframe to Wan I2V transition is recorded in
[docs/krea-to-wan2.2-hardware.md](docs/krea-to-wan2.2-hardware.md).
Installed Wan cache discovery, binding, provenance, and the first EasyCache
hardware comparison are recorded in
[docs/wan-cache-acceleration.md](docs/wan-cache-acceleration.md).
The checksum-pinned Wan2.1 first/last-frame installation, recovered host
failures, accelerated hardware run, and visual-review candidate are recorded in
[docs/wan2.1-flf2v-hardware.md](docs/wan2.1-flf2v-hardware.md).
The real approved-revision FPS, boundary, and final FFmpeg export hardware gate
is recorded in
[docs/phase1-export-hardware.md](docs/phase1-export-hardware.md).
The real Krea single-frame correction and immutable video-revision hardware
gate is recorded in
[docs/krea-frame-correction-hardware.md](docs/krea-frame-correction-hardware.md).
The production face-detection result and its required pre-refinement approval
boundary are recorded in
[docs/krea-face-detection-hardware.md](docs/krea-face-detection-hardware.md).
The capability-gated mannequin guide to Krea i2i fallback hardware result is
recorded in
[docs/krea-mannequin-fallback-hardware.md](docs/krea-mannequin-fallback-hardware.md).
The region-routed identity LoKr keyframe and accelerated Wan I2V handoff are
recorded in
[docs/krea-adapter-handoff-hardware.md](docs/krea-adapter-handoff-hardware.md).
Pinned Wan2.2 Animate/Replace nodes, preprocessors, artifacts, official test
inputs, and executable pipeline contracts are recorded in
[docs/wan2.2-animate-installation.md](docs/wan2.2-animate-installation.md).
The completed Wan2.2 Animate ROCm hardware run is recorded in
[docs/wan2.2-animate-hardware.md](docs/wan2.2-animate-hardware.md).
The completed SAM2-conditioned Wan2.2 Replace ROCm hardware run is recorded in
[docs/wan2.2-replace-hardware.md](docs/wan2.2-replace-hardware.md).
