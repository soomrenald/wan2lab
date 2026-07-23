# Wan2.2 TI2V-5B local hardware record

Status recorded on 2026-07-23.

## Scope and result

Wan2.2 TI2V-5B is the first installed Wan family for Wan2Lab. It is a unified
Prompt/T2V and first-frame I2V model. Wan2Lab advertises only those two modes for
this file; first/last, Animate, and Replace remain unavailable until compatible
model families are installed.

Both supported paths completed a live ROCm smoke render through Wan2Lab's
versioned ComfyUI graph and worker execution code:

| Mode | Result |
| --- | --- |
| Prompt | 1280x704, 24 FPS, 5 frames, H.264, 90.26 seconds |
| I2V | 1280x704, 24 FPS, 5 frames, H.264, 97.52 seconds |

These were deliberately short four-step integration renders. They verify graph
shape, model/component loading, sampling, VAE decoding, video encoding, typed
result collection, and provenance. They do not constitute visual-quality
acceptance.

## Pinned runtime observed

- ComfyUI: `285a98944c397a4a81f15ac63d69fa3dbc0a27b9`
  (`0.28.0`)
- ComfyUI-WanVideoWrapper:
  `088128b224242e110d3906c6750e9a3a348a659b`
- ComfyUI-VideoHelperSuite:
  `4ee72c065db22c9d96c2427954dc69e7b908444b`
- Python: `3.12.13`
- PyTorch: `2.10.0+rocm7.1`
- ROCm: `7.1`
- Device: AMD gfx1200, 17,095,983,104 bytes VRAM reported by ComfyUI
- Wrapper Python dependencies: Accelerate 1.14.0, Diffusers 0.39.0,
  PEFT 0.19.1, GGUF 0.19.0, OpenCV 5.0.0.93

## Installed external assets

The weights remain external to this Git repository.

| Component | ComfyUI-relative path | Bytes | SHA-256 |
| --- | --- | ---: | --- |
| Transformer | `models/diffusion_models/Wan2_2-TI2V-5B_fp8_e4m3fn_scaled_KJ.safetensors` | 5,277,255,650 | `a83f54a2450d5471e5721e59ab556afa2d8793e30280713e3796b254c5286b48` |
| VAE | `models/vae/Wan2_2_VAE_bf16.safetensors` | 1,409,401,152 | `0e913a2ca571c75fcb63385a8edadcca73454af5842596cb1ad11e4142590996` |
| Text encoder | `models/text_encoders/umt5-xxl-enc-fp8_e4m3fn.safetensors` | 6,731,333,792 | `3fe5173588270c22505d4f9158bb1644b78331b8614206a97e92760b960c9ffa` |

The scaled FP8 transformer and text encoder are selected with wrapper
quantization set to `disabled`; the wrapper detects their stored formats.

## ComfyUI launch

The working local server command is:

```bash
cd /home/wolfhard/ComfyUI
env \
  AMD_SERIALIZE_KERNEL=1 \
  HIP_VISIBLE_DEVICES=0 \
  ROCR_VISIBLE_DEVICES=0 \
  HIP_LAUNCH_BLOCKING=1 \
  PYTORCH_ALLOC_CONF=expandable_segments:False \
  MIOPEN_USER_DB_PATH=/home/wolfhard/.cache/miopen \
  /home/wolfhard/ComfyUI/venv_rocm7/bin/python main.py \
  --listen 127.0.0.1 \
  --port 8188 \
  --reserve-vram 3 \
  --disable-smart-memory \
  --fp32-vae \
  --disable-async-offload
```

The Wan wrapper loaded successfully. Several unrelated custom nodes reported
missing optional dependencies during this host's startup; they did not affect
the Wan graph or its required node set.

## Reproduce the smoke tests

From the Wan2Lab repository:

```bash
export PYTHONPATH=apps/desktop/src:packages/wan2core/src:/home/wolfhard/k2core/src

/home/wolfhard/krea_region_project/.venv/bin/python \
  scripts/wan2_2_smoke.py \
  --frames 5 \
  --steps 4 \
  --output-prefix wan2lab/hardware/wan2_2_ti2v_prompt_5f

/home/wolfhard/krea_region_project/.venv/bin/python \
  scripts/wan2_2_smoke.py \
  --mode i2v \
  --start-image example.png \
  --frames 5 \
  --steps 4 \
  --prompt "The simple cartoon bunny gently waves both arms while the camera remains still." \
  --output-prefix wan2lab/hardware/wan2_2_ti2v_i2v_5f
```

For I2V, `--start-image` is relative to the ComfyUI input directory.
Use `--runs 2` (or more) to submit sequential regenerations through one worker
session and `--release` to free the selection after the final run.
For a 121-frame render on a 16 GB device, use
`--vae-tile-size 128 --vae-tile-stride 64`. Wan2Lab selects those decode
defaults automatically when live discovery reports at most 18 GiB of total
VRAM; larger hosts retain the wrapper defaults.

## Output evidence

| Mode | ComfyUI-relative output | Bytes | SHA-256 |
| --- | --- | ---: | --- |
| Prompt | `output/wan2lab/hardware/wan2_2_ti2v_prompt_5f_00001.mp4` | 82,064 | `50443aadc246f060cc861223f40b63fda27417e30918fa55e38988894ff17e42` |
| I2V | `output/wan2lab/hardware/wan2_2_ti2v_i2v_5f_00001.mp4` | 54,287 | `f2e86321c6485c52ce43165f891915670df372ff6eb43b184e57619ea4f28abd` |

Prompt ID `8c6115a3-6550-444d-9d87-e51a0e620abe` produced the Prompt result.
Prompt ID `34d05420-fe9c-4ee6-b6b7-eee5bbc77e82` produced the I2V result.

The highest observed sampler allocation was 5.453 GB and highest observed
sampler reservation was 5.883 GB during the I2V smoke test.

## Retained residency and regeneration evidence

Two Prompt jobs were submitted sequentially through one loaded
`ComfyWorkerService`, with no intervening release:

| Run | Prompt ID | Output | Bytes | SHA-256 | ComfyUI time |
| --- | --- | --- | ---: | --- | ---: |
| 1 | `bd109283-aa7d-4751-b03f-dbbca156cf29` | `output/wan2lab/hardware/wan2_2_ti2v_residency_01_00001.mp4` | 91,696 | `57c73080c16b8091da99b8b8e617e59b8cbaa66bd4bf78d7cc560231969a2c5e` | 99.05 s |
| 2 | `e982ebe7-9d37-4db7-9573-e7935c6cb1b2` | `output/wan2lab/hardware/wan2_2_ti2v_residency_02_00001.mp4` | 76,803 | `871ed70698a5dabe307e6a3ef62ac8babdcaa5cfc3f018771d601be9a7b72a25` | 88.08 s |

Both results are 1280x704, 24 FPS, five-frame H.264 files. ComfyUI's second-job
log omitted transformer format detection, T5 parameter loading, and T5
encoding. It reused the cached graph outputs and host-resident model, while the
configured offload policy still copied transformer weights to the GPU for
sampling. The second job then completed with a new seed and distinct output.

## Full-length memory recovery

A 121-frame, 1280x704 Prompt graph completed its single denoising step with a
27,280-token input sequence, a 6.352 GB peak sampler allocation, and a 7.264 GB
peak reservation. The wrapper-default 272-pixel VAE tiles then exhausted the
16 GB device during decode: HIP requested another 7.62 GiB with 2.60 GiB free.

Wan2Lab converted ComfyUI's failure into a recoverable `WanOutOfMemory`, called
the explicit model-release path, and returned the host to 16,351,494,144 bytes
of free VRAM without terminating ComfyUI. This is live hardware evidence for
the structured OOM and recovery path.

The retry with 128-pixel tiles and 64-pixel strides completed:

| Prompt ID | Output | Bytes | SHA-256 | ComfyUI time |
| --- | --- | ---: | --- | ---: |
| `e4d9346e-514f-4098-b0ed-1ddee2e3f616` | `output/wan2lab/hardware/wan2_2_ti2v_prompt_121f_1step_smalltiles_00001.mp4` | 1,172,907 | `976f24e040553c3cc387b242d4f19cde5e4541361815d7f356e91fed83adb14b` | 21 min 13 s |

The output is H.264/yuv420p, 1280x704, 24 FPS, exactly 121 frames, and
5.041667 seconds long. FFmpeg decoded every frame without error. Sampling took
4 minutes 50 seconds and peaked at 6.322 GB allocated and 7.246 GB reserved;
the 45-tile VAE decode took 16 minutes 8 seconds. The smaller working set trades
decode speed and possible tile seams for completion on this host.

This one-step render proves the model's advertised full-duration graph, memory
recovery, decode, and output path. It is intentionally not a production-quality
or visual-quality acceptance render.

## Explicit release evidence

The worker's explicit release command completed successfully against the live
ComfyUI `/free` endpoint. Immediately before release, the worker reported the
TI2V-5B selection as resident and ComfyUI reported 16,353,591,296 bytes of free
VRAM. Two seconds after release, the worker reported `resident: false`, no
selected model, and the same free-VRAM value. This verifies the worker state
transition and compatibility with ComfyUI's successful empty response body.

## Model-specific graph behavior

The wrapper uses two different 5B paths:

- Prompt uses `WanVideoEmptyEmbeds`; the sampler expands the latent to the
  model's 48-channel shape and halves the spatial latent dimensions.
- I2V center-crops/scales the source to the exact requested dimensions, encodes
  it through `WanVideoEncode`, and supplies the resulting 48-channel latent as
  `WanVideoEmptyEmbeds.extra_latents`.

Using the ordinary I2V conditioning graph for this model creates a 96-channel
input and is rejected by the 48-channel patch embedding. The live smoke tests
are regression evidence for the model-specific routing now implemented in
Wan2Lab.
