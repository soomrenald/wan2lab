"""K2-backed face proposals with mandatory user confirmation."""

from __future__ import annotations

from pydantic import Field, model_validator

from k2core.face_detail import DetectedFace, assign_faces_to_regional_loras
from k2core.regions import PixelBox, RegionDefinition
from wan2core.base import DomainModel, Identifier
from wan2core.keyframes import Rectangle
from wan2core.keyframes.composition import KeyframeCompositionPlan, KeyframeCompositionRequest


class DetectedFaceInput(DomainModel):
    box: Rectangle
    score: float = Field(ge=0.0, le=1.0)


class FaceProposal(DomainModel):
    proposal_id: Identifier
    frame_index: int = Field(ge=0)
    identity_id: Identifier
    region_id: Identifier
    box: Rectangle
    score: float = Field(ge=0.0, le=1.0)
    prompt: str
    confirmed: bool = False
    manually_corrected: bool = False


class FaceRefinementBatchPlan(DomainModel):
    identity_id: Identifier
    proposals: tuple[FaceProposal, ...]

    @model_validator(mode="after")
    def validate_confirmations(self) -> "FaceRefinementBatchPlan":
        if not self.proposals:
            raise ValueError("face refinement batch requires proposals")
        if any(not item.confirmed for item in self.proposals):
            raise ValueError("every face region requires user confirmation")
        if any(item.identity_id != self.identity_id for item in self.proposals):
            raise ValueError("batch proposals must belong to the selected identity")
        return self


def propose_face_regions(
    *,
    frame_index: int,
    detections: tuple[DetectedFaceInput, ...],
    request: KeyframeCompositionRequest,
    composition: KeyframeCompositionPlan,
) -> tuple[FaceProposal, ...]:
    assignments = {item.region_id: item for item in request.region_assignments}
    regions = tuple(
        RegionDefinition(
            region_id=item.region_id,
            name=item.name,
            box=PixelBox(*item.box),
            prompt=item.prompt,
            face_identity_prompt=item.face_identity_prompt,
            negative_prompt=item.negative_prompt,
            priority=item.priority,
            spatial_role="subject",
        )
        for item in composition.regions
    )
    targets = assign_faces_to_regional_loras(
        (
            DetectedFace(
                PixelBox(
                    item.box.x0,
                    item.box.y0,
                    item.box.x1,
                    item.box.y1,
                ),
                item.score,
            )
            for item in detections
        ),
        regions,
        (route.to_k2_payload() for route in composition.adapter_routes),
    )
    return tuple(
        FaceProposal(
            proposal_id=f"face-{frame_index}-{index}",
            frame_index=frame_index,
            identity_id=assignments[target.region_id].identity_id,
            region_id=target.region_id,
            box=Rectangle(
                x0=target.face.box.x0,
                y0=target.face.box.y0,
                x1=target.face.box.x1,
                y1=target.face.box.y1,
            ),
            score=target.face.score,
            prompt=target.prompt,
        )
        for index, target in enumerate(targets)
    )


def confirm_face_proposal(
    proposal: FaceProposal,
    *,
    manual_box: Rectangle | None = None,
) -> FaceProposal:
    return proposal.model_copy(
        update={
            "box": manual_box or proposal.box,
            "confirmed": True,
            "manually_corrected": manual_box is not None,
        }
    )


__all__ = [
    "DetectedFaceInput",
    "FaceProposal",
    "FaceRefinementBatchPlan",
    "confirm_face_proposal",
    "propose_face_regions",
]
