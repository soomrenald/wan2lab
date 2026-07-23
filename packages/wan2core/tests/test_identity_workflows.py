from __future__ import annotations

import unittest
from datetime import UTC, datetime

from wan2core.characters import CharacterIdentity
from wan2core.identity import IdentityDriftWarning, IdentityWarningKind
from wan2core.identity.workflows import (
    apply_approved_checkpoint,
    approve_registered_checkpoint,
    propose_checkpoint_from_warnings,
    register_identity_analysis,
)
from wan2core.projects import Wan2LabProject
from wan2core.provenance import ProvenanceRecord
from wan2core.segments import SegmentState

from test_frame_workflows import source_project


class IdentityWorkflowTests(unittest.TestCase):
    def test_checkpoint_requires_explicit_approval_then_invalidates_for_replan(self) -> None:
        base = source_project()
        project = Wan2LabProject.model_validate(
            base.model_copy(
                update={
                    "characters": (
                        CharacterIdentity(
                            identity_id="character-1",
                            name="Avery",
                            identity_prompt="stable Avery identity",
                        ),
                    )
                }
            ).model_dump()
        )
        warnings = (
            IdentityDriftWarning(
                warning_id="warning-1",
                segment_revision_id="revision-1",
                frame_index=2,
                identity_id="character-1",
                kind=IdentityWarningKind.DRIFTING,
                score=0.7,
                message="Face similarity dropped",
            ),
            IdentityDriftWarning(
                warning_id="warning-2",
                segment_revision_id="revision-1",
                frame_index=3,
                identity_id="character-1",
                kind=IdentityWarningKind.UNCERTAIN,
                score=0.5,
                message="Association needs review",
            ),
        )
        proposal = propose_checkpoint_from_warnings(
            proposal_id="proposal-1",
            segment_id="segment-1",
            segment_start_ms=0,
            segment_end_ms=250,
            generation_fps=16,
            warnings=warnings,
        )
        project = register_identity_analysis(
            project, warnings=warnings, proposal=proposal
        )
        provenance = ProvenanceRecord(
            provenance_id="checkpoint-provenance",
            operation="link_identity_checkpoint",
            created_at=datetime(2026, 7, 22, tzinfo=UTC),
            input_asset_ids=("frame-3",),
        )
        with self.assertRaisesRegex(ValueError, "explicit user approval"):
            apply_approved_checkpoint(
                project,
                proposal_id="proposal-1",
                keyframe_id="checkpoint-1",
                source_frame_asset_id="frame-3",
                provenance=provenance,
            )
        project = approve_registered_checkpoint(project, "proposal-1")
        updated = apply_approved_checkpoint(
            project,
            proposal_id="proposal-1",
            keyframe_id="checkpoint-1",
            source_frame_asset_id="frame-3",
            provenance=provenance,
        )
        self.assertTrue(updated.checkpoint_proposals[0].user_approved)
        self.assertEqual(updated.keyframes[0].time_ms, 188)
        self.assertEqual(updated.segments[0].state, SegmentState.STALE)
        self.assertIn("replanning", updated.segments[0].stale_reason)


if __name__ == "__main__":
    unittest.main()
