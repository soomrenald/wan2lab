"""Explicitly reviewed identity-drift checkpoint and segment-split workflows."""

from __future__ import annotations

from wan2core.identity import (
    CheckpointProposal,
    IdentityDriftWarning,
    approve_checkpoint_proposal,
)
from wan2core.keyframes import Keyframe, KeyframeSource
from wan2core.keyframes import Rectangle
from wan2core.projects import Wan2LabProject
from wan2core.projects.invalidation import invalidate_segments
from wan2core.provenance import ProvenanceRecord


def register_identity_analysis(
    project: Wan2LabProject,
    *,
    warnings: tuple[IdentityDriftWarning, ...],
    proposal: CheckpointProposal | None = None,
) -> Wan2LabProject:
    revision_ids = {item.revision_id for item in project.segment_revisions}
    identity_ids = {item.identity_id for item in project.characters}
    if any(item.segment_revision_id not in revision_ids for item in warnings):
        raise ValueError("identity analysis references a missing revision")
    if any(item.identity_id not in identity_ids for item in warnings):
        raise ValueError("identity analysis references a missing character")
    proposals = project.checkpoint_proposals
    if proposal is not None:
        if set(proposal.warning_ids) != {item.warning_id for item in warnings}:
            raise ValueError("checkpoint proposal must reference the supplied warnings")
        proposals = (*proposals, proposal)
    updated = project.model_copy(
        update={
            "identity_warnings": (*project.identity_warnings, *warnings),
            "checkpoint_proposals": proposals,
        }
    )
    return Wan2LabProject.model_validate(updated.model_dump())


def propose_checkpoint_from_warnings(
    *,
    proposal_id: str,
    segment_id: str,
    segment_start_ms: int,
    segment_end_ms: int,
    generation_fps: float,
    warnings: tuple[IdentityDriftWarning, ...],
) -> CheckpointProposal:
    if not warnings:
        raise ValueError("checkpoint proposal requires identity warnings")
    if generation_fps <= 0:
        raise ValueError("generation FPS must be positive")
    median_index = sorted(item.frame_index for item in warnings)[len(warnings) // 2]
    time_ms = min(segment_end_ms, segment_start_ms + round(median_index * 1000 / generation_fps))
    return CheckpointProposal(
        proposal_id=proposal_id,
        segment_id=segment_id,
        time_ms=time_ms,
        reason="Identity drift requires a user-reviewed continuity checkpoint",
        warning_ids=tuple(item.warning_id for item in warnings),
    )


def approve_registered_checkpoint(
    project: Wan2LabProject,
    proposal_id: str,
) -> Wan2LabProject:
    found = False
    proposals = []
    for proposal in project.checkpoint_proposals:
        if proposal.proposal_id == proposal_id:
            found = True
            proposals.append(approve_checkpoint_proposal(proposal))
        else:
            proposals.append(proposal)
    if not found:
        raise KeyError(proposal_id)
    updated = project.model_copy(update={"checkpoint_proposals": tuple(proposals)})
    return Wan2LabProject.model_validate(updated.model_dump())


def confirm_warning_association(
    project: Wan2LabProject,
    *,
    segment_revision_id: str,
    identity_id: str,
    frame_index: int,
    region: Rectangle,
) -> Wan2LabProject:
    found = False
    warnings = []
    for warning in project.identity_warnings:
        if (
            warning.segment_revision_id == segment_revision_id
            and warning.identity_id == identity_id
            and warning.frame_index == frame_index
        ):
            found = True
            warnings.append(
                warning.model_copy(
                    update={
                        "proposed_region": region,
                        "association_confirmed": True,
                    }
                )
            )
        else:
            warnings.append(warning)
    if not found:
        return project
    return Wan2LabProject.model_validate(
        project.model_copy(update={"identity_warnings": tuple(warnings)}).model_dump()
    )


def apply_approved_checkpoint(
    project: Wan2LabProject,
    *,
    proposal_id: str,
    keyframe_id: str,
    source_frame_asset_id: str,
    provenance: ProvenanceRecord,
) -> Wan2LabProject:
    proposal = next(
        (item for item in project.checkpoint_proposals if item.proposal_id == proposal_id),
        None,
    )
    if proposal is None:
        raise KeyError(proposal_id)
    if not proposal.user_approved:
        raise ValueError("checkpoint proposal requires explicit user approval")
    if not proposal.propose_keyframe:
        raise ValueError("approved proposal does not request a keyframe")
    asset_ids = {item.asset_id for item in project.assets}
    if source_frame_asset_id not in asset_ids:
        raise ValueError("checkpoint source frame asset is missing")
    if provenance.output_asset_ids:
        raise ValueError("checkpoint linkage provenance must not claim a new immutable asset")
    keyframe = Keyframe(
        keyframe_id=keyframe_id,
        time_ms=proposal.time_ms,
        image_asset_id=source_frame_asset_id,
        source_type=KeyframeSource.EXTRACTED_VIDEO,
        provenance_id=provenance.provenance_id,
        approved=True,
        locked=True,
    )
    keyframes = tuple(sorted((*project.keyframes, keyframe), key=lambda item: item.time_ms))
    if len({item.time_ms for item in keyframes}) != len(keyframes):
        raise ValueError("checkpoint collides with an existing exact-time keyframe")
    timeline = project.timeline.model_copy(
        update={"keyframe_ids": tuple(item.keyframe_id for item in keyframes)}
    )
    updated = project.model_copy(
        update={
            "keyframes": keyframes,
            "timeline": timeline,
            "segment_plan": None,
            "generation_records": (*project.generation_records, provenance),
        }
    )
    validated = Wan2LabProject.model_validate(updated.model_dump())
    affected = [
        item.segment_id
        for item in validated.segments
        if item.start_ms < proposal.time_ms < item.end_ms
        or item.segment_id == proposal.segment_id
    ]
    return Wan2LabProject.model_validate(
        invalidate_segments(
            validated,
            affected,
            reason="user-approved identity checkpoint requires segment replanning",
        ).model_dump()
    )


__all__ = [
    "apply_approved_checkpoint",
    "approve_registered_checkpoint",
    "confirm_warning_association",
    "propose_checkpoint_from_warnings",
    "register_identity_analysis",
]
