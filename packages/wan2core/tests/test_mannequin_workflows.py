from __future__ import annotations

import unittest

from wan2core.assets import AssetKind, AssetRef
from wan2core.mannequin import JointPose, Quaternion
from wan2core.mannequin.workflows import (
    ConditioningPath,
    GuideKind,
    KreaMannequinCapabilities,
    apply_pose,
    default_guide_specs,
    default_mannequin_scene,
    import_blender_scene_document,
    plan_krea_conditioning,
    save_pose_from_instance,
)


class MannequinWorkflowTests(unittest.TestCase):
    def test_default_scene_has_portable_skeleton_and_three_guide_specs(self) -> None:
        scene = default_mannequin_scene(
            scene_id="scene-1", name="Standing", width=1280, height=720
        )
        self.assertIsNotNone(scene.instances[0].skeleton)
        self.assertEqual({item.kind for item in default_guide_specs(scene)}, set(GuideKind))

    def test_pose_can_be_saved_and_applied_to_same_skeleton(self) -> None:
        scene = default_mannequin_scene(
            scene_id="scene-1", name="Standing", width=640, height=640
        )
        instance = scene.instances[0]
        changed = instance.model_copy(
            update={
                "joints": tuple(
                    JointPose(
                        joint_name=item.joint_name,
                        rotation=Quaternion(z=0.2, w=0.98)
                        if item.joint_name == "shoulder_l"
                        else item.rotation,
                    )
                    for item in instance.joints
                )
            }
        )
        pose = save_pose_from_instance(changed, pose_id="pose-1", name="Wave")
        applied = apply_pose(instance, pose)
        self.assertEqual(applied.joints, changed.joints)

    def test_blender_json_import_marks_source_without_requiring_blender(self) -> None:
        scene = default_mannequin_scene(
            scene_id="scene-1", name="Imported", width=640, height=360
        )
        imported = import_blender_scene_document(
            scene.model_dump_json(), imported_asset_id="asset-blender-json"
        )
        self.assertEqual(imported.source_type.value, "blender")
        self.assertEqual(imported.imported_asset_id, "asset-blender-json")
        self.assertEqual(imported.imported_source_metadata["format"], "wan2lab-mannequin-json")

    def test_conditioning_prefers_capability_gated_depth_then_falls_back_to_i2i(self) -> None:
        scene = default_mannequin_scene(
            scene_id="scene-1", name="Standing", width=640, height=360
        )
        guides = {GuideKind.DEPTH: "depth-asset", GuideKind.SHADED: "shaded-asset"}
        depth = plan_krea_conditioning(
            scene=scene,
            capabilities=KreaMannequinCapabilities(
                depth_control_model_ids=("krea-depth-v1",), supports_i2i=True
            ),
            guide_assets=guides,
        )
        fallback = plan_krea_conditioning(
            scene=scene,
            capabilities=KreaMannequinCapabilities(supports_i2i=True),
            guide_assets=guides,
        )
        self.assertEqual(depth.path, ConditioningPath.DEPTH_CONTROL)
        self.assertEqual(depth.depth_control_model_id, "krea-depth-v1")
        self.assertEqual(fallback.path, ConditioningPath.I2I_SCAFFOLD)
        self.assertEqual(fallback.edit_strength, 0.35)

    def test_mannequin_guide_assets_require_dimensions(self) -> None:
        with self.assertRaises(ValueError):
            AssetRef(
                asset_id="guide-1",
                kind=AssetKind.MANNEQUIN_GUIDE,
                storage_path="guides/one.png",
                sha256="a" * 64,
            )


if __name__ == "__main__":
    unittest.main()
