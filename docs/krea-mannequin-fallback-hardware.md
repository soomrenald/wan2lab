# Krea mannequin fallback hardware acceptance

Status recorded on 2026-07-23.

## Result

The production mannequin renderer and capability resolver completed a real
Krea image-to-image run on the local AMD/ROCm host.

The runtime advertised Krea image editing but no compatible depth-control
model IDs. Wan2Lab therefore selected `i2i_scaffold`, used the rendered shaded
guide, and did not route the separately rendered depth guide into an
incompatible adapter.

The accepted technical output is:

```text
/home/wolfhard/.cache/wan2lab/krea-mannequin-fallback/results/
  hardware-mannequin-shaded_edited_20260724T025304Z_seed-20260802.png
```

- Dimensions: 832x480 RGB PNG.
- Size: 203,171 bytes.
- SHA-256:
  `3baac8aac6396c1addd31d9b2645ca9d4a9489cb08e82cbb823fa045d30bd289`.
- Seed: `20260802`.
- Sampler/scheduler: Euler/simple.
- Steps: 4.
- Edit strength: 0.45.
- Warnings: none.

The result is a coherent, centered, full-body wooden mannequin with the
requested studio appearance. It preserves the broad composition, but it does
not preserve every arm angle from the sparse guide. The execution and
capability-gating acceptance passes; exact pose fidelity remains a human
visual-quality decision.

## Guide evidence

All three 832x480 production guide outputs were created:

| Guide | Bytes | SHA-256 |
| --- | ---: | --- |
| shaded | 3,355 | `c6ed8753d203313019bc333f79b56f9f715ec4c13e63b3e3e38ea859e829c632` |
| silhouette | 3,381 | `7dff76eacaafd01835a526c4808d3e54aa2bd64f8f75bac8b684b4e5cedf2ebc` |
| depth | 3,316 | `d0454e44539ec61d7258954147e57969fc95e4ad15689d2e17e89e147edf955a` |

The resolved plan was:

```json
{
  "path": "i2i_scaffold",
  "guide_asset_id": "hardware-mannequin-shaded",
  "depth_control_model_id": null,
  "edit_strength": 0.45
}
```

## Runtime and resource evidence

- PyTorch: 2.10.0+rocm7.1.
- HIP: 7.1.25424.
- Selected device: AMD gfx1200, 17,095,983,104 bytes reported memory.
- Krea memory policy: `safe_16gb`.
- CPU VAE: enabled.
- Telemetry: 72 one-second samples.
- Average/peak VRAM: 7,436.5 / 14,634 MiB.
- Average/peak GPU activity: 35.4% / 100%.
- Average/peak graphics clock: 1,212.2 / 3,239 MHz.
- Raw telemetry:
  `/tmp/wan2lab-krea-mannequin-20260723.csv`.

## Reproduction

Run with the ComfyUI ROCm environment so the Krea backend can access the GPU:

```bash
PYTHONPATH=packages/wan2core/src:apps/desktop/src:../k2core/src \
~/ComfyUI/venv_rocm7/bin/python \
  scripts/krea_mannequin_fallback_smoke.py
```

The runner probes the runtime, renders all guide kinds, resolves the
capability-gated conditioning plan, executes Krea only when the fallback is
selected, releases the model, and prints exact evidence.

