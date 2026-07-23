"""Dependency-light renderer for reproducible mannequin guide images."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw

from wan2core.mannequin import MannequinScene, Quaternion, Vector3
from wan2core.mannequin.workflows import GuideKind


@dataclass(frozen=True, slots=True)
class RenderedGuideFile:
    kind: GuideKind
    path: Path


def render_mannequin_guides(
    scene: MannequinScene,
    output_directory: Path,
) -> tuple[RenderedGuideFile, ...]:
    """Render shaded, silhouette, and normalized-depth PNG guides."""

    output_directory.mkdir(parents=True, exist_ok=True)
    points, bones = _scene_geometry(scene)
    results = []
    for kind in GuideKind:
        image = _render(scene, points, bones, kind)
        path = output_directory / f"{scene.scene_id}-{kind.value}.png"
        image.save(path, format="PNG", optimize=False)
        results.append(RenderedGuideFile(kind=kind, path=path))
    return tuple(results)


def _scene_geometry(
    scene: MannequinScene,
) -> tuple[dict[str, tuple[float, float, float]], tuple[tuple[str, str], ...]]:
    points: dict[str, tuple[float, float, float]] = {}
    bones: list[tuple[str, str]] = []
    for instance in scene.instances:
        if instance.skeleton is None:
            raise ValueError("guide rendering requires an embedded skeleton definition")
        rotations = {item.joint_name: item.rotation for item in instance.joints}
        world_positions: dict[str, tuple[float, float, float]] = {}
        world_rotations: dict[str, Quaternion] = {}
        for joint in instance.skeleton.joints:
            local_rotation = rotations.get(joint.joint_name, Quaternion())
            if joint.parent_name is None:
                parent_position = (0.0, 0.0, 0.0)
                parent_rotation = Quaternion()
            else:
                parent_position = world_positions[joint.parent_name]
                parent_rotation = world_rotations[joint.parent_name]
                bones.append(
                    (
                        f"{instance.instance_id}:{joint.parent_name}",
                        f"{instance.instance_id}:{joint.joint_name}",
                    )
                )
            offset = _rotate(parent_rotation, joint.rest_offset)
            position = tuple(parent_position[index] + offset[index] for index in range(3))
            world_positions[joint.joint_name] = position
            world_rotations[joint.joint_name] = _multiply(parent_rotation, local_rotation)
            translated = (
                position[0] * instance.world_transform.scale.x
                + instance.world_transform.translation.x,
                position[1] * instance.world_transform.scale.y
                + instance.world_transform.translation.y,
                position[2] * instance.world_transform.scale.z
                + instance.world_transform.translation.z,
            )
            points[f"{instance.instance_id}:{joint.joint_name}"] = translated
    return points, tuple(bones)


def _render(scene, points, bones, kind: GuideKind) -> Image.Image:
    width, height = scene.camera.frame_width, scene.camera.frame_height
    background = (255, 255, 255) if kind is not GuideKind.DEPTH else (0, 0, 0)
    image = Image.new("RGB", (width, height), background)
    draw = ImageDraw.Draw(image)
    projected = {key: _project(scene, value) for key, value in points.items()}
    depths = [item[2] for item in projected.values()]
    low, high = (min(depths), max(depths)) if depths else (0.0, 1.0)
    span = max(high - low, 1e-6)
    for start, end in sorted(bones, key=lambda bone: projected[bone[0]][2], reverse=True):
        x0, y0, depth0 = projected[start]
        x1, y1, depth1 = projected[end]
        if kind is GuideKind.SILHOUETTE:
            color = (0, 0, 0)
            line_width = max(8, width // 48)
        elif kind is GuideKind.DEPTH:
            value = round(255 * (1.0 - (((depth0 + depth1) / 2 - low) / span)))
            color = (value, value, value)
            line_width = max(8, width // 52)
        else:
            value = round(95 + 110 * (1.0 - (((depth0 + depth1) / 2 - low) / span)))
            color = (value, min(235, value + 18), min(245, value + 28))
            line_width = max(7, width // 55)
        draw.line((x0, y0, x1, y1), fill=color, width=line_width)
        radius = max(5, line_width // 2)
        draw.ellipse((x1 - radius, y1 - radius, x1 + radius, y1 + radius), fill=color)
    return image


def _project(scene: MannequinScene, point: tuple[float, float, float]) -> tuple[float, float, float]:
    camera = scene.camera
    relative = Vector3(
        x=point[0] - camera.position.x,
        y=point[1] - camera.position.y,
        z=point[2] - camera.position.z,
    )
    camera_space = _rotate(_conjugate(camera.orientation), relative)
    depth = max(0.01, -camera_space[2])
    focal_pixels = camera.focal_length_mm / 36.0 * camera.frame_width
    return (
        camera.frame_width / 2 + camera_space[0] * focal_pixels / depth,
        camera.frame_height / 2 - camera_space[1] * focal_pixels / depth,
        depth,
    )


def _multiply(left: Quaternion, right: Quaternion) -> Quaternion:
    return Quaternion(
        w=left.w * right.w - left.x * right.x - left.y * right.y - left.z * right.z,
        x=left.w * right.x + left.x * right.w + left.y * right.z - left.z * right.y,
        y=left.w * right.y - left.x * right.z + left.y * right.w + left.z * right.x,
        z=left.w * right.z + left.x * right.y - left.y * right.x + left.z * right.w,
    )


def _conjugate(value: Quaternion) -> Quaternion:
    return Quaternion(x=-value.x, y=-value.y, z=-value.z, w=value.w)


def _rotate(rotation: Quaternion, vector: Vector3) -> tuple[float, float, float]:
    norm = math.sqrt(rotation.x**2 + rotation.y**2 + rotation.z**2 + rotation.w**2)
    if norm == 0:
        raise ValueError("quaternion cannot have zero magnitude")
    normalized = Quaternion(
        x=rotation.x / norm,
        y=rotation.y / norm,
        z=rotation.z / norm,
        w=rotation.w / norm,
    )
    value = Quaternion(x=vector.x, y=vector.y, z=vector.z, w=0.0)
    rotated = _multiply(_multiply(normalized, value), _conjugate(normalized))
    return (rotated.x, rotated.y, rotated.z)


__all__ = ["RenderedGuideFile", "render_mannequin_guides"]
