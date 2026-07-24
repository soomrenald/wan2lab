# Wan2.2 Replace hardware acceptance

Status recorded on 2026-07-23.

## Result

The versioned `wan2lab-wan2.2-replace` production pipeline completed on the
local 16 GB AMD/ROCm host using the pinned official Wan2.2 replacement inputs.

The output is:

```text
/home/wolfhard/ComfyUI/output/wan2lab/hardware/
  wan2_2_replace_17f_20step_00001-audio.mp4
```

- Prompt ID: `8c0bc5c5-af74-469c-8caa-cb29e9834372`.
- H.264 High/yuv420p video, 832x480, 16 FPS.
- Exactly 17 video frames over 1.0625 seconds.
- AAC-LC mono audio at 16 kHz.
- 194,425 bytes.
- SHA-256:
  `d2bc8ea07b76e2a226fa6dc0a24ac422631aa8bf4f9febdbeb932a4373a44a35`.
- Complete FFmpeg audio/video decode: passed without errors.
- ComfyUI execution time: 289.39 seconds.

The four-frame contact sheet is:

```text
/home/wolfhard/ComfyUI/output/wan2lab/hardware/
  wan2_2_replace_17f_20step_contact.png
```

It is 1664x240 with SHA-256
`7ba5ce39696feb20f0956b9741dea14e0de3f4a3d79e812425aeadc0ba77bc45`.
Inspection shows the replacement character following the source motion while
the market stall, awning, background figures, and camera remain coherent.
Fine facial detail remains a human visual-quality decision.

## Replacement path

The production graph completed each additional replacement stage:

1. SAM2 loaded on PyTorch ROCm.
2. The source-person mask propagated through all 17 source frames.
3. The mask was expanded and aligned to latent blocks.
4. The masked source background and reference character were encoded.
5. Wan Animate sampled the replacement with the same safe residency and
   EasyCache policy as Animate.
6. The preserved source audio was muxed into the output.

Runtime settings:

- Steps: 20.
- Scheduler: DPM++ SDE.
- CFG/shift: 1.0/5.0.
- Pose and face strength: 1.0/1.0.
- ONNX pose/detection: CPUExecutionProvider.
- SAM2: `sam2.1_hiera_base_plus.safetensors`.
- Transformer block swap: 25 of 40 blocks, blocking transfer.
- VAE decode: 128-pixel tiles with 64-pixel strides.
- Acceleration: EasyCache, auto/balanced, active.

## Resource evidence

Raw telemetry:
`/tmp/wan2lab-replace-20260723.csv`.

- Samples: 304 one-second samples.
- Average/peak VRAM: 10,236.1 / 15,734 MiB.
- Average/peak GPU activity: 81.1% / 100%.
- Average/peak graphics clock: 2,459.9 / 3,219 MHz.

The run completed without a device OOM or host OOM.

## Reproduction

```bash
PYTHONPATH=packages/wan2core/src:apps/desktop/src:../k2core/src \
python scripts/wan2_2_animate_smoke.py --mode replace --release
```

