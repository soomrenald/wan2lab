from __future__ import annotations

import unittest

from wan2core.backends import WanMode
from wan2core.backends.mock import MockWanBackend, default_mock_capabilities
from wan2core.orchestration import ReviewGateBlocked, WanStudioSession
from wan2core.projects import (
    ProjectSettings,
    Wan2LabProject,
    load_project_document,
    project_document,
)
from wan2core.assets import AssetKind, AssetRef
from wan2core.segments import RevisionReviewState, SegmentState
from wan2core.timeline import Timeline
from wan2core.workers import WorkerResult


class OrchestrationTests(unittest.TestCase):
    def test_planning_rejects_a_canvas_unsupported_by_the_selected_model(self) -> None:
        project = Wan2LabProject(
            project_id="project-resolution",
            project_settings=ProjectSettings(
                width=640,
                height=480,
                default_wan_backend_id="mock-wan",
                default_wan_model_id="wan-test",
            ),
            timeline=Timeline(duration_ms=5_000, output_fps=24.0),
        )

        with self.assertRaisesRegex(ValueError, "project canvas 640x480 is unsupported"):
            WanStudioSession(project).plan(
                default_mock_capabilities(),
                model_id="wan-test",
            )

    def test_generation_rejects_mismatched_boundary_dimensions_before_queueing(self) -> None:
        project = Wan2LabProject(
            project_id="project-boundary-size",
            project_settings=ProjectSettings(
                default_wan_backend_id="mock-wan",
                default_wan_model_id="wan-test",
            ),
            timeline=Timeline(duration_ms=5_000, output_fps=24.0),
        )
        session = WanStudioSession(project)
        session.plan(default_mock_capabilities(), model_id="wan-test")
        boundary = AssetRef(
            asset_id="small-boundary",
            kind=AssetKind.IMAGE,
            storage_path="objects/small.png",
            sha256="a" * 64,
            width=640,
            height=480,
        )
        segment = session.project.segments[0].model_copy(
            update={
                "start_image_asset_id": boundary.asset_id,
            }
        )
        session.project = Wan2LabProject.model_validate(
            session.project.model_copy(
                update={"assets": (boundary,), "segments": (segment,)}
            ).model_dump()
        )

        with self.assertRaisesRegex(ValueError, "resize or replace the boundary image"):
            session.queue_next_generation(seed=1)

        self.assertEqual(session.project.segments[0].state, SegmentState.DRAFT)
        self.assertEqual(session.project.segment_revisions, ())

    def test_persisted_segment_plan_resumes_generation_after_reload(self) -> None:
        project = Wan2LabProject(
            project_id="project-resume",
            project_settings=ProjectSettings(
                default_wan_backend_id="mock-wan",
                default_wan_model_id="wan-test",
            ),
            timeline=Timeline(duration_ms=5_000, output_fps=24.0),
        )
        session = WanStudioSession(project)
        session.plan(default_mock_capabilities(), model_id="wan-test")
        reloaded = WanStudioSession(load_project_document(project_document(session.project)))

        job_id, revision = reloaded.queue_next_generation(seed=3)

        self.assertEqual(job_id, "segment-1-job-1")
        self.assertEqual(revision.source_request.frame_count, 81)

    def test_replanning_preserves_revision_history_and_uses_fresh_segment_ids(self) -> None:
        project = Wan2LabProject(
            project_id="project-replan",
            project_settings=ProjectSettings(
                default_wan_backend_id="mock-wan",
                default_wan_model_id="wan-test",
            ),
            timeline=Timeline(duration_ms=5_000, output_fps=24.0),
        )
        session = WanStudioSession(project)
        session.plan(default_mock_capabilities(), model_id="wan-test")
        original = session.generate_next_with_mock(
            MockWanBackend(default_mock_capabilities()),
            seed=1,
            progress=lambda _event: None,
        )

        session.plan(default_mock_capabilities(), model_id="wan-test")

        self.assertEqual(session.project.segment_revisions, (original,))
        self.assertNotEqual(session.project.segments[0].segment_id, original.segment_id)
        self.assertFalse(session.project.segments[0].revision_ids)

    def test_external_worker_lifecycle_registers_assets_and_failure_state(self) -> None:
        project = Wan2LabProject(
            project_id="project-worker",
            project_settings=ProjectSettings(
                default_wan_backend_id="mock-wan",
                default_wan_model_id="wan-test",
            ),
            timeline=Timeline(duration_ms=8_000, output_fps=24.0),
        )
        session = WanStudioSession(project)
        session.plan(default_mock_capabilities(), model_id="wan-test")
        _job_id, revision = session.queue_next_generation(seed=9)
        result = WorkerResult(
            job_id="segment-1-job-1",
            result_asset_id="video-1",
            metadata={
                "resolved_parameters": {"steps": 20},
                "template_id": "wan-t2v",
                "template_version": "2",
                "model_filename": "wan.safetensors",
                "vae_filename": "wan-vae.safetensors",
                "text_encoder_filename": "umt5.safetensors",
                "precision": "bf16",
                "vae_precision": "fp16",
                "text_encoder_precision": "fp16",
                "quantization": "disabled",
                "load_device": "offload_device",
                "accelerator_vendors": ["cuda"],
                "device": {"name": "NVIDIA RTX", "type": "cuda"},
            },
        )
        video = AssetRef(
            asset_id="video-1",
            kind=AssetKind.VIDEO,
            storage_path="objects/video-1.mp4",
            sha256="1" * 64,
            width=1280,
            height=720,
            frame_count=revision.source_request.frame_count,
            duration_ms=revision.source_request.end_ms - revision.source_request.start_ms,
        )
        completed = session.complete_worker_generation(
            revision_id=revision.revision_id,
            result=result,
            result_asset=video,
            backend_version="test-worker",
        )
        self.assertEqual(completed.review_state, RevisionReviewState.READY_FOR_REVIEW)
        self.assertEqual(session.project.assets, (video,))
        provenance = session.project.generation_records[0]
        self.assertEqual(provenance.seed, 9)
        self.assertEqual(
            provenance.model_identifiers,
            (
                "wan-test",
                "wan.safetensors",
                "wan-vae.safetensors",
                "umt5.safetensors",
            ),
        )
        self.assertEqual(provenance.parameters["steps"], 20)
        self.assertEqual(provenance.parameters["generation_fps"], 16.0)
        self.assertEqual(provenance.parameters["frame_count"], 81)
        self.assertEqual(provenance.runtime["precision"], "bf16")
        self.assertEqual(provenance.runtime["accelerator_vendors"], ["cuda"])

        session.reject_current("retry")
        _job_id, retry = session.queue_rejected_generation(seed=10)
        failed = session.fail_worker_generation(
            revision_id=retry.revision_id,
            message="out of memory",
        )
        self.assertEqual(failed.review_state, RevisionReviewState.ERROR)
        self.assertEqual(session.project.segments[0].state, SegmentState.ERROR)
        self.assertEqual(failed.parent_revision_id, revision.revision_id)
        _job_id, retried = session.queue_rejected_generation(seed=11)
        self.assertEqual(retried.parent_revision_id, failed.revision_id)
        self.assertEqual(retried.review_state, RevisionReviewState.GENERATING)

    def test_mock_end_to_end_stops_at_every_review_gate(self) -> None:
        project = Wan2LabProject(
            project_id="project-1",
            project_settings=ProjectSettings(
                default_wan_backend_id="mock-wan",
                default_wan_model_id="wan-test",
            ),
            timeline=Timeline(duration_ms=11_000, output_fps=24.0),
        )
        capabilities = default_mock_capabilities()
        backend = MockWanBackend(capabilities)
        session = WanStudioSession(project)
        plan = session.plan(capabilities, model_id="wan-test")
        self.assertEqual(len(plan.segments), 3)

        progress = []
        first = session.generate_next_with_mock(backend, seed=1, progress=progress.append)
        self.assertEqual(first.source_request.mode, WanMode.PROMPT)
        with self.assertRaises(ReviewGateBlocked):
            session.generate_next_with_mock(backend, seed=2, progress=progress.append)

        session.approve_current()
        second = session.generate_next_with_mock(backend, seed=2, progress=progress.append)
        self.assertEqual(second.source_request.mode, WanMode.I2V)
        self.assertEqual(
            second.source_request.start_image_asset_id,
            first.end_frame_asset_id,
        )
        session.approve_current()
        session.generate_next_with_mock(backend, seed=3, progress=progress.append)
        session.approve_current()

        self.assertTrue(
            all(
                segment.state is SegmentState.APPROVED_LOCKED
                for segment in session.project.segments
            )
        )
        self.assertEqual(len(progress), 12)
        self.assertEqual(len(session.project.segment_revisions), 3)

    def test_reject_and_regenerate_preserves_old_revision_and_assets(self) -> None:
        project = Wan2LabProject(
            project_id="project-1",
            project_settings=ProjectSettings(
                default_wan_backend_id="mock-wan",
                default_wan_model_id="wan-test",
            ),
            timeline=Timeline(duration_ms=5_000, output_fps=24.0),
        )
        capabilities = default_mock_capabilities()
        backend = MockWanBackend(capabilities)
        session = WanStudioSession(project)
        session.plan(capabilities, model_id="wan-test")
        first = session.generate_next_with_mock(backend, seed=1, progress=lambda _event: None)
        rejected = session.reject_current("motion direction was wrong")
        self.assertEqual(rejected.revision_id, first.revision_id)
        old_asset_ids = {asset.asset_id for asset in session.project.assets}

        second = session.regenerate_rejected_with_mock(
            backend, seed=2, progress=lambda _event: None
        )
        self.assertEqual(second.revision_number, 2)
        self.assertEqual(second.parent_revision_id, first.revision_id)
        self.assertTrue(old_asset_ids.issubset({asset.asset_id for asset in session.project.assets}))
        self.assertEqual(session.project.segments[0].state, SegmentState.READY_FOR_REVIEW)


if __name__ == "__main__":
    unittest.main()
