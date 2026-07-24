# Genuine sequential continuation export acceptance

Status: passed on 2026-07-24.

## Scope

This gate assembles two distinct project-owner-approved Wan2.2 revisions through
the production export planner and FFmpeg adapter. Unlike the earlier isolated
assembly test, the second input is the genuinely generated continuation of the
first input's exact final frame.

The committed `scripts/phase1_export_smoke.py` runner now accepts
`--continuation` while retaining its original same-source smoke behavior.

## Approved immutable inputs

| Segment | Seed | Bytes | SHA-256 |
| --- | ---: | ---: | --- |
| Base I2V | `20260729` | 780,725 | `3a555d614b9ac0ba798ce57be8c6695cfd42d0c698733a207466ddc0030b41e3` |
| Genuine continuation | `20260806` | 693,714 | `335c06e717f53d26713571acf351aa51a90515b8bfdf08866afe7bdfda7dac18` |

Both inputs are H.264/yuv420p at 1280x704 and 24 FPS with 121 frames. The
project owner approved the continuation before this assembly was executed.

## Production assembly

The planner created two exact five-second timeline segments. It normalized each
input to 120 output frames, removed the continuation's duplicated leading
boundary frame, preserved the authoritative duration, and concatenated the
normalized revisions.

```text
/home/wolfhard/ComfyUI/output/wan2lab/hardware/
  wan2lab_phase1_genuine_continuation_10s.mp4
```

| Result | Value |
| --- | --- |
| Bytes | 903,939 |
| SHA-256 | `73240b3371dbdec1b966f6e526df2577ca720530e8369cef56fd632d4d69d04b` |
| Codec | H.264/yuv420p |
| Dimensions | 1280x704 |
| Frame rate | 24 FPS |
| Frames | 240 container and 240 decoded |
| Duration | 10.000000 seconds |
| Boundary de-duplication | first `false`; continuation `true` |

`ffprobe -count_frames` verified the exact duration and frame counts. A complete
FFmpeg decode to a null sink completed without warnings or errors.

The ten-frame contact sheet is:

```text
/home/wolfhard/ComfyUI/output/wan2lab/hardware/
  wan2lab_phase1_genuine_continuation_10s_contact.png
```

It is 1600x352, 824,877 bytes, with SHA-256
`0bb09fe8a0a95989ae2df362ab841d48dcf6e7ca1398c5839b2a15825f2c7e21`.
Inspection shows the approved wave and hand-lowering sequence in a stable
studio with the blue and orange subjects retained across the join.

Genuine multi-segment continuation, approval gating, boundary de-duplication,
provenance-bearing planning, exact-duration assembly, and final decoding
therefore pass the Product Phase 1 hardware gate.
