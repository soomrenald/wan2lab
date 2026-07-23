# Product Phase 1 acceptance status

Status recorded on 2026-07-23.

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
- Wan2.2 TI2V-5B FP8, its Wan2.2 BF16 VAE, and its UMT5 FP8 text encoder are
  installed and checksum-verified in the local ComfyUI model store.
- ComfyUI 0.28.0 is running on `127.0.0.1:8188` with PyTorch
  2.10.0+rocm7.1 on an AMD gfx1200 device with 16,304 MiB reported VRAM.
- Live Wan2Lab discovery identifies the accelerator as ROCm, finds every node
  required by the standard worker graph, and advertises the installed TI2V-5B
  model for Prompt and I2V only.
- The wrapper-specific TI2V-5B Prompt and I2V graph paths both completed
  five-frame, 1280x704, 24 FPS H.264 smoke renders. The runbook and immutable
  evidence are recorded in
  [wan2.2-ti2v-5b-hardware.md](wan2.2-ti2v-5b-hardware.md).
- Explicit model release succeeds through ComfyUI's `/free` endpoint and clears
  the worker's retained-model state.

## Hardware acceptance status

The first-family ROCm execution gate is partially accepted: backend discovery,
artifact selection, Prompt execution, I2V execution, output encoding, and
structured provenance are verified. The short four-step smoke renders establish
runtime integration only; they do not establish production visual quality.

Still manually verify full 121-frame production settings, retained residency,
and regeneration without unnecessary reload for TI2V-5B. First/last, Animate,
and Replace require later compatible model families because TI2V-5B does not
advertise those modes. OOM recovery, mannequin/Krea-to-Wan handoff, identity
correction, batch face repair, long continuation, and final export also remain
hardware gates.

Visual quality remains a human review decision and is never inferred from file
existence.

Product Phase 2 must not start until this hardware gate is deliberately accepted
or the project owner explicitly records a waiver.
