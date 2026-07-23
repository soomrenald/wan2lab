"""UI-neutral Krea image operations for sheets and composed keyframes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from pydantic import Field

from k2core.backends import (
    BackendResult,
    CancellationToken,
    ImageGeneratorBackend,
    ProgressCallback,
)
from wan2core.base import DomainModel, Identifier
from wan2core.keyframes.composition import KeyframeCompositionPlan


class CharacterSheetImageRequest(DomainModel):
    identity_id: Identifier
    appearance_id: Identifier
    entry_name: str = Field(min_length=1)
    identity_prompt: str = Field(min_length=1)
    appearance_prompt: str = ""
    pose_prompt: str = Field(min_length=1)
    negative_prompt: str = ""
    width: int = Field(default=1024, gt=0)
    height: int = Field(default=1024, gt=0)
    source_asset_id: Identifier | None = None
    mannequin_guide_asset_id: Identifier | None = None
    seed: int = Field(default=0, ge=0)

    def to_k2_request(self) -> dict[str, object]:
        prompt = ", ".join(
            item.strip()
            for item in (
                self.identity_prompt,
                self.appearance_prompt,
                self.pose_prompt,
                "single character, plain blank background",
            )
            if item.strip()
        )
        return {
            "operation": "edit_image" if self.source_asset_id else "generate_image",
            "prompt": prompt,
            "negative_prompt": self.negative_prompt,
            "width": self.width,
            "height": self.height,
            "seed": self.seed,
            "source_asset_id": self.source_asset_id,
            "mannequin_guide_asset_id": self.mannequin_guide_asset_id,
            "presentation_requirements": {
                "single_subject": True,
                "blank_background": True,
            },
        }


class RestyleEntryRequest(DomainModel):
    source_entry_id: Identifier
    source_asset_id: Identifier
    identity_prompt: str = Field(min_length=1)
    target_appearance_prompt: str = Field(min_length=1)
    edit_strength: float = Field(default=0.35, gt=0.0, le=1.0)
    seed: int = Field(default=0, ge=0)

    def to_k2_request(self) -> dict[str, object]:
        return {
            "operation": "edit_image",
            "source_asset_id": self.source_asset_id,
            "prompt": ", ".join((self.identity_prompt, self.target_appearance_prompt)),
            "edit_strength": self.edit_strength,
            "seed": self.seed,
            "preserve": ("identity", "pose", "view", "framing", "background"),
        }


class ComposedKeyframeRequest(DomainModel):
    composition: KeyframeCompositionPlan
    seed: int = Field(default=0, ge=0)
    source_asset_id: Identifier | None = None
    mannequin_guide_asset_id: Identifier | None = None
    conditioning_path: str | None = None

    def to_k2_request(self) -> dict[str, object]:
        return {
            "operation": "edit_image" if self.source_asset_id else "generate_image",
            "width": self.composition.width,
            "height": self.composition.height,
            "prompt": self.composition.unified_prompt,
            "global_prompt": self.composition.global_prompt,
            "regions": [item.model_dump(mode="json") for item in self.composition.regions],
            "adapter_routes": [item.to_k2_payload() for item in self.composition.adapter_routes],
            "prompt_backend": self.composition.prompt_backend,
            "adapter_backend": self.composition.adapter_backend,
            "source_asset_id": self.source_asset_id,
            "mannequin_guide_asset_id": self.mannequin_guide_asset_id,
            "conditioning_path": self.conditioning_path,
            "seed": self.seed,
        }


@dataclass(frozen=True, slots=True)
class KreaImageService:
    """Thin service that executes normalized jobs only through k2core's contract."""

    backend: ImageGeneratorBackend

    def execute(
        self,
        request: CharacterSheetImageRequest | RestyleEntryRequest | ComposedKeyframeRequest,
        *,
        progress: ProgressCallback,
        cancellation: CancellationToken,
    ) -> BackendResult:
        payload: Mapping[str, object] = request.to_k2_request()
        errors = self.backend.validate_image_request(payload)
        if errors:
            raise ValueError("; ".join(errors))
        return self.backend.generate_image(
            payload,
            progress=progress,
            cancellation=cancellation,
        )


__all__ = [
    "CharacterSheetImageRequest",
    "ComposedKeyframeRequest",
    "KreaImageService",
    "RestyleEntryRequest",
]
