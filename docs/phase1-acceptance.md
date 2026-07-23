# Product Phase 1 acceptance status

Status recorded on 2026-07-22.

## Automated acceptance

The local, GPU-independent Phase 1 suite passes with:

```bash
QT_QPA_PLATFORM=offscreen \
PYTHONPATH=apps/desktop/src:packages/wan2core/src:../k2core/src \
python -m pytest -q
```

The suite covers the canonical project codec and generated contracts, immutable
assets and revisions, identity/appearance/sheet workflows, compatible adapter
routing, regional keyframes, mannequin guides, timeline planning, every Wan
request mode, review gates, regeneration ancestry, single/batch/face edits,
invalidation, model residency and OOM recovery, FPS conversion, FFmpeg planning
and execution, the packaged desktop wheel, and offscreen QML launch.

`test_phase1_acceptance.py` additionally runs the integrated deterministic
acceptance fixture: an 18-second timeline with three-second approved anchors,
six sequential segments, a rejected and regenerated child revision, mandatory
approval of every current revision, final export planning, and export blocking
after a segment becomes stale.

## Local host checks

- FFmpeg 7.1.4 is installed and the media execution tests pass.
- Krea model, text-encoder, VAE, and adapter files are available under the local
  ComfyUI model store.
- No compatible Wan diffusion model, Wan VAE, or Wan text encoder is currently
  installed in that store.
- ComfyUI was not listening on `127.0.0.1:8188` during this check.

## Remaining hardware acceptance gate

Real CUDA/ROCm generation cannot be truthfully accepted until a supported Wan
model family and its required VAE/text encoder are installed and ComfyUI is
running with the required Wan wrapper nodes. Once available, manually verify
Prompt, I2V, first/last, Animate, Replace, retained residency, explicit release,
OOM recovery, mannequin/Krea-to-Wan handoff, identity correction, batch face
repair, long continuation, and final export. Visual quality remains a human
review decision and is never inferred from file existence.

Product Phase 2 must not start until this hardware gate is deliberately accepted
or the project owner explicitly records a waiver.
