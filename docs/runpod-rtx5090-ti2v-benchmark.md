# RunPod RTX 5090 Wan2.2 TI2V-5B benchmark

Status: passed and project-owner approved on 2026-07-24. The Pod is stopped and
its regular volume is preserved for restart.

This is the first SSH-first CUDA validation described in
`docs/runpod-cli-lab.md`. It proves that a clean RunPod reservation can install
the pinned Wan2Lab runtime, validate the first Wan family, and produce a
reviewable five-second I2V result without depending on the unfinished browser
application.

## Reservation and runtime

| Item | Value |
| --- | --- |
| GPU | NVIDIA GeForce RTX 5090, 32 GB |
| RunPod tier | Secure Cloud |
| GPU price at reservation | $0.99/hour |
| Template | `runpod-torch-v280` |
| Image | `runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404` |
| Pod storage | 100 GiB regular volume at `/workspace` |
| Container storage | 40 GiB |
| CPU / RAM | 16 vCPU / 125 GB |
| Python / PyTorch | 3.12.3 / 2.8.0+cu128 |
| NVIDIA driver | 570.195.03 |
| Wan2Lab | `ae9fa740f344543ecb65edc7cd8644568b1a22d0` |
| `k2core` | `a82b0b32a891e19eac5c5f6e35f8a9bfb715f9dc` |
| ComfyUI | `285a98944c397a4a81f15ac63d69fa3dbc0a27b9` |
| WanVideoWrapper | `088128b224242e110d3906c6750e9a3a348a659b` |
| VideoHelperSuite | `4ee72c065db22c9d96c2427954dc69e7b908444b` |

The initial reservation ran for approximately 40.5 minutes before it was
stopped, an upper-bound GPU charge estimate of $0.67 at the quoted hourly rate.
The later persistence test ran for approximately 5.5 minutes, adding about
$0.09. Regular volume storage continues to exist while the Pod is stopped.

The direct container-image reservation path did not become SSH-ready. Replacing
it with RunPod's official PyTorch template produced a healthy Pod; the failed
reservation had zero uptime and was removed without data loss.

## Environment gates

All guarded verification stages passed:

| Gate | Result |
| --- | --- |
| Base runtime | 16/16 |
| Model files and SHA-256 checks | 19/19 |
| Running ComfyUI and required nodes | 20/20 |

The pinned TI2V-5B transformer, VAE, and text encoder occupied approximately
13.4 GB. ComfyUI used standard PyTorch attention with DynamicVRAM enabled.
SageAttention was not installed. The image accepted the workload without
offloading the diffusion model.

## Smoke comparison

A five-frame Prompt smoke with CPU text encoding completed in 108.42 seconds.
The corresponding I2V smoke with GPU text encoding completed in 20.87 seconds.
This validates the `--text-encoder-device gpu` fast path on the 32 GB card.

EasyCache was requested for both runs, but a four-step smoke is too short to
reach its default cache start step. These runs prove execution and output
validity, not cache speedup.

## Approved full-duration workload

The benchmark used the approved Krea-to-Wan mannequin handoff image.

```text
Prompt:
The blue and orange wooden artist mannequins slowly raise one hand in a
friendly synchronized wave, subtle natural joint motion, locked camera,
stable studio background.

Negative:
flicker, distortion, warped limbs, extra limbs, camera movement, text,
watermark
```

| Parameter | Value |
| --- | --- |
| Mode | I2V |
| Seed | `20260729` |
| Frames | 121 |
| Steps | 30 |
| Resolution | 1280x704 |
| Frame rate | 24 fps |
| Text encoder | GPU |
| Acceleration | automatic balanced EasyCache |
| VAE tiles | default 272x144 |

The workload completed successfully:

| Measurement | Result |
| --- | --- |
| ComfyUI execution time | 414.65 s (6m54.65s) |
| End-to-end wall time | 417 s |
| Average GPU utilization | 76.74% |
| Peak GPU utilization | 100% |
| Average VRAM use | 10,950 MiB |
| Peak VRAM use | 12,153 MiB |
| Average board power | 450.74 W |
| Peak board power | 586.03 W |
| Encoded duration | 5.041667 s |
| Encoded frames | 121 |
| Video codec | H.264 |
| Output size | 791,010 bytes |
| Output SHA-256 | `3424a56c29a1532025068f3bcfcbd491652a129aa437e704586371c575f78ebd` |

The previous local end-to-end run took 2h42m22s, so this complete remote stack
was approximately 23.5 times faster. This is not an isolated GPU comparison:
the remote run also used GPU text encoding, automatic EasyCache, and different
VAE tiling.

Full FFmpeg decoding completed without errors. Contact-sheet review showed
both mannequins raising their hands, a stable studio background and camera, and
no gross temporal corruption. The project owner reviewed the locally preserved
output and approved it on 2026-07-24. The result therefore passes both the
technical and visual hardware gates.

## Preserved evidence

The output and machine-readable evidence were copied off the Pod before it was
stopped:

```text
/home/wolfhard/wan2lab_outputs/runpod-rtx5090-20260724/
```

That directory contains the MP4, contact sheet, two-second telemetry samples,
wall timer, exact runtime revisions, all verifier reports, and the filtered
ComfyUI history record. The stopped Pod retains the original copies under
`/workspace`.

## Stop/start durability

The original Pod restarted successfully on 2026-07-24 after its host regained
capacity. The post-restart verifier passed 20/20 checks:

- all pinned repository revisions remained exact;
- the Python, PyTorch, CUDA, driver and RTX 5090 runtime remained healthy;
- all three model files retained their exact accepted SHA-256 values;
- the prior full benchmark retained SHA-256
  `3424a56c29a1532025068f3bcfcbd491652a129aa437e704586371c575f78ebd`.

ComfyUI then restarted from the persistent workspace and generated a distinct
five-frame I2V result from the preserved conditioning asset:

| Item | Value |
| --- | --- |
| Seed | `20260730` |
| Prompt ID | `2ecdf941-720a-433c-a640-4c1b9f34c46b` |
| Text encoder | GPU |
| Acceleration | EasyCache auto/balanced, active |
| Output | `restart_i2v_5f_00001.mp4` |
| Media | H.264/yuv420p, 1280x704, 24 FPS, 5 frames |
| Duration | 0.208333 seconds |
| Bytes | 65,713 |
| SHA-256 | `6159bda69b396f533cbac66ceb2a7c4f4b1b030e504fd74c4a7f24d0c9aa590d` |

The MP4 passed a complete FFmpeg decode. The output, exact ComfyUI history and
20-check restart report were copied into the local evidence directory before
the Pod was stopped again. This passes remote asset persistence, reconnect,
model persistence, service restart, durable continuation and safe re-stop.

## Result and next comparison

The SSH/CLI fast track and stop/start lifecycle gate are complete for RTX 5090
and Wan2.2 TI2V-5B. The RTX 5090 is now the measured speed-first default for
this family. An RTX 6000 Ada 48 GB run remains useful as a value/VRAM
comparison, but it should reuse a deliberately portable model cache or budget
a second model transfer before reservation; the current regular volume is tied
to this stopped Pod.
