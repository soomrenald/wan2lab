# Product Phase 1 export hardware acceptance

Status recorded on 2026-07-23.

## Scope

This gate executes the production approval, export planning, FPS conversion,
boundary-frame de-duplication, manifest, and FFmpeg adapter path against a real
accepted Wan output. The committed runner is
`scripts/phase1_export_smoke.py`.

The immutable source is the approved full-duration Wan2.2 I2V candidate:

| Source | Value |
| --- | --- |
| Path | `output/wan2lab/hardware/krea_to_wan2_2_i2v_121f_30step_seed20260729_00001.mp4` |
| Bytes | 780,725 |
| SHA-256 | `3a555d614b9ac0ba798ce57be8c6695cfd42d0c698733a207466ddc0030b41e3` |
| Media | H.264/yuv420p, 1280x704, 24 FPS, 121 frames, 5.041667 seconds |

The runner registers two distinct immutable generation revisions using that
source, advances both through queued, generating, ready-for-review, and
approved/locked states, and builds a contiguous 0–10 second timeline. The
second request consumes the first revision's declared end-frame asset, so the
export planner removes its duplicated leading boundary frame and pads the tail
to preserve exact segment duration.

Reusing the source deliberately isolates assembly correctness from generation
quality. It does not claim that a second long-continuation generation has been
visually accepted.

## Recovered export defect

The first hardware execution produced 240 frames but only 9.958333 seconds and
reported a non-monotonic DTS at the concatenation boundary. Normalized MP4
segments encode 120 frames with a last presentation timestamp of 4.958333
seconds. The stream-copy concat demuxer inferred the next segment start from
that timestamp and duplicated one boundary DTS.

Wan2Lab now writes each approved segment's authoritative timeline duration
into the concat manifest. This preserves stream-copy quality while forcing
continuous timestamps. A regression test verifies the duration directives.

## Accepted result

| Result | Value |
| --- | --- |
| Output | `output/wan2lab/hardware/wan2lab_phase1_export_10s.mp4` |
| Bytes | 957,097 |
| SHA-256 | `203a4e86d5f1d45ef0c5260c922fe7e9bb24f381359ab98b76482d1033a8aac2` |
| Media | H.264 High/yuv420p, 1280x704 at 24 FPS |
| Frames | 240 container frames and 240 decoded frames |
| Duration | 10.000000 seconds |
| Planned segment frames | 120 + 120 |
| Boundary de-duplication | first `false`; second `true` |
| Executed stages | normalize segment 1, normalize segment 2, concatenate |

`ffprobe -count_frames` verified the exact frame count and duration. A complete
FFmpeg decode to a null sink completed with no warnings or errors.

Sequential approved-revision assembly, output-FPS execution, boundary
de-duplication, and final export therefore pass their Product Phase 1 hardware
gate. A genuinely generated multi-segment continuation and its visual quality
remain separate acceptance work.
