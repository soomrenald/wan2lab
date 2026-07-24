# Wan2.2 Animate hardware acceptance

Status: hardware-valid on 2026-07-23 and project-owner approved on 2026-07-24.

## Result

The versioned `wan2lab-wan2.2-animate` production pipeline completed on the
local 16 GB AMD/ROCm host using the pinned official Wan2.2 test inputs.

The output is:

```text
/home/wolfhard/ComfyUI/output/wan2lab/hardware/
  wan2_2_animate_17f_20step_00001-audio.mp4
```

- Prompt ID: `e86d7952-b563-4504-b5cd-5ebe62ca24da`.
- H.264 High/yuv420p video, 832x480, 16 FPS.
- Exactly 17 video frames over 1.0625 seconds.
- AAC-LC stereo audio at 48 kHz.
- 248,656 bytes.
- SHA-256:
  `dfe032606c5eef8962c0db16beaf1b8ba76a39f30514c600a2a8e03fb1f02179`.
- Complete FFmpeg audio/video decode: passed without errors.
- ComfyUI execution time: 285.26 seconds.

The four-frame contact sheet is:

```text
/home/wolfhard/ComfyUI/output/wan2lab/hardware/
  wan2_2_animate_17f_20step_contact.png
```

It is 1664x240 with SHA-256
`38d88d523b41f26497117ea4b23ad970c2a443ba0ae5f995de0b776d346ee821`.
Inspection shows a coherent reference character performing distinct hand and
facial motion across the sampled frames. Fine detail and semantic quality
remain a human visual decision.

## Runtime path

- Transformer:
  `Wan2_2-Animate-14B_fp8_e4m3fn_scaled_KJ.safetensors`.
- Transformer size: 18,401,760,586 bytes.
- Transformer SHA-256:
  `2936b31473a967e7a429a6646bba60e7862d0938e178b58b2a140f391dd5b8e6`.
- Transformer integrity: two matching staging reads, one matching installed
  read, and no kernel storage errors during transfer.
- Steps: 20.
- Scheduler: DPM++ SDE.
- CFG/shift: 1.0/5.0.
- Pose and face strength: 1.0/1.0.
- ONNX preprocessing: CPUExecutionProvider.
- Transformer block swap: 25 of 40 blocks, blocking transfer.
- VAE decode: 128-pixel tiles with 64-pixel strides.
- Acceleration: EasyCache, auto/balanced, active.

Preprocessing completed for all 17 driving frames. The sampler padded its
internal sequence to 21 frames as required by the model, while the production
output correctly contains the requested 17 frames.

## Resource evidence

Raw telemetry:
`/tmp/wan2lab-animate-20260723.csv`.

- Samples: 308 one-second samples.
- Average/peak VRAM: 9,825.3 / 15,325 MiB.
- Average/peak GPU activity: 79.3% / 100%.
- Average/peak graphics clock: 2,407.7 / 3,226 MHz.

During sampling, approximately 3.1 GB of transformer parameters resided on the
GPU and 10.2 GB were swapped to CPU. The run completed without a device OOM or
host OOM.

## Reproduction

```bash
PYTHONPATH=packages/wan2core/src:apps/desktop/src:../k2core/src \
python scripts/wan2_2_animate_smoke.py --mode animate --release
```

The project owner reviewed and approved the Animate candidate on 2026-07-24.
