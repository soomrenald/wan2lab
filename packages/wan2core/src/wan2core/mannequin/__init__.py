"""Renderer-neutral mannequin scene and pose records."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from wan2core.base import DomainModel, Identifier, require_unique


class Vector3(DomainModel):
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0


class Quaternion(DomainModel):
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    w: float = 1.0


class Transform(DomainModel):
    translation: Vector3 = Vector3()
    rotation: Quaternion = Quaternion()
    scale: Vector3 = Vector3(x=1.0, y=1.0, z=1.0)


class JointPose(DomainModel):
    joint_name: str = Field(min_length=1)
    rotation: Quaternion = Quaternion()


class MannequinInstance(DomainModel):
    instance_id: Identifier
    name: str = Field(min_length=1)
    skeleton_id: Identifier
    joints: tuple[JointPose, ...] = ()
    body_proportions: dict[str, float] = Field(default_factory=dict)
    world_transform: Transform = Transform()
    character_region_id: Identifier | None = None

    @model_validator(mode="after")
    def validate_joints(self) -> "MannequinInstance":
        require_unique([joint.joint_name for joint in self.joints], "joint names")
        return self


class Camera(DomainModel):
    position: Vector3
    orientation: Quaternion = Quaternion()
    focal_length_mm: float = Field(default=50.0, gt=0.0)
    frame_width: int = Field(gt=0)
    frame_height: int = Field(gt=0)
    crop: tuple[float, float, float, float] | None = None

    @model_validator(mode="after")
    def validate_crop(self) -> "Camera":
        if self.crop is not None:
            x0, y0, x1, y1 = self.crop
            if not (0.0 <= x0 < x1 <= 1.0 and 0.0 <= y0 < y1 <= 1.0):
                raise ValueError("camera crop must be a normalized positive rectangle")
        return self


class SceneLight(DomainModel):
    light_id: Identifier
    kind: str = Field(min_length=1)
    transform: Transform = Transform()
    intensity: float = Field(default=1.0, ge=0.0)
    color: str = "#ffffff"


class ContactConstraint(DomainModel):
    instance_id: Identifier
    joint_name: str = Field(min_length=1)
    target: Vector3


class MannequinSource(StrEnum):
    INTEGRATED = "integrated"
    BLENDER = "blender"


class MannequinScene(DomainModel):
    scene_id: Identifier
    name: str = Field(min_length=1)
    instances: tuple[MannequinInstance, ...]
    camera: Camera
    lights: tuple[SceneLight, ...] = ()
    prop_asset_ids: tuple[Identifier, ...] = ()
    contact_constraints: tuple[ContactConstraint, ...] = ()
    source_type: MannequinSource = MannequinSource.INTEGRATED
    imported_asset_id: Identifier | None = None
    guide_asset_ids: tuple[Identifier, ...] = ()

    @model_validator(mode="after")
    def validate_scene(self) -> "MannequinScene":
        if not self.instances:
            raise ValueError("a mannequin scene requires at least one instance")
        require_unique([item.instance_id for item in self.instances], "mannequin instance IDs")
        require_unique([item.light_id for item in self.lights], "light IDs")
        instance_ids = {item.instance_id for item in self.instances}
        if any(item.instance_id not in instance_ids for item in self.contact_constraints):
            raise ValueError("contact constraints must reference a scene instance")
        if self.source_type is MannequinSource.BLENDER and self.imported_asset_id is None:
            raise ValueError("Blender mannequin scenes require an imported asset")
        return self


__all__ = [
    "Camera",
    "ContactConstraint",
    "JointPose",
    "MannequinInstance",
    "MannequinScene",
    "MannequinSource",
    "Quaternion",
    "SceneLight",
    "Transform",
    "Vector3",
]

