# Krea single-frame correction hardware acceptance

Status: hardware-valid on 2026-07-23 and project-owner approved on 2026-07-24.

## Scope

This gate exercises the production frame extraction, shared Krea image-edit
backend, identity-preserving edit controls, immutable replacement, and one-pass
video reassembly paths against the accepted Wan2.1 FLF result.

The committed runner is `scripts/krea_frame_correction_smoke.py`. It uses
ComfyUI's ROCm Python environment, the safe 16 GB Krea memory policy, CPU VAE,
four Euler/simple edit steps, denoise 0.15, and seed `20260801`.

Prompt:

> Preserve the exact blue and orange wooden artist mannequins, studio, lighting, camera, and composition. Repair only the crossing arms so each mannequin raises its own outer hand naturally without touching.

## Immutable inputs and outputs

| Asset | Bytes | SHA-256 |
| --- | ---: | --- |
| Source FLF video | 118,836 | `35bac8c22b40a854e2180f05060c47a69564dfd5875a27e716cba605b22fa873` |
| Extracted frame 8 | recorded in Krea work storage | `e7babee00bbf2855562a5274c16b963abeaa5eb2a3a46d926f7175045855c85c` |
| Corrected frame 8 | recorded in Krea result storage | `6c2445241fcfad4187a621df3c9b73cbbd8b06bb884c80b38bc063644e0140e9` |
| Corrected video revision | 82,633 | `9f294352f68319e631f1b53a1aab4b2a49f4194122f52d8ff021c34ab281db09` |

Video result:

`output/wan2lab/hardware/wan2_1_flf2v_17f_30step_frame8_corrected.mp4`

The corrected revision is H.264 High/yuv420p at 832x480 and 16 FPS. It
contains exactly 17 container and decoded frames over 1.062500 seconds.
A complete FFmpeg decode to a null sink completed without warnings or errors.
Only frame index 8 was replaced; the revision assembler encoded the immutable
frame sequence once.

The comparison image is
`output/wan2lab/hardware/wan2_1_flf2v_frame8_original_corrected.png`
(1,664x480, SHA-256
`d8bae129727d66b862e4d48e27e71ea993bb2cac56b5a613a253f35a8e13a192`).
The original/corrected frame pair has normalized RMSE 0.0415064.

## Resource evidence

The complete telemetry window, including extraction, the rejected
non-accelerated-Python launch, corrected ROCm launch, model load, edit,
reassembly, and release, contains 102 one-second samples:

| Metric | Window average | Peak |
| --- | ---: | ---: |
| System-reported VRAM use | 7,420.0 MiB | 14,562 MiB |
| GPU activity | 35.2% | 100% |

The Krea runtime released its model components after the edit. Its completion
snapshot reported 15,370,027,008 bytes free VRAM, 264,265,728 bytes allocated,
and 268,435,456 bytes reserved.

## Acceptance

Extraction, Krea edit execution, `preserve_identity` routing, immutable
single-frame replacement, provenance metadata, encoding, frame count, and full
decode pass this hardware gate.

The comparison preserves the blue/orange subjects, studio, lighting, framing,
and most pixels while changing the middle arm contour. It does not
unambiguously achieve the prompt's requested fully separated outer arms.
Semantic correction quality therefore remains a human review decision and is
not inferred from successful execution. The project owner reviewed and
approved the correction candidate on 2026-07-24.
