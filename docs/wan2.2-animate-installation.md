# Wan2.2 Animate/Replace installation record

Status recorded on 2026-07-23.

## Runtime contract

Wan2Lab uses the unified Wan2.2 Animate model for two separately versioned
pipelines:

- `wan2lab-wan2.2-animate` performs reference-character animation from a
  driving video;
- `wan2lab-wan2.2-replace` additionally segments the source subject and
  preserves the source background.

Both templates use `WanVideoAnimateEmbeds`, explicit reference CLIP encoding,
ViTPose/YOLO pose and face preprocessing, the wrapper sampler, tiled VAE
decoding, and 25/40 block swapping on GPUs with at most 18 GiB. Replace adds
SAM2 video segmentation, mask expansion/block alignment, and masked background
conditioning.

The templates are not advertised as executable until every required node is
installed and the CLIP, ViTPose, YOLO, and (for Replace) SAM2 choices are
nonempty. Each mode remains single-character. Ordinary Wan LoRAs are not
attached.

Installed node revisions:

| Node package | Git revision |
| --- | --- |
| `kijai/ComfyUI-WanVideoWrapper` | `088128b224242e110d3906c6750e9a3a348a659b` |
| `kijai/ComfyUI-KJNodes` | `285a98944c397a4a81f15ac63d69fa3dbc0a27b9` |
| `kijai/ComfyUI-WanAnimatePreprocess` | upstream `0e0b6a2a555625acf4d4aefb780e27d06937132f`; local AMD dependency commit `46d6956` |
| `kijai/ComfyUI-segment-anything-2` | `0c35fff5f382803e2310103357b5e985f5437f32` |

The preprocessing package declared `onnxruntime-gpu`, but its installed CUDA
13 wheel could not import on this AMD host. The local dependency commit changes
that declaration to `onnxruntime` and adds its undeclared `matplotlib`
dependency. The separately installed Facefusion plugin also overwrote the
environment with `onnxruntime-gpu` during every startup, so its dependency
manifest and installer are pinned by local commit `900eab3` to the same CPU
package. A restart verified that these overrides persist:
`onnxruntime==1.27.0` imports with `AzureExecutionProvider` and
`CPUExecutionProvider`, while both Wan preprocessing nodes are present in the
live registry. Wan sampling and SAM2 still run through PyTorch ROCm; only ONNX
pose/detection inference uses CPU.

## Checksum-pinned artifacts

| Component | Installed path | Bytes | SHA-256 |
| --- | --- | ---: | --- |
| YOLOv10m | `models/detection/yolov10m.onnx` | 61,659,339 | `89b526498a6d55f869a6ab52e3a2eb20ad45b3711c1f7de3dd9ca0b399dfd6d7` |
| ViTPose-L whole body | `models/detection/vitpose-l-wholebody.onnx` | 1,234,579,166 | `89bdf6692d9224dbd5004dcef23a9ba2d54c5776212b359d5a5b5068ac14fd08` |
| SAM2.1 base-plus FP16 | `models/sam2/sam2.1_hiera_base_plus-fp16.safetensors` | 161,773,292 | `a2693628452963a5f17e73a70a90b5faa112109307c828dec36e5fb407061005` |
| Animate transformer (pinned) | `models/diffusion_models/Wan2_2-Animate-14B_fp8_e4m3fn_scaled_KJ.safetensors` | 18,401,760,586 | `2936b31473a967e7a429a6646bba60e7862d0938e178b58b2a140f391dd5b8e6` |

Artifact source revisions:

- Wan Animate checkpoint and YOLO:
  `Wan-AI/Wan2.2-Animate-14B@cb93a225fbaf1ca100f54e79da8f994995b689b3`;
- ViTPose:
  `JunkyByte/easy_ViTPose@e83805274e89428969355ec4afffcbc413e79188`;
- SAM2:
  `Kijai/sam2-safetensors@f885607d88bb3f9145efa49c3e3c50a9e5bf13eb`;
- scaled FP8 transformer:
  `Kijai/WanVideo_comfy_fp8_scaled@033a4e487f60220b3d6e469599a6aebc46e13cee`.

The three installed preprocess artifacts each passed two consecutive SHA-256
reads. Model directories are marked `compression=none` because the initial
large FLF download exposed kernel-reported Btrfs zstd decompression failures.

## Immutable official test inputs

Inputs come from
`Wan-Video/Wan2.2@42bf4cfaa384bc21833865abc2f9e6c0e67233dc`.

| Mode/input | ComfyUI input path | Bytes | SHA-256 |
| --- | --- | ---: | --- |
| Animate reference | `wan2lab/official/animate-reference.jpeg` | 123,149 | `8123db8e5c47c3a229c288b4c5245e8ee2ce4378b1c09e92873b75939812eb7b` |
| Animate driving video | `wan2lab/official/animate-driving.mp4` | 903,201 | `80f3cfe3786a7f8a94844476448fb45e7e115216ddcdaad14b0b88223be597e7` |
| Replace reference | `wan2lab/official/replace-reference.jpeg` | 143,318 | `412591418fbb133bd46c41b3376b810bd7e3eb59b916bf9693da337a08ca1b0d` |
| Replace source video | `wan2lab/official/replace-source.mp4` | 754,294 | `db6da60e5fcb0fda0bff151bfbdbb7085d5a86a78508743cce2a25709de86a19` |

The committed runner is `scripts/wan2_2_animate_smoke.py`. ComfyUI has been
restarted and exposes the installed preprocessing nodes; hardware execution
now requires only the transformer installation.
