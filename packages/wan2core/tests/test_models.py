from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime

from pydantic import ValidationError

from wan2core.assets import AssetKind, AssetRef
from wan2core.characters import (
    AdapterFamily,
    AdapterKind,
    AdapterRef,
    AppearanceProfile,
    ApprovalState,
    CharacterIdentity,
    CharacterSheet,
    PoseViewEntry,
    PoseViewSource,
    StyleDuplicationEntry,
    duplicate_sheet_style,
)
from wan2core.mannequin import Camera, MannequinInstance, MannequinScene, MannequinSource, Vector3
from wan2core.projects import (
    PROJECT_SCHEMA_VERSION,
    ProjectSettings,
    Wan2LabProject,
    load_project_document,
    migrate_project_data,
    project_document,
)
from wan2core.provenance import ProvenanceRecord
from wan2core.timeline import Timeline


class DomainModelTests(unittest.TestCase):
    def test_assets_are_immutable_and_cannot_escape_storage(self) -> None:
        asset = AssetRef(
            asset_id="image-1",
            kind=AssetKind.IMAGE,
            storage_path="assets/image.png",
            sha256="a" * 64,
            width=1280,
            height=720,
        )
        self.assertTrue(asset.immutable_source)
        with self.assertRaises(ValidationError):
            AssetRef(
                asset_id="bad",
                kind=AssetKind.IMAGE,
                storage_path="../image.png",
                sha256="a" * 64,
                width=1,
                height=1,
            )

    def test_identity_and_appearance_are_separate(self) -> None:
        identity = CharacterIdentity(
            identity_id="character-1",
            name="Character",
            identity_prompt="same stable person",
        )
        red = AppearanceProfile(
            appearance_id="look-red",
            identity_id=identity.identity_id,
            name="Red dress",
            clothing_state="red dress",
        )
        swim = AppearanceProfile(
            appearance_id="look-swim",
            identity_id=identity.identity_id,
            name="Swimwear",
            clothing_state="blue swimwear",
        )
        self.assertEqual(red.identity_id, swim.identity_id)
        self.assertNotEqual(red.appearance_id, swim.appearance_id)

    def test_adapter_family_must_match_declared_model_family(self) -> None:
        with self.assertRaises(ValidationError):
            AdapterRef(
                adapter_id="adapter-1",
                asset_id="adapter-asset",
                family=AdapterFamily.KREA,
                kind=AdapterKind.LORA,
                model_family="wan2.2",
            )

    def test_style_duplication_preserves_pose_and_parentage(self) -> None:
        entry = PoseViewEntry(
            entry_id="entry-red",
            name="front_neutral_full",
            image_asset_id="asset-red",
            identity_id="character-1",
            appearance_id="look-red",
            view_label="front",
            pose_label="neutral",
            framing_label="full",
            mannequin_scene_id="pose-1",
            source_type=PoseViewSource.GENERATED,
            provenance_id="prov-red",
            approval_state=ApprovalState.APPROVED,
        )
        source = CharacterSheet(
            sheet_id="sheet-red",
            name="Red dress",
            identity_id="character-1",
            appearance_id="look-red",
            entries=(entry,),
        )
        result = duplicate_sheet_style(
            source,
            target_sheet_id="sheet-swim",
            target_name="Swimwear",
            target_appearance_id="look-swim",
            replacements=(
                StyleDuplicationEntry(
                    source_entry_id="entry-red",
                    target_entry_id="entry-swim",
                    target_asset_id="asset-swim",
                    provenance_id="prov-swim",
                ),
            ),
        )
        copied = result.entries[0]
        self.assertEqual(copied.parent_entry_id, entry.entry_id)
        self.assertEqual(copied.mannequin_scene_id, entry.mannequin_scene_id)
        self.assertEqual(copied.pose_label, entry.pose_label)
        self.assertEqual(copied.approval_state, ApprovalState.DRAFT)
        self.assertEqual(source.entries[0].image_asset_id, "asset-red")

    def test_blender_scene_requires_imported_source(self) -> None:
        camera = Camera(position=Vector3(z=5), frame_width=1280, frame_height=720)
        instance = MannequinInstance(
            instance_id="person-1",
            name="Person",
            skeleton_id="human",
        )
        with self.assertRaises(ValidationError):
            MannequinScene(
                scene_id="scene-1",
                name="Imported pose",
                instances=(instance,),
                camera=camera,
                source_type=MannequinSource.BLENDER,
            )

    def test_minimal_project_round_trips_without_field_loss(self) -> None:
        project = Wan2LabProject(
            project_id="project-1",
            project_settings=ProjectSettings(
                default_wan_backend_id="mock-wan",
                default_wan_model_id="wan-test",
            ),
            timeline=Timeline(duration_ms=18_000, output_fps=24.0),
            generation_records=(
                ProvenanceRecord(
                    provenance_id="project-created",
                    operation="create_project",
                    created_at=datetime(2026, 7, 22, tzinfo=UTC),
                ),
            ),
        )
        encoded = project_document(project)
        decoded = load_project_document(encoded)
        self.assertEqual(decoded, project)
        self.assertEqual(decoded.timeline.duration_ms, 18_000)

    def test_schema_one_project_migrates_without_mutating_source_data(self) -> None:
        legacy = {
            "schema_version": 1,
            "project_id": "legacy-project",
            "project_settings": {
                "default_wan_backend_id": "mock-wan",
                "default_wan_model_id": "wan-test",
            },
            "timeline": {"duration_ms": 5_000, "output_fps": 24.0},
        }

        migrated = migrate_project_data(legacy)
        project = load_project_document(json.dumps(legacy))

        self.assertEqual(legacy["schema_version"], 1)
        self.assertNotIn("mannequin_poses", legacy)
        self.assertEqual(migrated["schema_version"], PROJECT_SCHEMA_VERSION)
        self.assertEqual(migrated["mannequin_poses"], [])
        self.assertEqual(project.schema_version, PROJECT_SCHEMA_VERSION)
        self.assertEqual(project.mannequin_poses, ())
        self.assertIsNone(project.segment_plan)

    def test_project_loader_rejects_future_schema(self) -> None:
        document = (
            '{"schema_version": 999, "project_id": "future", '
            '"project_settings": {"default_wan_backend_id": "mock-wan", '
            '"default_wan_model_id": "wan-test"}, '
            '"timeline": {"duration_ms": 5000, "output_fps": 24}}'
        )

        with self.assertRaisesRegex(ValueError, "newer than supported"):
            load_project_document(document)

    def test_asset_creation_operation_must_claim_the_asset(self) -> None:
        asset = AssetRef(
            asset_id="derived-image",
            kind=AssetKind.IMAGE,
            storage_path="objects/derived.png",
            sha256="f" * 64,
            width=1280,
            height=720,
            creation_operation_id="missing-operation",
            immutable_source=False,
        )

        with self.assertRaisesRegex(
            ValidationError,
            "asset creation operation references missing provenance",
        ):
            Wan2LabProject(
                project_id="invalid-lineage",
                project_settings=ProjectSettings(
                    default_wan_backend_id="mock-wan",
                    default_wan_model_id="wan-test",
                ),
                assets=(asset,),
                timeline=Timeline(duration_ms=1_000, output_fps=24.0),
            )


if __name__ == "__main__":
    unittest.main()
