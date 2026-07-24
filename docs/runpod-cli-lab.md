# Standalone RunPod SSH/CLI lab

Status: implementation prepared on 2026-07-24; live provisioning awaits local
RunPod API authentication.

This is the accelerated path to CUDA hardware validation before the Product
Phase 2 browser application is complete. It is a dedicated Wan2Lab Pod and does
not use or modify `k2lab_runpod`.

## Safety and storage choices

- The first target is one RTX 5090 32 GB for TI2V-5B speed validation.
- RTX 6000 Ada 48 GB is the next value comparison when extra VRAM is useful.
- The Pod uses RunPod's official CUDA 12.8/PyTorch 2.8.0 template,
  `runpod-torch-v280`.
- `/workspace` is a 100 GiB regular Pod volume. It survives stop/start, but is
  deleted when the Pod is terminated.
- The container disk is ephemeral and contains no project or model assets.
- The reservation automatically stops after eight hours.
- Provisioning previews live inventory and the exact command before it can
  perform a cost-bearing action.
- ComfyUI listens only on Pod loopback. Use an SSH tunnel instead of exposing
  an unauthenticated ComfyUI API.

A network volume is useful when moving the same assets between different GPU
Pods, but a Pod attached to one cannot be stopped. For this first guarded
benchmark, a 100 GiB regular volume plus automatic stop is the safer default
and leaves ample room after the 13.4 GB TI2V-5B installation.

## One-time workstation setup

RunPod CLI v2.7.2 is installed at `~/.local/bin/runpodctl`. Its downloaded
binary matched the publisher's SHA-256:

```text
acf5c49a3192b522e95cae92539fa6fcd8be8c48802aa26c7f3f2ec980ab4f5c
```

Keep the API key outside Git. Configure it locally:

```bash
read -rsp 'RunPod API key: ' WAN2LAB_RUNPOD_KEY
printf '\n'
runpodctl config --apiKey "${WAN2LAB_RUNPOD_KEY}"
unset WAN2LAB_RUNPOD_KEY
runpodctl gpu list
```

The key is stored by RunPod's CLI and is never copied into the Pod or this
repository.

## Preview and reserve

Preview the RTX 5090 reservation without creating anything:

```bash
cd /home/wolfhard/wan2lab
scripts/runpod/provision_cli_lab.sh
```

After reviewing live availability and price, create it:

```bash
scripts/runpod/provision_cli_lab.sh \
  --create \
  --acknowledge-billing
```

For the 48 GB comparison:

```bash
scripts/runpod/provision_cli_lab.sh \
  --gpu-id 'NVIDIA RTX 6000 Ada Generation' \
  --create \
  --acknowledge-billing
```

Record the returned Pod ID. `runpodctl pod get POD_ID` prints its current state
and SSH connection details.

## Bootstrap over SSH

Get the exact Wan2Lab revision from the workstation:

```bash
cd /home/wolfhard/wan2lab
git rev-parse HEAD
```

SSH into the Pod, clone that immutable revision, and bootstrap:

```bash
cd /workspace
git clone https://github.com/soomrenald/wan2lab.git
cd wan2lab
git checkout --detach WAN2LAB_COMMIT_SHA
WAN2LAB_REF=WAN2LAB_COMMIT_SHA scripts/runpod/bootstrap_cli_lab.sh
```

The bootstrap pins the following runtime:

| Component | Revision |
| --- | --- |
| `k2core` | `a82b0b32a891e19eac5c5f6e35f8a9bfb715f9dc` |
| ComfyUI | `285a98944c397a4a81f15ac63d69fa3dbc0a27b9` |
| ComfyUI-WanVideoWrapper | `088128b224242e110d3906c6750e9a3a348a659b` |
| ComfyUI-VideoHelperSuite | `4ee72c065db22c9d96c2427954dc69e7b908444b` |

It installs `k2core` and `wan2core`, not the PySide6 desktop application.
Resolved versions are written to `/workspace/wan2lab-runtime/versions.env`.

Validate the base environment before downloading models:

```bash
/workspace/wan2lab-venv/bin/python \
  /workspace/wan2lab/scripts/runpod/verify_cli_lab.py
```

## Install the first Wan family

The three TI2V-5B files total 13.4 GB. The installer uses immutable Hugging
Face revisions and validates the exact sizes and hashes already accepted on
local hardware:

```bash
cd /workspace/wan2lab
scripts/runpod/install_ti2v_5b_models.sh

/workspace/wan2lab-venv/bin/python \
  scripts/runpod/verify_cli_lab.py \
  --require-models \
  --verify-model-hashes
```

`HF_TOKEN` may be exported for higher Hugging Face rate limits. Do not put it
on a command line or save it in the repository.

## Start ComfyUI and test through Wan2Lab

On the Pod:

```bash
cd /workspace/wan2lab
scripts/runpod/start_comfy.sh
tail -f /workspace/wan2lab-runtime/logs/comfy-*.log
```

On the workstation, use the SSH target reported by RunPod:

```bash
ssh -L 8188:127.0.0.1:8188 RUNPOD_SSH_TARGET
```

Once `/object_info` responds, verify the required nodes:

```bash
/workspace/wan2lab-venv/bin/python \
  /workspace/wan2lab/scripts/runpod/verify_cli_lab.py \
  --require-models \
  --comfy-url http://127.0.0.1:8188
```

Run the same short Prompt smoke contract used locally:

```bash
cd /workspace/wan2lab
export PYTHONPATH=/workspace/wan2lab/apps/desktop/src:/workspace/wan2lab/packages/wan2core/src
/workspace/wan2lab-venv/bin/python scripts/wan2_2_smoke.py \
  --frames 5 \
  --steps 4 \
  --seed 20260723 \
  --output-prefix wan2lab/remote/ti2v_5b_prompt_5f
```

The next benchmark is the approved 121-frame, 30-step workload with automatic
balanced acceleration, recording wall time, sample time, VAE time, VRAM, GPU
utilization, output SHA-256, and exact runtime revisions.

## Lifecycle

Inspect and stop without destroying `/workspace`:

```bash
runpodctl pod get POD_ID
runpodctl pod stop POD_ID
```

Restart the same Pod and volume:

```bash
runpodctl pod start POD_ID
```

Only terminate after copying every required result elsewhere. Termination
deletes the regular Pod volume:

```bash
runpodctl pod remove POD_ID
```
