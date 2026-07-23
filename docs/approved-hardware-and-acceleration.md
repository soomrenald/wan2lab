# Approved Wan hardware and acceleration requirements

This document records the July 2026 amendment to the authoritative Wan2Lab
implementation specification. The source specification lives outside this
repository at
`/home/wolfhard/wan2lab_detailed_implementation_spec_updated.md`.

## Default acceleration behavior

Accelerated Wan inference is a primary option for every Wan pipeline:
Prompt/T2V, I2V, first/last, Animate, and Replace.

- Project acceleration defaults to enabled, automatic selection, and the
  balanced quality profile.
- A segment may inherit, enable, disable, or request a specific method.
- Initial method families are LightX2V, FastVideo, Wan Lightning/distilled
  low-step schedules, and backend-declared cache acceleration such as
  TeaCache, MagCache, or EasyCache.
- Backends declare exact model, mode, runtime, accelerator, artifact,
  schedule, adapter, and quality compatibility.
- Animate and Replace use an accelerator only when that specialized pipeline
  explicitly declares support.
- Automatic selection chooses the highest-ranked installed compatible method.
- When nothing compatible is installed, the request visibly resolves to base
  inference. The UI must not claim that acceleration is active.
- Provenance records requested and resolved policy, method/artifact hashes,
  schedule, cached or skipped work, warnings, and fallback reason.

## Initial model-to-GPU guidance

| Wan workload | Value | Speed | Full-memory/production |
| --- | --- | --- | --- |
| TI2V-5B Prompt and I2V | RTX 4090 24 GB | RTX 5090 32 GB | RTX 6000 Ada 48 GB |
| Quantized/offloaded 14B Prompt, I2V, first/last | RTX 6000 Ada 48 GB | RTX PRO 6000 Blackwell 96 GB | RTX PRO 6000 Blackwell 96 GB |
| Animate and Replace 14B | A100 80 GB | RTX PRO 6000 Blackwell 96 GB | RTX PRO 6000 Blackwell 96 GB |
| Minimum-latency 14B after matching benchmark validation | H100 80 GB | H100 80 GB | H100 80 GB |

A40 48 GB and RTX A6000 48 GB are cost-oriented fallbacks for
non-interactive quantized/offloaded 14B batch work. A100 80 GB is the initial
lower-cost full-memory 14B baseline. H100-class and newer premium accelerators
are recommended only when live matching benchmark/cost evidence or an explicit
latency requirement justifies them.

`wan2core` owns provider-neutral typed workload recommendations. Live RunPod
inventory and pricing remain `runpod_core` responsibilities. Before the first
cost-bearing Pod reservation, the browser must combine both sources, show
model/mode suitability, VRAM, expected acceleration and offload, rationale,
live price, and matching benchmark evidence. It must revalidate immediately
before reservation and require explicit cost confirmation.
