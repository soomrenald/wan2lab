"""User-reviewed identity drift warnings and checkpoint proposals."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from wan2core.base import DomainModel, Identifier, Milliseconds
from wan2core.keyframes import Rectangle


class IdentityWarningKind(StrEnum):
    UNCERTAIN = "uncertain"
    MISSING = "missing"
    SWAPPED = "swapped"
    DRIFTING = "drifting"
    OCCLUSION = "occlusion"
    POSITION_EXCHANGE = "position_exchange"


class FaceAssociation(DomainModel):
    frame_index: int = Field(ge=0)
    identity_id: Identifier
    region: Rectangle
    confidence: float = Field(ge=0.0, le=1.0)
    user_confirmed: bool = False


class IdentityDriftWarning(DomainModel):
    warning_id: Identifier
    segment_revision_id: Identifier
    frame_index: int = Field(ge=0)
    identity_id: Identifier
    kind: IdentityWarningKind
    score: float | None = Field(default=None, ge=0.0, le=1.0)
    message: str = Field(min_length=1)
    proposed_region: Rectangle | None = None
    association_confirmed: bool = False


class CheckpointProposal(DomainModel):
    proposal_id: Identifier
    segment_id: Identifier
    time_ms: Milliseconds
    reason: str = Field(min_length=1)
    warning_ids: tuple[Identifier, ...]
    propose_keyframe: bool = True
    propose_segment_split: bool = True
    user_approved: bool = False


def approve_checkpoint_proposal(proposal: CheckpointProposal) -> CheckpointProposal:
    """Explicit approval marker; this function does not mutate the timeline itself."""

    return proposal.model_copy(update={"user_approved": True})


__all__ = [
    "CheckpointProposal",
    "FaceAssociation",
    "IdentityDriftWarning",
    "IdentityWarningKind",
    "approve_checkpoint_proposal",
]

