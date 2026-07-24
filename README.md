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
