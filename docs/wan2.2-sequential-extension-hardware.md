# Wan2.2 sequential extension hardware acceptance

Status recorded on 2026-07-23.

## Result

A genuine full-duration Wan2.2 I2V extension was generated from the exact last
decoded frame of the previously owner-approved five-second segment.

The extension candidate is:

```text
/home/wolfhard/ComfyUI/output/wan2lab/hardware/
  krea_to_wan2_2_i2v_121f_extension_seed20260806_00001.mp4
```

- Prompt ID: `b511528c-5ab2-4941-84ad-6591037f1d8e`.
- H.264 High/yuv420p, 1280x704, 24 FPS.
- Exactly 121 frames over 5.041667 seconds.
- 693,714 bytes.
- SHA-256:
  `335c06e717f53d26713571acf351aa51a90515b8bfdf08866afe7bdfda7dac18`.
- Full FFmpeg decode: passed without errors.
- Seed: `20260806`.
- Steps: 30, UniPC.
- ComfyUI execution time: 2:16:59.
- EasyCache: auto/balanced, active.

The continuation prompt was:

> The blue and orange wooden artist mannequins finish their friendly wave,
> slowly lower their raised hands, then turn slightly toward one another,
> subtle natural joint motion, locked camera, stable studio background.

The negative prompt was:

> flicker, distortion, warped limbs, extra limbs, camera movement, text,
> watermark

## Boundary evidence

Source segment:

```text
/home/wolfhard/ComfyUI/output/wan2lab/hardware/
  krea_to_wan2_2_i2v_121f_30step_seed20260729_00001.mp4
```

The extracted frame 120 was staged as
`input/wan2lab/hardware/approved-segment-final.png`, with SHA-256
`a6d5c3ecb2461923f66f9b844fc260010a84dfc38210ff058d1b94aefc933f4b`.
It was used as the new segment's immutable I2V start image.

The extension's first decoded frame has SHA-256
`06fa53e580d653482bdf74de397719cca8d3637e9e91a5552556d23ab6269e96`.
Its PSNR against the source boundary is 40.260881 dB. The side-by-side
comparison is:

```text
/home/wolfhard/ComfyUI/output/wan2lab/hardware/
  sequential_extension_boundary_comparison.png
```

Comparison SHA-256:
`7f2508cf1b60767f1bd896b902908d9596cf4e56fcf2f4f14f465ff6bd848593`.

## Visual-review evidence

The nine-frame contact sheet is:

```text
/home/wolfhard/ComfyUI/output/wan2lab/hardware/
  krea_to_wan2_2_i2v_121f_extension_contact.png
```

It is 960x528 with SHA-256
`38c10e720eb97fc4d60c8a6b2021454ef73714461e3a103b98345eeb5f088699`.
Inspection shows both subjects and their colors remain stable. They begin at
the approved raised-hand boundary, lower their hands, and the orange
mannequin turns toward the blue mannequin while the camera and studio remain
stable.

This is a technically accepted review candidate. Wan2Lab must not mark it
approved or assemble it into the final long video until the project owner
accepts its visual quality.

## Acceleration and resource evidence

EasyCache evaluated the residual threshold after step 10. It reused steps 12,
14, and 16, then correctly returned to full computation when later residuals
did not meet the threshold.

Raw telemetry:
`/tmp/wan2lab-sequential-extension-20260723.csv`.

- Samples: 1,652 at five-second intervals.
- Average/peak VRAM: 14,042.7 / 15,138 MiB.
- Average/peak GPU activity: 99.2% / 100%.
- Average/peak graphics clock: 3,205.0 / 3,272 MHz.

The run completed without a GPU OOM, host OOM, or storage error. Tiled VAE
decode used 128-pixel tiles with 64-pixel strides and completed 45 tiles in
5:43.

