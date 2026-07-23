from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from k2core.backends import BackendCapabilities, BackendResult
from wan2core.assets import AssetKind, AssetRef
from wan2core.characters import (
    AppearanceProfile,
    ApprovalState,
    CharacterIdentity,
    CharacterSheet,
    PoseViewEntry,
    PoseViewSource,
    StyleDuplicationEntry,
)
from wan2core.keyframes.generation import (
    CharacterSheetImageRequest,
    KreaImageService,
    RestyleEntryRequest,
)
from wan2core.keyframes.workflows import (
    register_style_duplication,
    remove_pose_view_entry,
    update_pose_view_entry,
)
from wan2core.projects import ProjectSettings, Wan2LabProject
from wan2core.provenance import ProvenanceRecord
from wan2core.timeline import Timeline


def source_project() -> Wan2LabProject:
    asset = AssetRef(
        asset_id="asset-source",
        kind=AssetKind.IMAGE,
        storage_path="objects/source.png",
        sha256="a" * 64,
        width=512,
        height=512,
    )
    provenance = ProvenanceRecord(
        provenance_id="provenance-source",
        operation="import",
        created_at=datetime(2026, 7, 22, tzinfo=UTC),
        output_asset_ids=(asset.asset_id,),
    )
    entry = PoseViewEntry(
        entry_id="entry-source",
        name="front_neutral_full",
        image_asset_id=asset.asset_id,
        identity_id="character-1",
        appearance_id="appearance-1",
        source_type=PoseViewSource.IMPORTED,
        provenance_id=provenance.provenance_id,
        approval_state=ApprovalState.APPROVED,
    )
    return Wan2LabProject(
        project_id="project-1",
        project_settings=ProjectSettings(
            default_wan_backend_id="mock-wan",
            default_wan_model_id="mock-model",
        ),
        assets=(asset,),
        characters=(
            CharacterIdentity(
                identity_id="character-1",
                name="Avery",
                identity_prompt="stable Avery identity",
                character_sheet_ids=("sheet-source",),
            ),
        ),
        appearance_profiles=(
            AppearanceProfile(
                appearance_id="appearance-1",
                identity_id="character-1",
                name="Red dress",
            ),
        ),
        character_sheets=(
            CharacterSheet(
                sheet_id="sheet-source",
                name="Avery — red dress",
                identity_id="character-1",
                appearance_id="appearance-1",
                entries=(entry,),
            ),
        ),
        generation_records=(provenance,),
        timeline=Timeline(duration_ms=5_000, output_fps=24),
    )


class FakeCancellation:
    cancelled = False

    def raise_if_cancelled(self) -> None:
        return None


class RecordingKreaBackend:
    backend_id = "recording-krea"

    def __init__(self, output: Path) -> None:
        self.output = output
        self.requests: list[dict[str, object]] = []

    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            backend_id=self.backend_id,
            modes=frozenset({"generate_image", "edit_image"}),
            accelerator_vendors=frozenset({"cpu"}),
        )

    def validate_image_request(self, request):
        return ()

    def generate_image(self, request, *, progress, cancellation):
        cancellation.raise_if_cancelled()
        self.requests.append(dict(request))
        progress("complete", 1.0, {})
        return BackendResult(asset_paths=(self.output,))

    def release(self) -> None:
        return None


class CharacterSheetOperationTests(unittest.TestCase):
    def test_review_rename_and_remove_preserve_asset_history(self) -> None:
        project = source_project()
        reviewed = update_pose_view_entry(
            project,
            sheet_id="sheet-source",
            entry_id="entry-source",
            name="front smiling",
            approval_state=ApprovalState.REJECTED,
        )
        self.assertEqual(reviewed.character_sheets[0].entries[0].name, "front smiling")
        removed = remove_pose_view_entry(
            reviewed,
            sheet_id="sheet-source",
            entry_id="entry-source",
        )
        self.assertEqual(removed.character_sheets[0].entries, ())
        self.assertEqual(removed.assets, project.assets)
        self.assertEqual(removed.generation_records, project.generation_records)

    def test_restyle_registers_new_assets_and_keeps_source_unchanged(self) -> None:
        project = source_project()
        output = AssetRef(
            asset_id="asset-restyled",
            kind=AssetKind.IMAGE,
            storage_path="objects/restyled.png",
            sha256="b" * 64,
            width=512,
            height=512,
            parent_asset_ids=("asset-source",),
        )
        provenance = ProvenanceRecord(
            provenance_id="provenance-restyled",
            operation="restyle_character_sheet_entry",
            created_at=datetime(2026, 7, 22, tzinfo=UTC),
            input_asset_ids=("asset-source",),
            output_asset_ids=(output.asset_id,),
        )
        updated = register_style_duplication(
            project,
            source_sheet_id="sheet-source",
            target_profile=AppearanceProfile(
                appearance_id="appearance-2",
                identity_id="character-1",
                name="Swimsuit",
                style_prompt="blue swimsuit",
            ),
            target_sheet_id="sheet-restyled",
            target_name="Avery — swimsuit",
            replacements=(
                StyleDuplicationEntry(
                    source_entry_id="entry-source",
                    target_entry_id="entry-restyled",
                    target_asset_id=output.asset_id,
                    provenance_id=provenance.provenance_id,
                ),
            ),
            assets=(output,),
            provenance=(provenance,),
        )
        self.assertEqual(project.character_sheets[0].entries[0].image_asset_id, "asset-source")
        target = updated.character_sheets[1]
        self.assertEqual(target.entries[0].parent_entry_id, "entry-source")
        self.assertEqual(target.entries[0].approval_state, ApprovalState.DRAFT)
        self.assertEqual(updated.characters[0].character_sheet_ids, ("sheet-source", "sheet-restyled"))

    def test_sheet_generation_and_restyle_execute_through_k2core_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "result.png"
            output.touch()
            backend = RecordingKreaBackend(output)
            service = KreaImageService(backend)
            progress_events = []
            service.execute(
                CharacterSheetImageRequest(
                    identity_id="character-1",
                    appearance_id="appearance-1",
                    entry_name="front",
                    identity_prompt="stable identity",
                    appearance_prompt="red dress",
                    pose_prompt="front view",
                ),
                progress=lambda *event: progress_events.append(event),
                cancellation=FakeCancellation(),
            )
            service.execute(
                RestyleEntryRequest(
                    source_entry_id="entry-source",
                    source_asset_id="asset-source",
                    identity_prompt="stable identity",
                    target_appearance_prompt="blue swimsuit",
                ),
                progress=lambda *_event: None,
                cancellation=FakeCancellation(),
            )
        self.assertEqual(backend.requests[0]["presentation_requirements"]["blank_background"], True)
        self.assertEqual(backend.requests[1]["operation"], "edit_image")
        self.assertIn("identity", backend.requests[1]["preserve"])
        self.assertEqual(len(progress_events), 1)


if __name__ == "__main__":
    unittest.main()
