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
- Two sequential Prompt jobs completed through one worker selection without an
  intervening release; the second reused ComfyUI's cached T5/model work and
  produced a distinct revision from its incremented seed.
- A default-tile 121-frame decode produced a real HIP OOM; Wan2Lab returned a
  recoverable structured error, released model state, and left ComfyUI healthy.
- Live discovery now selects 128-pixel VAE tiles and 64-pixel strides for this
  constrained-VRAM model/host combination. The recovered full-duration run
  produced a valid 121-frame, 1280x704, 24 FPS, 5.041667-second H.264 file.
- A local Krea two-subject keyframe completed under the safe 16 GB policy, was
  released and staged byte-for-byte, then produced a valid Wan2.2 I2V result
  whose first frame preserves both subjects. Exact evidence is recorded in
  [krea-to-wan2.2-hardware.md](krea-to-wan2.2-hardware.md).
- A 17-frame Krea-conditioned I2V candidate completed at the model's 30-step
  default. Its codec, dimensions, frame count, duration, full decode, resource
  peaks, and contact sheet are verified; semantic/visual acceptance remains a
  human decision.
- A different-seed, full-duration Krea-conditioned I2V candidate completed at
  30 steps. It is H.264/yuv420p at 1280x704 and 24 FPS, contains exactly 121
  frames over 5.041667 seconds, and passed a full FFmpeg decode. Both
  mannequins raise one hand in the sampled contact sheet. The project owner
  approved this first-family hardware output on 2026-07-23.
- Accelerated Wan is now a primary project and per-segment control. It defaults
  to enabled/auto/balanced, resolves only backend-declared installed compatible
  methods, and explicitly records base-inference fallback when none is
  executable.
- The installed EasyCache, MagCache, and TeaCache node schemas are discovered
  and validated. A 17-frame, 30-step EasyCache I2V hardware run completed
  13.5% faster end-to-end and 15.8% faster in sampling than the comparable
  base candidate, with complete typed acceleration provenance and a valid
  fully decoded MP4. Evidence is recorded in
  [wan-cache-acceleration.md](wan-cache-acceleration.md).
- Versioned model-to-GPU guidance is available for TI2V-5B, general 14B,
  Animate/Replace 14B, and benchmark-justified minimum-latency 14B workloads.
- Wan2.1 FLF2V-14B completed a distinct-family, accelerated first/last-frame
  run at 832x480, 17 frames, and 30 steps. The H.264 output has exactly 17
  decoded frames, passed a complete FFmpeg decode, and records model,
  component, acceleration, timing, and resource provenance. Exact evidence is
  recorded in [wan2.1-flf2v-hardware.md](wan2.1-flf2v-hardware.md).
- Live ComfyUI now exposes the installed Wan Animate/Replace preprocessing and
  SAM2 nodes. The AMD host uses a restart-stable CPU ONNX Runtime override for
  pose/detection while diffusion and SAM2 remain on PyTorch ROCm.
- Two real approved Wan revisions completed the production FPS-normalization,
  boundary-frame de-duplication, manifest, and FFmpeg export path. The recovered
  stream-copy timestamp defect is regression-tested; the accepted output is
  exactly 240 decoded frames over 10.000000 seconds and fully decodes without
  warnings. Evidence is recorded in
  [phase1-export-hardware.md](phase1-export-hardware.md).
- A real FLF middle frame completed production extraction, conservative
  identity-preserving Krea edit, immutable replacement, and one-pass revision
  reassembly. The corrected 17-frame video passed exact media inspection and a
  complete FFmpeg decode. Technical evidence and the still-pending semantic
  correction decision are recorded in
  [krea-frame-correction-hardware.md](krea-frame-correction-hardware.md).
- Production face detection returned one typed candidate from the official
  synthetic Animate reference using the installed RetinaFace model and CPU
  ONNX provider. Detection passes, but refinement is correctly paused before
  the required user confirmation. Evidence is recorded in
  [krea-face-detection-hardware.md](krea-face-detection-hardware.md).
- The production mannequin renderer created shaded, silhouette, and normalized
  depth guides. Because this Krea runtime advertises no compatible depth-control
  model, the capability resolver correctly selected the shaded i2i scaffold and
  completed a real ROCm Krea edit without warnings. Exact pose fidelity remains
  a visual decision. Evidence is recorded in
  [krea-mannequin-fallback-hardware.md](krea-mannequin-fallback-hardware.md).
- A Krea identity LoKr matched all 256 model targets and was applied only to
  the left of two character regions with zero measured outside-gate delta. The
  accepted two-subject PNG was staged byte-for-byte and completed an accelerated
  Wan2.2 I2V handoff whose five frames fully decode. Evidence is recorded in
  [krea-adapter-handoff-hardware.md](krea-adapter-handoff-hardware.md).
- The pinned Wan2.2 Animate transformer passed three complete SHA-256 reads.
  CPU ONNX pose/face preprocessing, reference CLIP conditioning, safe 25/40
  block swapping, EasyCache sampling, tiled VAE decode, and audio muxing
  completed on ROCm. The 17-frame result fully decodes. Evidence is recorded in
  [wan2.2-animate-hardware.md](wan2.2-animate-hardware.md).
- Wan2.2 Replace additionally completed SAM2 source-person segmentation and
  mask propagation across all 17 frames, masked background conditioning,
  reference-character replacement, and source-audio muxing. The result fully
  decodes and preserves the sampled background composition. Evidence is
  recorded in [wan2.2-replace-hardware.md](wan2.2-replace-hardware.md).
- A genuine 121-frame continuation was generated from the exact last frame of
  the owner-approved five-second segment. It fully decodes, has a 40.26 dB
  boundary match, preserves both subjects and the studio, and performs the
  prompted wave completion and turn. It remains review-gated before approval
  or long assembly. Evidence is recorded in
  [wan2.2-sequential-extension-hardware.md](wan2.2-sequential-extension-hardware.md).
- The SSH-first RunPod CUDA path passed clean-environment bootstrap, pinned
  model/hash verification, ComfyUI node verification, Prompt and I2V smoke
  execution, and an approved-workload 121-frame I2V benchmark on an RTX 5090.
  The five-second H.264 result fully decodes; the 30-step accelerated workload
  completed in 414.65 seconds and peaked at 12,153 MiB VRAM. The Pod is stopped
  with its regular volume preserved, and exact evidence is recorded in
  [runpod-rtx5090-ti2v-benchmark.md](runpod-rtx5090-ti2v-benchmark.md).

## Hardware acceptance status

The first-family ROCm execution gate is accepted: backend discovery, artifact
selection, Prompt execution, I2V execution, output encoding, structured
provenance, full-duration decoding, OOM recovery, and user visual review are
complete. The matching first-family CUDA execution gate is hardware-valid on
an RTX 5090, including a clean remote bootstrap, GPU text encoding, accelerated
full-duration execution, telemetry capture, output validation, evidence
preservation, and safe Pod stop.

First/last execution is hardware-valid on the installed Wan2.1 FLF2V family;
its visual quality decision remains pending. Animate and Replace are both
hardware-valid; their fine visual-quality decisions remain human review tasks.
The mannequin-guided i2i fallback is hardware-valid; its exact pose-fidelity
decision remains pending. Adapter-routed multi-character handoff is
hardware-valid. Identity correction approval, batch face repair, and genuinely
generated continuation approval and long assembly remain hardware gates.

The project owner approved the EasyCache hardware candidate after reviewing its
output and contact sheet on 2026-07-23.

Visual quality remains a human review decision and is never inferred from file
existence.

Product Phase 2 remains blocked by the fixed implementation sequence until all
required Product Phase 1 acceptance work is complete.
