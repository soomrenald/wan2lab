from __future__ import annotations

import unittest

from wan2core.identity import CheckpointProposal, approve_checkpoint_proposal


class IdentityReviewTests(unittest.TestCase):
    def test_checkpoint_proposal_never_mutates_without_explicit_approval(self) -> None:
        proposal = CheckpointProposal(
            proposal_id="proposal-1",
            segment_id="segment-1",
            time_ms=2500,
            reason="sustained identity drift",
            warning_ids=("warning-1",),
        )
        self.assertFalse(proposal.user_approved)
        approved = approve_checkpoint_proposal(proposal)
        self.assertTrue(approved.user_approved)
        self.assertFalse(proposal.user_approved)


if __name__ == "__main__":
    unittest.main()

