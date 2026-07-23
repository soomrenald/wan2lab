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
python -m pip install -e packages/wan2core
python -m pip install -e '.[dev]'
pytest
```

Model weights are external assets and are never included in this repository.

