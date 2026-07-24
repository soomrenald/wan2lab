# Regional adapter Krea-to-Wan hardware acceptance

Status recorded on 2026-07-23.

## Result

The production Krea backend generated a two-character keyframe with one
identity LoKr routed only to the left character region. The staged PNG then
completed the production Wan2.2 TI2V-5B I2V graph with default-active
EasyCache.

This verifies the regional adapter implementation and the Krea-to-Wan asset
handoff. The short five-frame Wan output is an integration artifact, not an
identity-consistency or motion-quality approval.

## Krea keyframe

Accepted keyframe:

```text
/home/wolfhard/.cache/wan2lab/krea-adapter-keyframes/
  wan2lab-krea-adapter-two-character_20260724T030040Z_seed-20260804.png
```

- 768x432 RGB PNG.
- 300,103 bytes.
- SHA-256:
  `4565b2a215229a2195b0eae67d2a11028af8a3a9741a54e14ae2f5fdd996808e`.
- Seed: `20260804`.
- Steps: 4, Euler/simple.
- Left region: one `lface` subject in blue.
- Right region: one distinct subject with short curly black hair in orange.
- Warnings: none.

The staged ComfyUI input
`input/wan2lab/hardware/krea-adapter-two-character.png` has the same SHA-256.

The selected identity artifact is
`models/loras/krea_lface_tonly.safetensors`, SHA-256
`a28a78e1b701728fa8b088ee30c30a87608e11656ba326ebcf2cd016c184799c`,
at strength 0.7. Runtime adapter evidence:

- Format: LoKr.
- Complete/matched targets: 256/256.
- Status: `applied_regional`.
- Application mode: `unfused_region_text_image_delta_gate_v3`.
- Locally applied model targets: 200.
- Region: `left-character` only.
- Image-mask coverage: 0.3761574074.
- Observed forward calls: 800.
- Outside-gate delta RMS: 0.0.

The first prompt candidate produced three people and was rejected on semantic
inspection. The accepted retry removed all subject descriptions from the
global prompt and left subject ownership to the two regional prompts; it
contains exactly two separated, full-body subjects.

## Wan handoff

Wan output:

```text
/home/wolfhard/ComfyUI/output/wan2lab/hardware/
  krea_adapter_two_character_i2v_5f_00001.mp4
```

- Prompt ID: `29ffa6df-5625-487b-bd8e-38290581b5cc`.
- H.264 High/yuv420p, 1280x704, 24 FPS.
- Exactly five frames over 0.208333 seconds.
- 63,827 bytes.
- SHA-256:
  `654f8a83c79d9c31b824ee43209c8db8d22eb8ebae87201fd4c1342b1c61791c`.
- Full FFmpeg decode: passed without errors.
- Acceleration: EasyCache, auto/balanced, active.

The three-frame contact sheet is:

```text
/home/wolfhard/ComfyUI/output/wan2lab/hardware/
  krea_adapter_two_character_i2v_5f_contact.png
```

It is 1920x352, has SHA-256
`401f3984d8119b3d23ed65f0a10c1b7bcd43de02c7667ca0b4575e8d3dfc1923`,
and shows both subjects and their color-separated composition remaining stable
across the short clip.

## Reproduction

Generate and stage the routed Krea keyframe:

```bash
PYTHONPATH=packages/wan2core/src:apps/desktop/src:../k2core/src \
~/ComfyUI/venv_rocm7/bin/python \
  scripts/krea_adapter_keyframe_smoke.py
```

Then submit the staged image through `scripts/wan2_2_smoke.py --mode i2v`.

