"""Renderer-neutral mannequin scene, pose, import, and conditioning workflows."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from wan2core.assets import AssetKind, AssetRef
from wan2core.base import DomainModel, Identifier
from wan2core.mannequin import (
    Camera,
    JointPose,
    MannequinInstance,
    MannequinPose,
    MannequinScene,
    MannequinSource,
    Quaternion,
    SkeletonDefinition,
    SkeletonJoint,
    Vector3,
)
from wan2core.projects import Wan2LabProject
from wan2core.provenance import ProvenanceRecord


class GuideKind(StrEnum):
    SHADED = "shaded"
    SILHOUETTE = "silhouette"
    DEPTH = "depth"


class GuideRenderSpec(DomainModel):
    kind: GuideKind
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    near_clip: float = Field(default=0.1, gt=0.0)
    far_clip: float = Field(default=100.0, gt=0.0)

    @model_validator(mode="after")
    def validate_clip_range(self) -> "GuideRenderSpec":
        if self.far_clip <= self.near_clip:
            raise ValueError("far clip must be greater than near clip")
        return self


class ConditioningPath(StrEnum):
    DEPTH_CONTROL = "depth_control"
    I2I_SCAFFOLD = "i2i_scaffold"


class KreaMannequinCapabilities(DomainModel):
    depth_control_model_ids: tuple[Identifier, ...] = ()
    supports_i2i: bool = True


class MannequinConditioningPlan(DomainModel):
    scene_id: Identifier
    path: ConditioningPath
    guide_asset_id: Identifier
    depth_control_model_id: Identifier | None = None
    edit_strength: float | None = Field(default=None, gt=0.0, le=1.0)
    explanation: str


def default_humanoid_skeleton() -> SkeletonDefinition:
    """Return the portable skeleton used by the dependency-free desktop viewport."""

    joints = (
        ("pelvis", None, (0.0, 0.0, 0.0)),
        ("spine", "pelvis", (0.0, 0.55, 0.0)),
        ("chest", "spine", (0.0, 0.5, 0.0)),
        ("neck", "chest", (0.0, 0.35, 0.0)),
        ("head", "neck", (0.0, 0.35, 0.0)),
        ("shoulder_l", "chest", (-0.35, 0.22, 0.0)),
        ("elbow_l", "shoulder_l", (-0.48, -0.05, 0.0)),
        ("wrist_l", "elbow_l", (-0.42, -0.05, 0.0)),
        ("shoulder_r", "chest", (0.35, 0.22, 0.0)),
        ("elbow_r", "shoulder_r", (0.48, -0.05, 0.0)),
        ("wrist_r", "elbow_r", (0.42, -0.05, 0.0)),
        ("hip_l", "pelvis", (-0.22, -0.1, 0.0)),
        ("knee_l", "hip_l", (-0.05, -0.75, 0.0)),
        ("ankle_l", "knee_l", (0.0, -0.72, 0.0)),
        ("hip_r", "pelvis", (0.22, -0.1, 0.0)),
        ("knee_r", "hip_r", (0.05, -0.75, 0.0)),
        ("ankle_r", "knee_r", (0.0, -0.72, 0.0)),
    )
    return SkeletonDefinition(
        skeleton_id="wan2lab-humanoid-v1",
        joints=tuple(
            SkeletonJoint(
                joint_name=name,
                parent_name=parent,
                rest_offset=Vector3(x=offset[0], y=offset[1], z=offset[2]),
            )
            for name, parent, offset in joints
        ),
    )


def default_mannequin_scene(
    *,
    scene_id: Identifier,
    name: str,
    width: int,
    height: int,
) -> MannequinScene:
    skeleton = default_humanoid_skeleton()
    return MannequinScene(
        scene_id=scene_id,
        name=name,
        instances=(
            MannequinInstance(
                instance_id=f"{scene_id}-mannequin-1",
                name="Mannequin 1",
                skeleton_id=skeleton.skeleton_id,
                skeleton=skeleton,
                joints=tuple(
                    JointPose(joint_name=item.joint_name, rotation=Quaternion())
                    for item in skeleton.joints
                ),
            ),
        ),
        camera=Camera(
            position=Vector3(x=0.0, y=0.9, z=6.0),
            focal_length_mm=50.0,
            frame_width=width,
            frame_height=height,
        ),
    )


def default_guide_specs(scene: MannequinScene) -> tuple[GuideRenderSpec, ...]:
    return tuple(
        GuideRenderSpec(
            kind=kind,
            width=scene.camera.frame_width,
            height=scene.camera.frame_height,
        )
        for kind in GuideKind
    )


def save_mannequin_scene(project: Wan2LabProject, scene: MannequinScene) -> Wan2LabProject:
    scenes = tuple(item for item in project.mannequin_scenes if item.scene_id != scene.scene_id)
    updated = project.model_copy(update={"mannequin_scenes": (*scenes, scene)})
    return Wan2LabProject.model_validate(updated.model_dump())


def save_pose_from_instance(
    instance: MannequinInstance,
    *,
    pose_id: Identifier,
    name: str,
) -> MannequinPose:
    return MannequinPose(
        pose_id=pose_id,
        name=name,
        skeleton_id=instance.skeleton_id,
        joints=instance.joints,
        body_proportions=instance.body_proportions,
    )


def register_mannequin_pose(
    project: Wan2LabProject,
    pose: MannequinPose,
) -> Wan2LabProject:
    poses = tuple(item for item in project.mannequin_poses if item.pose_id != pose.pose_id)
    updated = project.model_copy(update={"mannequin_poses": (*poses, pose)})
    return Wan2LabProject.model_validate(updated.model_dump())


def apply_pose(instance: MannequinInstance, pose: MannequinPose) -> MannequinInstance:
    if instance.skeleton_id != pose.skeleton_id:
        raise ValueError("saved pose is incompatible with this mannequin skeleton")
    return instance.model_copy(
        update={"joints": pose.joints, "body_proportions": pose.body_proportions}
    )


def import_blender_scene_document(
    document: str | bytes,
    *,
    imported_asset_id: Identifier,
) -> MannequinScene:
    """Load renderer-neutral JSON exported from Blender, preserving source linkage."""

    scene = MannequinScene.model_validate_json(document)
    return MannequinScene.model_validate(
        scene.model_dump()
        | {
            "source_type": MannequinSource.BLENDER,
            "imported_asset_id": imported_asset_id,
            "imported_source_metadata": {
                **scene.imported_source_metadata,
                "format": "wan2lab-mannequin-json",
            },
        }
    )


def register_blender_scene(
    project: Wan2LabProject,
    *,
    scene: MannequinScene,
    source_asset: AssetRef,
    provenance: ProvenanceRecord,
) -> Wan2LabProject:
    if scene.source_type is not MannequinSource.BLENDER:
        raise ValueError("registered Blender scene must be marked as Blender-sourced")
    if scene.imported_asset_id != source_asset.asset_id:
        raise ValueError("Blender scene and imported source asset do not match")
    if provenance.output_asset_ids != (source_asset.asset_id,):
        raise ValueError("Blender import provenance does not match source asset")
    updated = project.model_copy(
        update={
            "assets": (*project.assets, source_asset),
            "generation_records": (*project.generation_records, provenance),
        }
    )
    return save_mannequin_scene(
        Wan2LabProject.model_validate(updated.model_dump()), scene
    )


def attach_rendered_guides(
    project: Wan2LabProject,
    *,
    scene_id: Identifier,
    assets: tuple[AssetRef, ...],
    provenance: tuple[ProvenanceRecord, ...],
) -> Wan2LabProject:
    if not assets or any(asset.kind is not AssetKind.MANNEQUIN_GUIDE for asset in assets):
        raise ValueError("mannequin guides require mannequin-guide assets")
    if {item.asset_id for item in assets} != {
        output_id for item in provenance for output_id in item.output_asset_ids
    }:
        raise ValueError("every rendered guide requires matching provenance")
    scenes = []
    found = False
    for scene in project.mannequin_scenes:
        if scene.scene_id != scene_id:
            scenes.append(scene)
            continue
        found = True
        scenes.append(
            scene.model_copy(
                update={"guide_asset_ids": (*scene.guide_asset_ids, *(item.asset_id for item in assets))}
            )
        )
    if not found:
        raise KeyError(scene_id)
    updated = project.model_copy(
        update={
            "assets": (*project.assets, *assets),
            "mannequin_scenes": tuple(scenes),
            "generation_records": (*project.generation_records, *provenance),
        }
    )
    return Wan2LabProject.model_validate(updated.model_dump())


def plan_krea_conditioning(
    *,
    scene: MannequinScene,
    capabilities: KreaMannequinCapabilities,
    guide_assets: dict[GuideKind, Identifier],
    preferred_depth_model_id: Identifier | None = None,
    fallback_edit_strength: float = 0.35,
) -> MannequinConditioningPlan:
    depth_model = preferred_depth_model_id
    if depth_model is not None and depth_model not in capabilities.depth_control_model_ids:
        raise ValueError("selected depth-control model is not supported")
    if depth_model is None and capabilities.depth_control_model_ids:
        depth_model = capabilities.depth_control_model_ids[0]
    if depth_model is not None:
        depth_asset = guide_assets.get(GuideKind.DEPTH)
        if depth_asset is None:
            raise ValueError("depth conditioning requires a rendered or imported depth guide")
        return MannequinConditioningPlan(
            scene_id=scene.scene_id,
            path=ConditioningPath.DEPTH_CONTROL,
            guide_asset_id=depth_asset,
            depth_control_model_id=depth_model,
            explanation="Compatible Krea depth control is available.",
        )
    if not capabilities.supports_i2i:
        raise ValueError("backend supports neither compatible depth control nor i2i")
    scaffold = guide_assets.get(GuideKind.SHADED)
    if scaffold is None:
        raise ValueError("i2i fallback requires a shaded mannequin guide")
    return MannequinConditioningPlan(
        scene_id=scene.scene_id,
        path=ConditioningPath.I2I_SCAFFOLD,
        guide_asset_id=scaffold,
        edit_strength=fallback_edit_strength,
        explanation="No compatible depth control was reported; using the shaded i2i scaffold.",
    )


__all__ = [
    "ConditioningPath",
    "GuideKind",
    "GuideRenderSpec",
    "KreaMannequinCapabilities",
    "MannequinConditioningPlan",
    "apply_pose",
    "attach_rendered_guides",
    "default_guide_specs",
    "default_humanoid_skeleton",
    "default_mannequin_scene",
    "import_blender_scene_document",
    "plan_krea_conditioning",
    "register_blender_scene",
    "register_mannequin_pose",
    "save_mannequin_scene",
    "save_pose_from_instance",
]
