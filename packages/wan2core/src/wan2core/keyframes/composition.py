"""K2-backed regional keyframe composition planning."""

from __future__ import annotations

from pydantic import Field

from k2core.lora import CHARACTER_IDENTITY_LORA_ROUTING, STANDARD_LORA_ROUTING
from k2core.regional_lora import character_identity_triggers
from k2core.regional_prompting import compile_regional_prompt_plan
from k2core.regions import PixelBox, RegionDefinition
from wan2core.base import DomainModel, Identifier
from wan2core.characters import AdapterFamily, AdapterRef
from wan2core.keyframes import CharacterRegionAssignment
from wan2core.projects import Wan2LabProject


class KeyframeCompositionRequest(DomainModel):
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    scene_prompt: str = ""
    environment_prompt: str = ""
    lighting_prompt: str = ""
    region_assignments: tuple[CharacterRegionAssignment, ...]
    mannequin_scene_id: Identifier | None = None
    spatial_strength: float = Field(default=1.0, gt=0.0, le=10.0)
    outside_penalty: float = Field(default=1.0, ge=0.0, le=10.0)
    falloff_pixels: float = Field(default=128.0, ge=0.0, le=2048.0)


class CompiledRegion(DomainModel):
    region_id: Identifier
    name: str
    box: tuple[float, float, float, float]
    prompt: str
    face_identity_prompt: str
    negative_prompt: str
    priority: int
    pose_reference_asset_id: Identifier


class KreaAdapterRouteSpec(DomainModel):
    route_id: Identifier
    adapter_id: Identifier
    asset_id: Identifier
    model_family: str
    strength: float
    region_ids: tuple[Identifier, ...]
    routing_mode: str
    trigger_phrase: str = ""

    def to_k2_payload(self) -> dict[str, object]:
        return {
            "id": self.route_id,
            "name": self.adapter_id,
            "path": self.asset_id,
            "strength": self.strength,
            "global": False,
            "region_ids": list(self.region_ids),
            "routing_mode": self.routing_mode,
            "trigger_phrase": self.trigger_phrase,
        }


class KeyframeCompositionPlan(DomainModel):
    width: int
    height: int
    global_prompt: str
    unified_prompt: str
    regions: tuple[CompiledRegion, ...]
    adapter_routes: tuple[KreaAdapterRouteSpec, ...]
    mannequin_scene_id: Identifier | None = None
    prompt_backend: str
    adapter_backend: str


def compile_keyframe_composition(
    project: Wan2LabProject,
    request: KeyframeCompositionRequest,
) -> KeyframeCompositionPlan:
    identities = {item.identity_id: item for item in project.characters}
    appearances = {item.appearance_id: item for item in project.appearance_profiles}
    entries = {
        entry.entry_id: entry
        for sheet in project.character_sheets
        for entry in sheet.entries
    }
    if request.mannequin_scene_id is not None:
        scenes = {item.scene_id for item in project.mannequin_scenes}
        if request.mannequin_scene_id not in scenes:
            raise ValueError("keyframe composition references a missing mannequin scene")

    definitions: list[RegionDefinition] = []
    compiled_regions: list[CompiledRegion] = []
    route_specs: list[KreaAdapterRouteSpec] = []
    for assignment in request.region_assignments:
        identity = identities.get(assignment.identity_id)
        appearance = appearances.get(assignment.appearance_id)
        entry = entries.get(assignment.pose_view_entry_id)
        if identity is None or appearance is None or entry is None:
            raise ValueError("region assignment references missing character data")
        if appearance.identity_id != identity.identity_id:
            raise ValueError("region appearance belongs to another identity")
        if entry.identity_id != identity.identity_id or entry.appearance_id != appearance.appearance_id:
            raise ValueError("region pose/view entry does not match identity and appearance")

        box = PixelBox(
            assignment.rectangle.x0,
            assignment.rectangle.y0,
            assignment.rectangle.x1,
            assignment.rectangle.y1,
        )
        prompt_parts = tuple(
            part.strip()
            for part in (identity.stable_description, appearance.style_prompt, assignment.prompt)
            if part.strip()
        )
        prompt = ", ".join(prompt_parts)
        definitions.append(
            RegionDefinition(
                region_id=assignment.region_id,
                name=assignment.name,
                box=box,
                prompt=prompt,
                negative_prompt=assignment.negative_prompt,
                face_identity_prompt=identity.identity_prompt,
                priority=assignment.priority,
                spatial_role="subject",
            )
        )
        compiled_regions.append(
            CompiledRegion(
                region_id=assignment.region_id,
                name=assignment.name,
                box=(box.x0, box.y0, box.x1, box.y1),
                prompt=prompt,
                face_identity_prompt=identity.identity_prompt,
                negative_prompt=assignment.negative_prompt,
                priority=assignment.priority,
                pose_reference_asset_id=entry.image_asset_id,
            )
        )
        adapters = {item.adapter_id: item for item in (*identity.adapter_refs, *appearance.adapter_refs)}
        for selection in assignment.adapters:
            if not -4.0 <= selection.strength <= 4.0:
                raise ValueError("Krea adapter strength must be between -4 and 4")
            adapter = adapters.get(selection.adapter_id)
            if adapter is None:
                raise ValueError(f"adapter {selection.adapter_id} is not assigned to the character")
            _require_krea_adapter(adapter)
            is_identity = adapter in identity.adapter_refs
            if is_identity and not adapter.trigger.strip():
                raise ValueError("character identity adapter requires trigger text")
            route_specs.append(
                KreaAdapterRouteSpec(
                    route_id=f"{adapter.adapter_id}:{assignment.region_id}",
                    adapter_id=adapter.adapter_id,
                    asset_id=adapter.asset_id,
                    model_family=adapter.model_family,
                    strength=selection.strength,
                    region_ids=(assignment.region_id,),
                    routing_mode=(
                        CHARACTER_IDENTITY_LORA_ROUTING if is_identity else STANDARD_LORA_ROUTING
                    ),
                    trigger_phrase=adapter.trigger if is_identity else "",
                )
            )

    global_prompt = ". ".join(
        part.strip().rstrip(".")
        for part in (
            request.scene_prompt,
            request.environment_prompt,
            request.lighting_prompt,
        )
        if part.strip()
    )
    payloads = [route.to_k2_payload() for route in route_specs]
    triggers = character_identity_triggers(payloads)
    regional_plan = compile_regional_prompt_plan(
        request.width,
        request.height,
        global_prompt,
        tuple(definitions),
        strength=request.spatial_strength,
        outside_penalty=request.outside_penalty,
        falloff_pixels=request.falloff_pixels,
        character_identity_triggers=triggers,
    )
    return KeyframeCompositionPlan(
        width=request.width,
        height=request.height,
        global_prompt=global_prompt,
        unified_prompt=regional_plan.prompt,
        regions=tuple(compiled_regions),
        adapter_routes=tuple(route_specs),
        mannequin_scene_id=request.mannequin_scene_id,
        prompt_backend=regional_plan.backend,
        adapter_backend="krea-regional-lora-delta-gating-v3",
    )


def _require_krea_adapter(adapter: AdapterRef) -> None:
    if adapter.family is not AdapterFamily.KREA:
        raise ValueError(f"adapter {adapter.adapter_id} is not compatible with Krea")
    if not adapter.model_family.casefold().startswith("krea"):
        raise ValueError(f"adapter {adapter.adapter_id} has incompatible model family")


__all__ = [
    "CompiledRegion",
    "KeyframeCompositionPlan",
    "KeyframeCompositionRequest",
    "KreaAdapterRouteSpec",
    "compile_keyframe_composition",
]
