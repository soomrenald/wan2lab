# Krea face-detection hardware acceptance

Status: candidate 0 confirmed by the project owner on 2026-07-24.

## Scope

This gate runs Wan2Lab's production face-candidate detector on the immutable
official Wan Animate synthetic reference. It deliberately stops before face
refinement because the Product Phase 1 contract requires user confirmation of
the detected region.

The committed runner is `scripts/krea_face_detection_smoke.py`. It loads the
shared Krea backend under the safe 16 GB ROCm policy, resolves the installed
RetinaFace ONNX model, executes detection, records the provider and candidates,
releases the runtime, and writes `refinement_approved: false`.

## Input

| Field | Value |
| --- | --- |
| Path | `input/wan2lab/official/animate-reference.jpeg` |
| Dimensions | 1280x720 |
| Bytes | 123,149 |
| SHA-256 | `8123db8e5c47c3a229c288b4c5245e8ee2ce4378b1c09e92873b75939812eb7b` |
| Threshold | 0.4 |
| Requested provider | `auto` |
| Resolved provider | `CPUExecutionProvider` |
| Detector | `ComfyUI-WanVideoWrapper/fantasyportrait/models/face_det.onnx` |

## Result

Exactly one candidate was returned:

| Candidate | Box `(x0, y0, x1, y1)` | Score |
| --- | --- | ---: |
| 0 | `(511.5229, 113.5447, 700.8113, 308.3947)` | 0.7487187 |

The annotated evidence is
`output/wan2lab/hardware/animate_reference_face_detection.png`
(1280x720, 598,742 bytes, SHA-256
`03d0af51c8ad3364448107e5effa5e207010db79070f59dba2795ba05ee5b200`).
The box encloses the synthetic character's visible face.

Detection, threshold routing, model discovery, CPU provider selection, typed
candidate output, and model release pass. The project owner confirmed candidate
0 without a manual box correction on 2026-07-24, unlocking the batch refinement
execution gate.

## Refinement preflight result

The first post-confirmation execution normalized the immutable JPEG to a
1280x720 RGB PNG and passed the exact confirmed box through the production
typed batch request. The shared runtime returned:

```text
status: no_regional_lora_faces
detection_count: 1
selected_count: 1
refined_count: 0
```

The returned image was pixel-identical to the normalized source. This is
recorded as a failed preflight, not a refinement pass. The official synthetic
Animate reference is not associated with either installed character identity
adapter, and substituting the unrelated `lface` or `sface` adapter would change
the selected identity.

The preflight exposed and fixed a separate production defect: Wan2Lab retained
the adapter ID and strength but dropped its asset, region, trigger, routing
mode, and model-family metadata before the Krea worker. Face repair now carries
a fully resolved character-identity route and fails closed when no compatible
route exists. The committed `scripts/krea_face_refinement_smoke.py` runner also
requires an explicit compatible adapter and treats any result other than one
completed refined face as failure.

Batch identity repair therefore remains blocked on associating a compatible
Krea identity LoRA/LoKr with this confirmed character, or selecting and
confirming a face belonging to an already configured identity. The no-op
artifact is retained only as diagnostic evidence under:

```text
/home/wolfhard/.cache/wan2lab/krea-face-refinement/
```

## Compatible-identity candidate

The accepted regional adapter keyframe provides a safe alternative: its left
character is already associated with the checksum-verified `lface` identity
LoKr. Production detection on that immutable 768x432 keyframe returned:

| Candidate | Character | Box `(x0, y0, x1, y1)` | Score |
| --- | --- | --- | ---: |
| 0 | Left, blue, `lface` | `(142.0941, 43.2801, 202.4097, 107.0598)` | 0.6044554 |
| 1 | Right, orange, unrelated identity | `(513.6829, 32.0473, 578.0296, 100.6600)` | 0.5611747 |

The annotated evidence uses a green box for candidate 0 and a yellow box for
candidate 1:

```text
/home/wolfhard/ComfyUI/output/wan2lab/hardware/
  lface_batch_face_candidates.png
```

It is 768x432, 468,884 bytes, with SHA-256
`45c4d74d670a6e988d74cece914928859c5373d50fc01cae15733b09597e2ec8`.
Candidate 0 requires a new explicit project-owner confirmation because it is
not the previously confirmed synthetic Animate face. After confirmation, it
can use `krea_lface_tonly.safetensors` without changing identity.
