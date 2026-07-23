from __future__ import annotations

import unittest
from datetime import UTC, datetime

from pydantic import ValidationError

from wan2core.assets import AssetKind, AssetRef
from wan2core.characters import (
    AdapterFamily,
    AdapterKind,
    AdapterRef,
    AppearanceProfile,
    CharacterIdentity,
    CharacterSheet,
    PoseViewEntry,
    PoseViewSource,
)
from wan2core.editing.faces import (
    DetectedFaceInput,
    FaceRefinementBatchPlan,
    confirm_face_proposal,
    propose_face_regions,
)
from wan2core.keyframes import (
    AdapterSelection,
    CharacterRegionAssignment,
    Keyframe,
    KeyframeSource,
    Rectangle,
)
from wan2core.keyframes.composition import (
    KeyframeCompositionRequest,
    compile_keyframe_composition,
)
from wan2core.keyframes.workflows import add_timeline_keyframe
from wan2core.projects import ProjectSettings, Wan2LabProject
from wan2core.provenance import ProvenanceRecord
from wan2core.timeline import Timeline


def character_data(index: int, x0: int):
    identity_id = f"character-{index}"
    appearance_id = f"appearance-{index}"
    entry_id = f"entry-{index}"
    identity_adapter = AdapterRef(
        adapter_id=f"identity-adapter-{index}",
        asset_id=f"identity-lora-{index}",
        family=AdapterFamily.KREA,
        kind=AdapterKind.LORA,
        model_family="krea2",
        trigger=f"person{index}",
    )
    style_adapter = AdapterRef(
        adapter_id=f"style-adapter-{index}",
        asset_id=f"style-lora-{index}",
        family=AdapterFamily.KREA,
        kind=AdapterKind.LOKR,
        model_family="krea2",
    )
    identity = CharacterIdentity(
        identity_id=identity_id,
        name=f"Character {index}",
        identity_prompt=f"person{index}, stable face {index}",
        stable_description=f"person {index}",
        adapter_refs=(identity_adapter,),
    )
    appearance = AppearanceProfile(
        appearance_id=appearance_id,
        identity_id=identity_id,
        name=f"Look {index}",
        style_prompt=f"wardrobe {index}",
        adapter_refs=(style_adapter,),
    )
    entry = PoseViewEntry(
        entry_id=entry_id,
        name="front_neutral_full",
        image_asset_id=f"pose-image-{index}",
        identity_id=identity_id,
        appearance_id=appearance_id,
        source_type=PoseViewSource.IMPORTED,
        provenance_id=f"pose-prov-{index}",
    )
    sheet = CharacterSheet(
        sheet_id=f"sheet-{index}",
        name=f"Sheet {index}",
        identity_id=identity_id,
        appearance_id=appearance_id,
        entries=(entry,),
    )
    assignment = CharacterRegionAssignment(
        region_id=f"region-{index}",
        name=f"Character {index}",
        rectangle=Rectangle(x0=x0, y0=100, x1=x0 + 300, y1=650),
        identity_id=identity_id,
        appearance_id=appearance_id,
        pose_view_entry_id=entry_id,
        prompt=f"pose {index}",
        adapters=(
            AdapterSelection(adapter_id=identity_adapter.adapter_id, strength=1.0),
            AdapterSelection(adapter_id=style_adapter.adapter_id, strength=0.7),
        ),
        priority=10 - index,
    )
    return identity, appearance, sheet, assignment


def composition_project() -> tuple[Wan2LabProject, tuple[CharacterRegionAssignment, ...]]:
    first = character_data(1, 50)
    second = character_data(2, 650)
    project = Wan2LabProject(
        project_id="project-1",
        project_settings=ProjectSettings(
            width=1280,
            height=720,
            default_wan_backend_id="mock-wan",
            default_wan_model_id="wan-test",
        ),
        characters=(first[0], second[0]),
        appearance_profiles=(first[1], second[1]),
        character_sheets=(first[2], second[2]),
        timeline=Timeline(duration_ms=18_000, output_fps=24),
    )
    return project, (first[3], second[3])


class KeyframeWorkflowTests(unittest.TestCase):
    def test_multi_character_composition_uses_exact_k2_backends(self) -> None:
        project, assignments = composition_project()
        request = KeyframeCompositionRequest(
            width=1280,
            height=720,
            scene_prompt="two people meet",
            environment_prompt="garden path",
            lighting_prompt="warm evening light",
            region_assignments=assignments,
        )
        plan = compile_keyframe_composition(project, request)
        self.assertEqual(plan.prompt_backend, "krea-unified-spatial-attention-v6")
        self.assertEqual(plan.adapter_backend, "krea-regional-lora-delta-gating-v3")
        self.assertEqual(len(plan.regions), 2)
        self.assertEqual(len(plan.adapter_routes), 4)
        self.assertIn("person1", plan.unified_prompt)
        self.assertIn("person2", plan.unified_prompt)
        self.assertEqual(
            {item.pose_reference_asset_id for item in plan.regions},
            {"pose-image-1", "pose-image-2"},
        )

    def test_face_proposals_use_k2_assignment_and_require_confirmation(self) -> None:
        project, assignments = composition_project()
        request = KeyframeCompositionRequest(
            width=1280,
            height=720,
            region_assignments=assignments,
        )
        plan = compile_keyframe_composition(project, request)
        proposals = propose_face_regions(
            frame_index=4,
            detections=(
                DetectedFaceInput(box=Rectangle(x0=120, y0=150, x1=200, y1=240), score=0.9),
                DetectedFaceInput(box=Rectangle(x0=730, y0=150, x1=810, y1=240), score=0.8),
            ),
            request=request,
            composition=plan,
        )
        self.assertEqual([item.identity_id for item in proposals], ["character-1", "character-2"])
        with self.assertRaises(ValidationError):
            FaceRefinementBatchPlan(identity_id="character-1", proposals=(proposals[0],))
        confirmed = confirm_face_proposal(
            proposals[0],
            manual_box=Rectangle(x0=125, y0=155, x1=205, y1=245),
        )
        batch = FaceRefinementBatchPlan(identity_id="character-1", proposals=(confirmed,))
        self.assertTrue(batch.proposals[0].confirmed)
        self.assertTrue(batch.proposals[0].manually_corrected)

    def test_imported_keyframe_is_registered_at_exact_time(self) -> None:
        project, _assignments = composition_project()
        asset = AssetRef(
            asset_id="keyframe-image",
            kind=AssetKind.IMAGE,
            storage_path="assets/keyframe.png",
            sha256="c" * 64,
            width=1280,
            height=720,
        )
        provenance = ProvenanceRecord(
            provenance_id="keyframe-prov",
            operation="import_keyframe",
            created_at=datetime(2026, 7, 22, tzinfo=UTC),
            output_asset_ids=(asset.asset_id,),
        )
        keyframe = Keyframe(
            keyframe_id="keyframe-1",
            time_ms=3_000,
            image_asset_id=asset.asset_id,
            source_type=KeyframeSource.IMPORTED,
            provenance_id=provenance.provenance_id,
            approved=True,
            locked=True,
        )
        updated = add_timeline_keyframe(
            project,
            keyframe=keyframe,
            asset=asset,
            provenance=provenance,
        )
        self.assertEqual(updated.timeline.keyframe_ids, ("keyframe-1",))
        self.assertEqual(updated.keyframes[0].time_ms, 3_000)


if __name__ == "__main__":
    unittest.main()

