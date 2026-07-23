"""Shared application orchestration over immutable Wan2Lab domain records."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Callable

from wan2core.assets import AssetKind, AssetRef
from wan2core.backends import BackendCapabilities
from wan2core.backends.mock import CancellationToken, MockWanBackend
from wan2core.projects import Wan2LabProject
from wan2core.provenance import ProvenanceRecord
from wan2core.review import (
    approve_revision,
    complete_generation,
    finish_generation_failure,
    queue_revision,
    reject_revision,
    start_generation,
)
from wan2core.segments import Segment, SegmentRequest, SegmentRevision, SegmentState
from wan2core.timeline import SegmentPlan, plan_segments
from wan2core.workers import WorkerProgress, WorkerResult


class ReviewGateBlocked(RuntimeError):
    pass


class WanStudioSession:
    """UI-neutral project session used by desktop and future server controllers."""

    def __init__(self, project: Wan2LabProject) -> None:
        self.project = project
        self.segment_plan: SegmentPlan | None = project.segment_plan

    def plan(self, capabilities: BackendCapabilities, *, model_id: str) -> SegmentPlan:
        plan = plan_segments(
            self.project.timeline,
            self.project.keyframes,
            capabilities,
            model_id=model_id,
            default_segment_budget_ms=self.project.project_settings.default_segment_duration_ms,
            continuation_policy=self.project.project_settings.default_continuation_policy,
        )
        reserved_segment_ids = {
            revision.segment_id for revision in self.project.segment_revisions
        }
        allocated_segment_ids = set(reserved_segment_ids)
        remapped_planned_segments = []
        for planned in plan.segments:
            segment_id = planned.segment_id
            suffix = 2
            while segment_id in allocated_segment_ids:
                segment_id = f"{planned.segment_id}-plan-{suffix}"
                suffix += 1
            allocated_segment_ids.add(segment_id)
            remapped_planned_segments.append(
                planned.model_copy(update={"segment_id": segment_id})
            )
        plan = plan.model_copy(update={"segments": tuple(remapped_planned_segments)})
        segments = tuple(
            Segment(
                segment_id=item.segment_id,
                start_ms=item.start_ms,
                end_ms=item.end_ms,
                start_keyframe_id=item.start_keyframe_id,
                end_keyframe_id=item.end_keyframe_id,
                mode=item.mode,
                backend_id=item.backend_id,
                model_id=item.model_id,
                generation_fps=item.generation_fps,
                frame_count=item.frame_count,
                continuation_policy=item.continuation_policy,
            )
            for item in plan.segments
        )
        timeline = self.project.timeline.model_copy(
            update={"segment_ids": tuple(item.segment_id for item in segments)}
        )
        self.project = self._validated(
            self.project.model_copy(
                update={
                    "segments": segments,
                    "segment_plan": plan,
                    "timeline": timeline,
                }
            )
        )
        self.segment_plan = plan
        return plan

    def generate_next_with_mock(
        self,
        backend: MockWanBackend,
        *,
        seed: int,
        progress: Callable[[WorkerProgress], None],
        cancellation: CancellationToken | None = None,
    ) -> SegmentRevision:
        if self.segment_plan is None:
            raise RuntimeError("timeline must be planned before generation")
        blocking = next(
            (
                segment
                for segment in self.project.segments
                if segment.state
                in {
                    SegmentState.QUEUED,
                    SegmentState.GENERATING,
                    SegmentState.READY_FOR_REVIEW,
                    SegmentState.MODIFYING,
                    SegmentState.REJECTED,
                    SegmentState.ERROR,
                    SegmentState.CANCELLED,
                }
            ),
            None,
        )
        if blocking is not None:
            raise ReviewGateBlocked(
                f"segment {blocking.segment_id} requires review before downstream generation"
            )
        segment = next(
            (segment for segment in self.project.segments if segment.state is SegmentState.DRAFT),
            None,
        )
        if segment is None:
            raise StopIteration("no draft segment remains")
        planned = next(item for item in self.segment_plan.segments if item.segment_id == segment.segment_id)
        return self._generate_mock_revision(
            segment,
            planned,
            backend,
            seed=seed,
            progress=progress,
            cancellation=cancellation,
        )

    def queue_next_generation(self, *, seed: int) -> tuple[str, SegmentRevision]:
        if self.segment_plan is None:
            raise RuntimeError("timeline must be planned before generation")
        blocking = next(
            (
                segment
                for segment in self.project.segments
                if segment.state
                in {
                    SegmentState.QUEUED,
                    SegmentState.GENERATING,
                    SegmentState.READY_FOR_REVIEW,
                    SegmentState.MODIFYING,
                    SegmentState.REJECTED,
                }
            ),
            None,
        )
        if blocking is not None:
            raise ReviewGateBlocked(
                f"segment {blocking.segment_id} requires review before downstream generation"
            )
        segment = next(
            (item for item in self.project.segments if item.state is SegmentState.DRAFT),
            None,
        )
        if segment is None:
            raise StopIteration("no draft segment remains")
        return self._queue_external_revision(segment, seed=seed)

    def queue_rejected_generation(self, *, seed: int) -> tuple[str, SegmentRevision]:
        if self.segment_plan is None:
            raise RuntimeError("timeline must be planned before generation")
        segment = next(
            (
                item
                for item in self.project.segments
                if item.state
                in {
                    SegmentState.REJECTED,
                    SegmentState.ERROR,
                    SegmentState.CANCELLED,
                    SegmentState.STALE,
                }
            ),
            None,
        )
        if segment is None:
            raise ReviewGateBlocked(
                "no rejected, failed, or stale segment is ready to regenerate"
            )
        return self._queue_external_revision(
            segment,
            seed=seed,
            parent_revision_id=self._latest_revision(segment).revision_id,
        )

    def complete_worker_generation(
        self,
        *,
        revision_id: str,
        result: WorkerResult,
        result_asset: AssetRef,
        frame_assets: tuple[AssetRef, ...] = (),
        backend_version: str | None = None,
    ) -> SegmentRevision:
        revision = next(
            (item for item in self.project.segment_revisions if item.revision_id == revision_id),
            None,
        )
        if revision is None:
            raise KeyError(f"unknown revision: {revision_id}")
        segment = next(
            item for item in self.project.segments if item.segment_id == revision.segment_id
        )
        if result.result_asset_id != result_asset.asset_id:
            raise ValueError("worker result and registered result asset differ")
        if result.frame_asset_ids != tuple(item.asset_id for item in frame_assets):
            raise ValueError("worker frame IDs and registered frame assets differ")
        provenance_id = f"{revision.revision_id}-provenance"
        segment, revision = complete_generation(
            segment,
            revision,
            result_asset_id=result_asset.asset_id,
            frame_asset_ids=result.frame_asset_ids,
            start_frame_asset_id=(frame_assets[0].asset_id if frame_assets else None),
            end_frame_asset_id=(frame_assets[-1].asset_id if frame_assets else None),
            resolved_parameters=dict(result.metadata.get("resolved_parameters", {})),
            generation_metadata=result.metadata,
            provenance_id=provenance_id,
        )
        request = revision.source_request
        provenance = ProvenanceRecord(
            provenance_id=provenance_id,
            operation="generate_segment",
            created_at=datetime.now(UTC),
            model_identifiers=tuple(
                item
                for item in (
                    request.model_id,
                    str(result.metadata.get("model_filename", "")),
                    str(result.metadata.get("vae_filename", "")),
                    str(result.metadata.get("text_encoder_filename", "")),
                )
                if item
            ),
            backend_id=request.backend_id,
            backend_version=backend_version or "",
            parameters={
                **revision.resolved_parameters,
                "mode": request.mode.value,
                "generation_fps": request.generation_fps,
                "frame_count": request.frame_count,
                "frame_rounding": request.frame_rounding.value,
                "resolution": {"width": request.width, "height": request.height},
                "action_spec": (
                    request.action_spec.model_dump(mode="json")
                    if request.action_spec is not None
                    else None
                ),
                "character_identity_ids": request.character_identity_ids,
            },
            prompts={
                "prompt": request.prompt,
                "negative_prompt": request.negative_prompt,
            },
            seed=revision.seed,
            input_asset_ids=tuple(
                item
                for item in (
                    request.start_image_asset_id,
                    request.end_image_asset_id,
                    request.reference_character_asset_id,
                    request.driving_video_asset_id,
                    request.source_video_asset_id,
                    request.mask_asset_id,
                )
                if item is not None
            ),
            output_asset_ids=(result_asset.asset_id, *result.frame_asset_ids),
            runtime={
                key: value
                for key, value in result.metadata.items()
                if key
                in {
                    "prompt_id",
                    "template_id",
                    "template_version",
                    "model_filename",
                    "vae_filename",
                    "text_encoder_filename",
                    "precision",
                    "vae_precision",
                    "text_encoder_precision",
                    "quantization",
                    "load_device",
                    "accelerator_vendors",
                    "device",
                }
            },
        )
        self.project = self.project.model_copy(
            update={
                "assets": (*self.project.assets, result_asset, *frame_assets),
                "generation_records": (*self.project.generation_records, provenance),
            }
        )
        self._replace_segment_and_revision(segment, revision)
        self.project = self._validated(self.project)
        return revision

    def fail_worker_generation(
        self,
        *,
        revision_id: str,
        message: str,
        cancelled: bool = False,
    ) -> SegmentRevision:
        revision = next(
            (item for item in self.project.segment_revisions if item.revision_id == revision_id),
            None,
        )
        if revision is None:
            raise KeyError(f"unknown revision: {revision_id}")
        segment = next(
            item for item in self.project.segments if item.segment_id == revision.segment_id
        )
        segment, revision = finish_generation_failure(
            segment,
            revision,
            message=message,
            cancelled=cancelled,
        )
        self._replace_segment_and_revision(segment, revision)
        self.project = self._validated(self.project)
        return revision

    def _queue_external_revision(
        self,
        segment: Segment,
        *,
        seed: int,
        parent_revision_id: str | None = None,
    ) -> tuple[str, SegmentRevision]:
        assert self.segment_plan is not None
        planned = next(
            item for item in self.segment_plan.segments if item.segment_id == segment.segment_id
        )
        request = self._request_for(segment, planned)
        segment, revision = queue_revision(
            segment,
            revision_id=f"{segment.segment_id}-revision-{len(segment.revision_ids) + 1}",
            request=request,
            seed=seed,
            parent_revision_id=parent_revision_id,
        )
        segment, revision = start_generation(segment, revision)
        self._replace_segment_and_revision(segment, revision)
        self.project = self._validated(self.project)
        return f"{segment.segment_id}-job-{revision.revision_number}", revision

    def regenerate_rejected_with_mock(
        self,
        backend: MockWanBackend,
        *,
        seed: int,
        progress: Callable[[WorkerProgress], None],
        cancellation: CancellationToken | None = None,
    ) -> SegmentRevision:
        if self.segment_plan is None:
            raise RuntimeError("timeline must be planned before generation")
        segment = next(
            (
                item
                for item in self.project.segments
                if item.state
                in {
                    SegmentState.REJECTED,
                    SegmentState.ERROR,
                    SegmentState.CANCELLED,
                    SegmentState.STALE,
                }
            ),
            None,
        )
        if segment is None:
            raise ReviewGateBlocked(
                "no rejected, failed, or stale segment is ready to regenerate"
            )
        parent = self._latest_revision(segment)
        planned = next(item for item in self.segment_plan.segments if item.segment_id == segment.segment_id)
        return self._generate_mock_revision(
            segment,
            planned,
            backend,
            seed=seed,
            progress=progress,
            cancellation=cancellation,
            parent_revision_id=parent.revision_id,
        )

    def _generate_mock_revision(
        self,
        segment: Segment,
        planned,
        backend: MockWanBackend,
        *,
        seed: int,
        progress: Callable[[WorkerProgress], None],
        cancellation: CancellationToken | None,
        parent_revision_id: str | None = None,
    ) -> SegmentRevision:
        request = self._request_for(segment, planned)
        segment, revision = queue_revision(
            segment,
            revision_id=f"{segment.segment_id}-revision-{len(segment.revision_ids) + 1}",
            request=request,
            seed=seed,
            parent_revision_id=parent_revision_id,
        )
        segment, revision = start_generation(segment, revision)
        self._replace_segment_and_revision(segment, revision)
        backend.load_model(segment.model_id)
        token = cancellation or CancellationToken.create()
        result = backend.generate_segment(
            request,
            job_id=f"{segment.segment_id}-job-{revision.revision_number}",
            progress=progress,
            cancellation=token,
        )
        first_frame = result.frame_asset_ids[0] if result.frame_asset_ids else None
        last_frame = result.frame_asset_ids[-1] if result.frame_asset_ids else None
        provenance_id = f"{revision.revision_id}-provenance"
        segment, revision = complete_generation(
            segment,
            revision,
            result_asset_id=result.result_asset_id,
            frame_asset_ids=result.frame_asset_ids,
            start_frame_asset_id=first_frame,
            end_frame_asset_id=last_frame,
            generation_metadata=result.metadata,
            provenance_id=provenance_id,
        )
        assets = self._mock_assets(result, request)
        provenance = ProvenanceRecord(
            provenance_id=provenance_id,
            operation="mock_generate_segment",
            created_at=datetime.now(UTC),
            model_identifiers=(request.model_id,),
            backend_id=request.backend_id,
            backend_version=backend.capabilities().backend_version,
            parameters={
                **request.parameters,
                "mode": request.mode.value,
                "generation_fps": request.generation_fps,
                "frame_count": request.frame_count,
                "frame_rounding": request.frame_rounding.value,
                "resolution": {"width": request.width, "height": request.height},
                "action_spec": (
                    request.action_spec.model_dump(mode="json")
                    if request.action_spec is not None
                    else None
                ),
                "character_identity_ids": request.character_identity_ids,
            },
            prompts={
                "prompt": request.prompt,
                "negative_prompt": request.negative_prompt,
            },
            seed=seed,
            input_asset_ids=tuple(
                asset_id
                for asset_id in (
                    request.start_image_asset_id,
                    request.end_image_asset_id,
                    request.reference_character_asset_id,
                    request.driving_video_asset_id,
                    request.source_video_asset_id,
                    request.mask_asset_id,
                )
                if asset_id is not None
            ),
            output_asset_ids=(result.result_asset_id, *result.frame_asset_ids),
            runtime={"mock": True},
        )
        self.project = self.project.model_copy(
            update={
                "assets": (*self.project.assets, *assets),
                "generation_records": (*self.project.generation_records, provenance),
            }
        )
        self._replace_segment_and_revision(segment, revision)
        self.project = self._validated(self.project)
        return revision

    def approve_current(self) -> SegmentRevision:
        segment = next(
            (
                segment
                for segment in self.project.segments
                if segment.state is SegmentState.READY_FOR_REVIEW
            ),
            None,
        )
        if segment is None:
            raise ReviewGateBlocked("no segment is ready for review")
        revision = self._latest_revision(segment)
        segment, revision = approve_revision(segment, revision)
        self._replace_segment_and_revision(segment, revision)
        self.project = self._validated(self.project)
        return revision

    def reject_current(self, reason: str) -> SegmentRevision:
        segment = next(
            (
                segment
                for segment in self.project.segments
                if segment.state is SegmentState.READY_FOR_REVIEW
            ),
            None,
        )
        if segment is None:
            raise ReviewGateBlocked("no segment is ready for review")
        revision = self._latest_revision(segment)
        segment, revision = reject_revision(segment, revision, reason=reason)
        self._replace_segment_and_revision(segment, revision)
        self.project = self._validated(self.project)
        return revision

    def _request_for(self, segment: Segment, planned) -> SegmentRequest:
        keyframes = {item.keyframe_id: item for item in self.project.keyframes}
        action = next(
            (
                item
                for item in self.project.actions
                if item.action_id == segment.action_spec_id
            ),
            None,
        )
        start_asset = segment.start_image_asset_id or (
            keyframes[segment.start_keyframe_id].image_asset_id
            if segment.start_keyframe_id is not None
            else self._previous_boundary_asset(segment)
        )
        end_asset = segment.end_image_asset_id or (
            keyframes[segment.end_keyframe_id].image_asset_id
            if segment.end_keyframe_id is not None
            else None
        )
        return SegmentRequest(
            request_id=f"{segment.segment_id}-request-{len(segment.revision_ids) + 1}",
            segment_id=segment.segment_id,
            mode=segment.mode,
            backend_id=segment.backend_id,
            model_id=segment.model_id,
            start_ms=segment.start_ms,
            end_ms=segment.end_ms,
            width=self.project.project_settings.width,
            height=self.project.project_settings.height,
            generation_fps=segment.generation_fps or planned.generation_fps,
            frame_count=segment.frame_count or planned.frame_count,
            frame_rounding=segment.frame_rounding,
            start_image_asset_id=start_asset,
            end_image_asset_id=end_asset,
            reference_character_asset_id=segment.reference_character_asset_id,
            driving_video_asset_id=segment.driving_video_asset_id,
            source_video_asset_id=segment.source_video_asset_id,
            mask_asset_id=segment.mask_asset_id,
            prompt=segment.prompt,
            negative_prompt=segment.negative_prompt,
            action_spec_id=segment.action_spec_id,
            action_spec=action,
            character_identity_ids=segment.character_identity_ids,
            parameters=segment.parameters,
        )

    def _previous_boundary_asset(self, segment: Segment) -> str | None:
        previous = next(
            (
                candidate
                for candidate in self.project.segments
                if candidate.end_ms == segment.start_ms
            ),
            None,
        )
        if previous is None or previous.current_approved_revision_id is None:
            return None
        revision = next(
            item
            for item in self.project.segment_revisions
            if item.revision_id == previous.current_approved_revision_id
        )
        return revision.end_frame_asset_id

    def _latest_revision(self, segment: Segment) -> SegmentRevision:
        revision_id = segment.revision_ids[-1]
        return next(item for item in self.project.segment_revisions if item.revision_id == revision_id)

    def _replace_segment_and_revision(
        self, segment: Segment, revision: SegmentRevision
    ) -> None:
        segments = tuple(
            segment if item.segment_id == segment.segment_id else item
            for item in self.project.segments
        )
        revisions = tuple(
            item
            for item in self.project.segment_revisions
            if item.revision_id != revision.revision_id
        ) + (revision,)
        self.project = self.project.model_copy(
            update={"segments": segments, "segment_revisions": revisions}
        )

    @staticmethod
    def _mock_assets(result, request: SegmentRequest) -> tuple[AssetRef, ...]:
        def digest(asset_id: str) -> str:
            return hashlib.sha256(asset_id.encode("utf-8")).hexdigest()

        video = AssetRef(
            asset_id=result.result_asset_id,
            kind=AssetKind.VIDEO,
            storage_path=f"mock/{result.result_asset_id}.mp4",
            sha256=digest(result.result_asset_id),
            width=request.width,
            height=request.height,
            frame_count=request.frame_count,
            duration_ms=request.end_ms - request.start_ms,
            creation_operation_id=request.request_id,
            immutable_source=False,
        )
        frames = tuple(
            AssetRef(
                asset_id=asset_id,
                kind=AssetKind.IMAGE,
                storage_path=f"mock/{asset_id}.png",
                sha256=digest(asset_id),
                width=request.width,
                height=request.height,
                parent_asset_ids=(result.result_asset_id,),
                creation_operation_id=request.request_id,
                immutable_source=False,
            )
            for asset_id in result.frame_asset_ids
        )
        return (video, *frames)

    @staticmethod
    def _validated(project: Wan2LabProject) -> Wan2LabProject:
        return Wan2LabProject.model_validate(project.model_dump())


__all__ = ["ReviewGateBlocked", "WanStudioSession"]
