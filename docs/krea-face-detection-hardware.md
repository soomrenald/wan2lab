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
