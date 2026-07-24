# Wan cache-acceleration hardware record

Status recorded on 2026-07-23.

## Implemented behavior

Wan2Lab discovers and schema-validates the installed Wan wrapper's EasyCache,
MagCache, and TeaCache nodes. The backend advertises them only for compatible
Prompt, I2V, and first/last modes. Animate and Replace remain excluded unless a
specialized workflow explicitly declares a compatible acceleration binding.

Project acceleration defaults to enabled, automatic selection, and the
balanced profile. On this host, automatic selection resolves TI2V-5B Prompt
and I2V to `comfy-wan-easycache`. The generated graph binds
`WanVideoEasyCache` directly to `WanVideoSampler.cache_args`.

If no compatible node or artifact is installed, the typed result explicitly
records base-inference fallback. The UI never claims that acceleration is
active in that case.

## Hardware comparison

The accelerated comparison uses the same Krea keyframe, prompt, negative
prompt, model/components, resolution, frame count, steps, scheduler, precision,
offload, and VAE tile settings as the earlier 17-frame base candidate. It uses
a new seed, `20260730`.

```bash
PYTHONPATH=apps/desktop/src:packages/wan2core/src:/home/wolfhard/k2core/src \
/home/wolfhard/krea_region_project/.venv/bin/python \
  scripts/wan2_2_smoke.py \
  --mode i2v \
  --start-image wan2lab/krea-wan-handoff.png \
  --frames 17 \
  --steps 30 \
  --seed 20260730 \
  --prompt "The blue and orange wooden artist mannequins slowly raise one hand in a friendly synchronized wave, subtle natural joint motion, locked camera, stable studio background." \
  --negative-prompt "flicker, distortion, warped limbs, extra limbs, camera movement, text, watermark" \
  --output-prefix wan2lab/hardware/krea_to_wan2_2_i2v_17f_30step_easycache \
  --release
```

| Measurement | Base inference | EasyCache balanced | Change |
| --- | ---: | ---: | ---: |
| End-to-end ComfyUI time | 25:37 | 22:10 | 13.5% faster |
| Sampling | 22:12 | 18:42 | 15.8% faster |
| VAE decode | 2:28 | 2:28 | unchanged |
| Peak sampler allocation | 5.975 GB | 6.075 GB | +0.100 GB |
| Peak sampler reservation | 6.674 GB | 6.674 GB | unchanged |

EasyCache used a threshold of `0.015`, began after step 10, ended at the final
step, and stored cache state on the offload device. The method was
conservative: it skipped compatible work in the middle of sampling and
recomputed later steps when the latent changed.

## Output evidence

- Prompt ID: `339a3ee6-15ab-47a0-8eea-1704dfffea25`
- Path:
  `/home/wolfhard/ComfyUI/output/wan2lab/hardware/krea_to_wan2_2_i2v_17f_30step_easycache_00001.mp4`
- H.264/yuv420p, 1280x704, 24 FPS, 17 frames, 0.708333 seconds
- 148,825 bytes
- SHA-256:
  `82233394d283faa875de5002b5768d5f15f7e3d60d05dfeb99396a8bb2964eb1`
- Full FFmpeg decode completed without error.

The five-frame contact sheet is stored at
`/home/wolfhard/.cache/wan2lab/review/wan2lab-easycache-17f-contact-sheet.png`
(386,349 bytes, SHA-256
`4e5df21a928347214f292da0e557b6fb6e05f8888db634035fce8c4f2bd6c55a`).

The sampled frames retain the stable studio, subject colors, and limb
structure. The blue mannequin raises one hand while the orange mannequin
remains mostly stationary. No gross cache-specific visual corruption is
visible in the sampled frames. Semantic and visual approval remains a human
review decision.

## Provenance result

The worker result records:

- Requested acceleration: enabled, automatic, balanced.
- Active method: `comfy-wan-easycache`.
- Kind: `cache`.
- Resolved cache parameters and schedule description.
- Model, VAE, text encoder, precision, quantization, offload, accelerator, and
  device details.

This record is suitable as local ROCm evidence. It is not a RunPod GPU cost
benchmark; future per-output cost estimates require an exact GPU SKU and
matching immutable runtime evidence.
