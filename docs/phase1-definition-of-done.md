# Product Phase 1 definition-of-done traceability

Status: audited on 2026-07-24 against specification section 38.5.

This matrix separates implemented behavior, hardware execution, and decisions
that the specification reserves for project-owner review. `Automated` means the
behavior is covered by the 157-test Phase 1 suite. `Hardware` means the real
local ROCm or remote CUDA path has produced and validated an artifact.

| Section 38.5 requirement | Automated evidence | Hardware evidence | Status |
| --- | --- | --- | --- |
| Import or generate Krea keyframes | Keyframe workflow and desktop controller tests | Regional Krea-to-Wan handoff | Passed |
| Assign multiple regional character identities | Regional composition and controller approval tests | Region-routed LoKr handoff | Passed |
| Unlimited named character sheets with identity, appearance and adapters | Character, sheet-operation and scoped-adapter tests | Krea sheet/adapter runtime already exercised | Passed |
| Duplicate a sheet into another appearance | Immutable restyle and sequential controller tests | Krea edit backend exercised | Passed |
| Select individual entries for regional generation | Composition and regional-controller tests | Two-subject regional handoff | Passed |
| Tune lighting and environment independently | Separate identity/appearance metadata and request tests | Krea generation path exercised | Passed |
| Integrated mannequin and Blender guidance | Portable scene, Blender import, renderer and controller tests | Shaded i2i fallback completed because depth control was unavailable | Passed |
| Prompt, I2V, first/last, arbitrary keyframes, Animate and Replace | Workflow, capability and timeline tests | Every applicable mode executed and owner-approved | Passed |
| Default-active accelerated Wan inference | Acceleration policy, capability fallback, Comfy graph and controller tests | EasyCache ROCm and RTX 5090 runs | Passed |
| Tune backend-supported parameters | Strict descriptors, capability discovery and QML editor tests | Parameters bound in real graphs | Passed |
| User-facing output FPS and advanced generation FPS | Project round-trip, timeline and media tests | Ten-second FPS-normalized export | Passed |
| Generate beyond one native clip | Long-interval planner and persisted-resume tests | Genuine 121-frame continuation, owner-approved | Passed |
| Pause after every segment | Review and end-to-end orchestration tests | Generated outputs retained at review gates | Passed |
| Approve, Modify, Reject and regenerate | Review, revision and controller tests | Immutable correction and regeneration artifacts | Passed |
| Wan residency and Krea switching | Runtime and controller transition tests | Retained-model and explicit-release hardware runs | Passed |
| Single-frame and batch-frame edits | Frame workflow, controller and FFmpeg runner tests | Single-frame correction approved; batch region confirmed | Batch hardware execution unlocked |
| Confirm face detection and allow manual correction | Face workflow and controller confirmation tests | RetinaFace candidate 0 owner-confirmed | Passed |
| Batch identity repair | Batch workflow, controller and one-pass assembly tests | Detector/preprocessor path validated and region confirmed | Hardware execution unlocked |
| Keep boundary edits local or propagate | Boundary workflow and invalidation tests | Production frame replacement exercised | Passed |
| Assemble approved segments with provenance | Export, invalidation and integrated acceptance tests | Distinct approved base and genuine continuation assembled into an exact ten-second output | Passed |
| NVIDIA/AMD handling and OOM protection | Worker preflight, diagnostics and recovery tests | ROCm OOM/recovery and CUDA RTX 5090 execution | Passed |

## Remaining execution

No known Phase 1 software implementation gap or owner-review decision remains
in section 38.5. On 2026-07-24 the project owner approved every remaining
candidate and confirmed face candidate 0 without a manual box correction. The
genuine long assembly passed on 2026-07-24. Batch identity repair must still
execute and pass its technical validation before Phase 1 is complete. Its
confirmed synthetic character has no associated compatible identity adapter;
the runtime correctly refuses to substitute an unrelated installed identity.

The independent RunPod stop/start persistence test passed on 2026-07-24 after
the original host regained capacity. The regular volume preserved all pinned
models and prior evidence, and a distinct continuation job succeeded after
service restart. The Pod was safely stopped again.

Product Phase 2 UI work remains behind these Phase 1 acceptance gates under the
approved implementation sequence.
