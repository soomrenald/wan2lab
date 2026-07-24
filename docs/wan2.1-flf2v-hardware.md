# Wan2.1 FLF2V hardware acceptance

Status recorded on 2026-07-23.

## Scope

This gate validates the next distinct Wan family after the accepted Wan2.2
TI2V-5B family: Wan2.1 FLF2V-14B-720P, using first and last image anchors
through the local Wan2Lab worker on the 16 GB ROCm host.

The official Wan release identifies FLF2V-14B as a 720p model and recommends
Chinese prompts because its first/last-frame training data is primarily
Chinese. The installed ComfyUI-WanVideoWrapper reference graph uses the
wrapper-native FP8 transformer, Wan2.1 VAE, `clip_vision_h`, endpoint CLIP
embedding concatenation, and 20/40 block offload for a roughly 16 GB profile.

## Checksum-verified artifacts

| Component | Repository path | Bytes | SHA-256 |
| --- | --- | ---: | --- |
| Transformer | `models/diffusion_models/Wan2_1-FLF2V-14B-720P_fp8_e4m3fn.safetensors` | 17,138,067,577 | `5c88fbec1f57255b42503bf3a1cfa495dc2d4ae891f1ebce2edbe9bbd155c5e2` |
| VAE | `models/vae/Wan2_1_VAE_bf16.safetensors` | 253,806,278 | `1ab9a32cc2c740f6e39d80d367ce5dcc28db8c71b79b28670546b8973e9d75f9` |
| CLIP vision | `models/clip_vision/clip_vision_h.safetensors` | 1,264,219,396 | `64a7ef761bfccbadbaa3da77366aac4185a6c58fa5de5f589b42a65bcc21f161` |
| Reused UMT5 | `models/text_encoders/umt5-xxl-enc-fp8_e4m3fn.safetensors` | 6,731,333,792 | `3fe5173588270c22505d4f9158bb1644b78331b8614206a97e92760b960c9ffa` |

The transformer and VAE come from `Kijai/WanVideo_comfy`; CLIP vision comes
from `Comfy-Org/Wan_2.1_ComfyUI_repackaged`. The upstream base model is
`Wan-AI/Wan2.1-FLF2V-14B-720P`.

## Immutable endpoint inputs

| Endpoint | ComfyUI input path | Dimensions | SHA-256 |
| --- | --- | --- | --- |
| First | `wan2lab/krea-wan-handoff.png` | 768x432 | `a7731584d20f2d2f7810d8258312ee09be828a61d211b77708c2b495678cf00b` |
| Last | `wan2lab/krea-wan-handoff-end.png` | 1280x704 | `a6d5c3ecb2461923f66f9b844fc260010a84dfc38210ff058d1b94aefc933f4b` |

The first endpoint is the accepted Krea two-mannequin keyframe. The last
endpoint is the decoded final frame of the accepted five-second Wan2.2 I2V
revision, preserving the same subjects while changing their poses.

## Reproducible run

The committed runner is `scripts/wan2_1_flf2v_smoke.py`. Its acceptance profile
is:

- 832x480, 17 frames, 16 FPS;
- 30 sampling steps, CFG 6, shift 5;
- BF16 compute with wrapper-autodetected FP8 weights;
- explicit CPU/offload-device routing;
- 20/40 transformer blocks swapped on this 16 GB host;
- tiled decode at 128 pixels with 64-pixel strides;
- default-active auto/balanced Wan acceleration;
- endpoint CLIP embeddings combined with `concat`;
- official FLF/Fun latent layout enabled.

Prompt:

> 干净明亮的摄影棚里，蓝色和橙色木制艺术人体模型分别平稳地抬起一只手挥手。镜头固定，全身广角，动作连贯自然。

Negative prompt:

> 过曝，静态，模糊，低质量，畸形，额外肢体，闪烁，跳帧，字幕，水印

## Result

The hardware run completed through Wan2Lab on the local ROCm host:

| Result | Value |
| --- | --- |
| Output | `output/wan2lab/hardware/wan2_1_flf2v_17f_30step_00001.mp4` |
| Output SHA-256 | `35bac8c22b40a854e2180f05060c47a69564dfd5875a27e716cba605b22fa873` |
| Container/video | MP4, H.264 High, yuv420p |
| Dimensions/rate | 832x480 at 16 FPS |
| Frames/duration | 17 frames, 1.062500 seconds |
| File size | 118,836 bytes |
| Comfy prompt ID | `421d903f-5784-4f0e-9694-939eca15f8be` |
| End-to-end Comfy execution | 405.07 seconds |
| Sampling | 351 seconds |
| Tiled VAE decode | 29 seconds |

`ffprobe -count_frames` reported 17 container and decoded frames. A complete
FFmpeg decode to a null sink completed without warnings or errors.

EasyCache was selected by the default `auto/balanced` policy and executed with
threshold 0.015, start step 10, end step -1, and its cache on the offload
device. The result provenance records the active method as
`comfy-wan-easycache`; no base-inference fallback occurred.

## Resource evidence

The accepted telemetry window contains 421 one-second samples from
19:19:21 through 19:26:23 local time:

| Metric | Window average | Peak |
| --- | ---: | ---: |
| System-reported VRAM use | 11,294.5 MiB | 15,127 MiB |
| GPU activity | 85.8% | 100% |
| GFX clock | 2,620.8 MHz | 3,242 MHz |

The wrapper separately reported 10.107 GB maximum PyTorch allocation and
10.391 GB maximum reservation during sampling. Its block-swap summary placed
8,093.14 MB of transformer blocks on the GPU and 7,322.37 MB on CPU.

## Recovered host failures

Hardware execution exposed and fixed three production issues before the
accepted run:

1. differently sized endpoint images could not be concatenated by the wrapper
   CLIP node; Wan2Lab now scales both endpoints to the requested dimensions;
2. this wrapper returned `None` for an omitted optional VACE block-swap value;
   non-VACE graphs now bind it explicitly to zero;
3. non-blocking swap transfers exhausted host RAM and the kernel killed
   ComfyUI. Constrained-memory graphs now use blocking-safe transfers.

The first transformer download also occupied a kernel-reported corrupt Btrfs
zstd extent. That copy remains quarantined. The accepted transformer was
downloaded into a `compression=none` staging directory, matched the pinned
byte count and SHA-256 on two consecutive reads, produced no new kernel
storage errors, and matched again after its atomic move into the model store.

## Contact sheet and visual gate

Frames 0, 4, 8, 12, and 16 are sampled in
`output/wan2lab/hardware/wan2_1_flf2v_17f_30step_00001_contact.png`
(2,080x240, 644,460 bytes, SHA-256
`82573003f0dc955eaebb7be6db1f8da7070e32534cadfb2ff694771066fece69`).

The sampled frames preserve the blue/orange subjects and studio composition
while moving from lowered arms to raised hands. The middle transition includes
arm/hand overlap between the subjects. File, pipeline, endpoint-conditioning,
and resource acceptance pass; semantic and visual quality remain an explicit
human review decision.
