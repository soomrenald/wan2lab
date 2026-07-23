# Local Krea-to-Wan2.2 hardware handoff

Status recorded on 2026-07-23.

## Scope and result

This test verifies the constrained-VRAM model transition and Stage A keyframe
handoff path:

1. Wan residency is explicitly released.
2. The isolated Krea runtime loads on ROCm and generates a two-subject keyframe.
3. Krea releases its in-process model state.
4. The immutable PNG is copied into ComfyUI input storage without changing its
   bytes.
5. Wan2.2 TI2V-5B reloads, scales/crops the keyframe to 1280x704, encodes it as
   a 48-channel start latent, and produces a short I2V result.
6. Wan residency is explicitly released again.

The complete path succeeded on the 16 GB AMD host. It verifies model-switch
ordering, artifact integrity, graph compatibility, and first-frame
preservation. It does not claim production motion quality or identity
consistency from a four-step, five-frame integration render.

## Pinned Krea assets

The weights remain external to this Git repository.

| Component | ComfyUI-relative path | Bytes | SHA-256 |
| --- | --- | ---: | --- |
| Transformer | `models/diffusion_models/krea2_turbo_fp8_scaled.safetensors` | 13,141,730,784 | `eb4dd8c612cfd10f64f25b057e6e6bbcb5737c94a7372177e456dbf7579502f1` |
| Text encoder | `models/text_encoders/qwen3vl_4b_fp8_scaled.safetensors` | 5,242,467,968 | `54bd5144df0bbc25dd6ccadfcb826b521445a1b06ae5a42570bdd2974ca87094` |
| VAE | `models/vae/qwen_image_vae.safetensors` | 253,806,246 | `a70580f0213e67967ee9c95f05bb400e8fb08307e017a924bf3441223e023d1f` |

The Krea hardware worker must run with the accelerator-enabled ComfyUI Python
environment. The desktop development environment on this host is CPU-only and
correctly refuses an in-process GPU load.

## Reproduce

From the Wan2Lab repository:

```bash
export PYTHONPATH=apps/desktop/src:packages/wan2core/src:/home/wolfhard/k2core/src

/home/wolfhard/ComfyUI/venv_rocm7/bin/python \
  scripts/krea_keyframe_smoke.py
```

The script uses the `safe_16gb` memory policy, CPU VAE, 768x432 output, four
steps, and seed `20260726`. Copy the printed output path into ComfyUI input
storage:

```bash
mkdir -p /home/wolfhard/ComfyUI/input/wan2lab
cp PRINTED_KREA_OUTPUT.png \
  /home/wolfhard/ComfyUI/input/wan2lab/krea-wan-handoff.png
```

Then run Wan I2V:

```bash
/home/wolfhard/krea_region_project/.venv/bin/python \
  scripts/wan2_2_smoke.py \
  --mode i2v \
  --start-image wan2lab/krea-wan-handoff.png \
  --frames 5 \
  --steps 4 \
  --seed 20260727 \
  --prompt "The blue and orange wooden mannequins gently wave while the camera remains still." \
  --output-prefix wan2lab/hardware/krea_to_wan2_2_i2v_5f \
  --release
```

## Evidence

The generated Krea keyframe:

- Path:
  `/home/wolfhard/.cache/wan2lab/krea-results/wan2lab-krea-wan-handoff_20260723T164212Z_seed-20260726.png`
- 768x432, 8-bit RGB PNG
- 299,148 bytes
- SHA-256:
  `a7731584d20f2d2f7810d8258312ee09be828a61d211b77708c2b495678cf00b`
- Content inspection confirms one blue and one orange full-body wooden
  mannequin in the requested studio composition.

The staged ComfyUI input has the same SHA-256.

The Wan handoff result:

- Prompt ID: `4c0e3fff-7c47-498d-b4d5-e4401c8dbc1d`
- Path:
  `output/wan2lab/hardware/krea_to_wan2_2_i2v_5f_00001.mp4`
- H.264/yuv420p, 1280x704, 24 FPS, five frames, 0.208333 seconds
- 64,935 bytes
- SHA-256:
  `701ec294539e6ef61ec1e6bc9c99dd2a467fbb9b46082db07a889926d14d8b26`
- ComfyUI execution time: 184.21 seconds

FFmpeg decoded every output frame without error. Inspection of the extracted
first frame confirms that both mannequins, their colors, and the studio
composition survive the expected center crop/scale and Wan latent encode.

## Thirty-step visual-review candidate

A longer candidate uses the same Krea keyframe with 17 frames and the model's
30-step default:

```bash
/home/wolfhard/krea_region_project/.venv/bin/python \
  scripts/wan2_2_smoke.py \
  --mode i2v \
  --start-image wan2lab/krea-wan-handoff.png \
  --frames 17 \
  --steps 30 \
  --seed 20260728 \
  --prompt "The blue and orange wooden artist mannequins slowly raise one hand in a friendly synchronized wave, subtle natural joint motion, locked camera, stable studio background." \
  --negative-prompt "flicker, distortion, warped limbs, extra limbs, camera movement, text, watermark" \
  --output-prefix wan2lab/hardware/krea_to_wan2_2_i2v_17f_30step_review \
  --release
```

Evidence:

- Prompt ID: `47a52813-1567-45b8-b7d4-9b21c8be2501`
- Path:
  `output/wan2lab/hardware/krea_to_wan2_2_i2v_17f_30step_review_00001.mp4`
- H.264/yuv420p, 1280x704, 24 FPS, 17 frames, 0.708333 seconds
- 146,894 bytes
- SHA-256:
  `0e5df779bc26c64e8d65169873b079281a8fd24b1cfd9decef76ff22dd8b729c`
- ComfyUI execution time: 25 minutes 37 seconds
- Sampling: 22 minutes 12 seconds, 5.975 GB peak allocated, 6.674 GB
  peak reserved
- VAE decode: 45 tiles in 2 minutes 28 seconds

FFmpeg decoded every frame without error. A five-frame contact sheet is stored
at
`/home/wolfhard/.cache/wan2lab/review/wan2lab-review-contact-sheet.png`
(1,345,109 bytes, SHA-256
`1863e8d6e7dde314a804feddc558eedcf3b10707e01e2638178884f4490e84a6`).

The contact sheet shows a stable studio and stable subject colors, with the blue
mannequin raising a hand. The orange mannequin remains mostly stationary, so
the requested synchronized action is not fully demonstrated. This is a review
candidate, not an automatic semantic or visual-quality acceptance.

## Constrained-memory behavior

An initial 1280x704 Krea attempt stopped safely after denoising step 1/4 when
K2Core measured only 1.65 GiB free. This was a deliberate
`CriticalGpuMemoryPressure` guard, not a device OOM or corrupt output. The
runtime released cleanly.

The 768x432 CPU-VAE retry completed under `safe_16gb` without OOM recovery. Its
final telemetry reported 15,619,588,096 free GPU bytes and no warnings. Wan
performed the final exact-canvas conversion as part of its model-specific I2V
graph.
