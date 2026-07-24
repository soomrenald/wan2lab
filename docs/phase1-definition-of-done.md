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
| Prompt, I2V, first/last, arbitrary keyframes, Animate and Replace | Workflow, capability and timeline tests | Every applicable mode executed | Technical pass; fine visual decisions remain for first/last, Animate and Replace |
| Default-active accelerated Wan inference | Acceleration policy, capability fallback, Comfy graph and controller tests | EasyCache ROCm and RTX 5090 runs | Passed |
| Tune backend-supported parameters | Strict descriptors, capability discovery and QML editor tests | Parameters bound in real graphs | Passed |
| User-facing output FPS and advanced generation FPS | Project round-trip, timeline and media tests | Ten-second FPS-normalized export | Passed |
| Generate beyond one native clip | Long-interval planner and persisted-resume tests | Genuine 121-frame continuation | Technical pass; extension awaits project-owner approval |
| Pause after every segment | Review and end-to-end orchestration tests | Generated outputs retained at review gates | Passed |
| Approve, Modify, Reject and regenerate | Review, revision and controller tests | Immutable correction and regeneration artifacts | Passed |
| Wan residency and Krea switching | Runtime and controller transition tests | Retained-model and explicit-release hardware runs | Passed |
| Single-frame and batch-frame edits | Frame workflow, controller and FFmpeg runner tests | Single-frame correction completed | Single-frame technical pass; batch semantic gate awaits confirmed face region |
| Confirm face detection and allow manual correction | Face workflow and controller confirmation tests | One RetinaFace candidate recorded | Correctly paused for project-owner confirmation |
| Batch identity repair | Batch workflow, controller and one-pass assembly tests | Detector/preprocessor path validated | Hardware refinement awaits confirmed face region |
| Keep boundary edits local or propagate | Boundary workflow and invalidation tests | Production frame replacement exercised | Passed |
| Assemble approved segments with provenance | Export, invalidation and integrated acceptance tests | Two-revision ten-second export completed | Passed for approved revisions; long continuation assembly awaits extension approval |
| NVIDIA/AMD handling and OOM protection | Worker preflight, diagnostics and recovery tests | ROCm OOM/recovery and CUDA RTX 5090 execution | Passed |

## Remaining owner decisions

No known Phase 1 software implementation gap remains in section 38.5. The
remaining gates are deliberately non-automated:

1. Accept or reject first/last-frame visual quality.
2. Accept or reject Animate visual quality.
3. Accept or reject Replace visual quality.
4. Accept or reject the mannequin guide's exact pose fidelity.
5. Accept or reject the single-frame identity correction.
6. Accept or reject the generated sequential extension before long assembly.
7. Confirm detected face candidate 0, or provide a corrected manual region,
   before the batch identity-repair run.

The RunPod stop/start persistence test is additionally pending because the
original RTX 5090 host currently has no free GPU slot. The regular volume
remains preserved and the restart verifier is ready; this is an external
capacity condition rather than a Phase 1 desktop implementation gap.

Product Phase 2 UI work remains behind these Phase 1 acceptance gates under the
approved implementation sequence.
