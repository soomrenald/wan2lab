from __future__ import annotations

import unittest

from wan2core.actions import ActionSpec
from wan2core.backends import (
    SegmentAccelerationPolicy,
    WanAccelerationPolicy,
    WanMode,
    resolve_wan_acceleration,
)
from wan2core.segments import SegmentRequest
from wan2lab.backends.comfy_workflow import (
    ComfyModelSelection,
    ComfyWanWorkflowBuilder,
    ModeWorkflowTemplate,
    WorkflowBindingError,
)
from wan2lab.backends.comfyui import BACKEND_ID, inspect_comfyui_wan

from test_comfyui_backend import node, object_info


def builder(
    system_stats: dict[str, object] | None = None,
    *,
    easycache: bool = False,
) -> ComfyWanWorkflowBuilder:
    info = object_info()
    info.update(
        {
            "LoadWanVideoT5TextEncoder": {"input": {"required": {}}},
            "LoadImage": {"input": {"required": {}}},
            "VHS_VideoCombine": {"input": {"required": {}}},
            "SpecializedAnimate": {"input": {"required": {}}},
            "SpecializedReplace": {"input": {"required": {}}},
        }
    )
    if easycache:
        info["WanVideoEasyCache"] = node(
            {
                "easycache_thresh": ["FLOAT", {"default": 0.015}],
                "start_step": ["INT", {"default": 10}],
                "end_step": ["INT", {"default": -1}],
                "cache_device": [["main_device", "offload_device"]],
            }
        )
    capabilities = inspect_comfyui_wan(
        info,
        system_stats or {"devices": [{"name": "NVIDIA CUDA"}]},
        executable_specialized_modes=frozenset({WanMode.ANIMATE, WanMode.REPLACE}),
    )
    selections = {
        model.model_id: ComfyModelSelection(
            model_id=model.model_id,
            model_filename=model.display_name,
            vae_filename="wan_2.1_vae.safetensors",
            text_encoder_filename="umt5_xxl_fp16.safetensors",
            clip_vision_filename="clip_vision_h.safetensors",
        )
        for model in capabilities.model_variants
    }
    templates = {
        WanMode.ANIMATE: ModeWorkflowTemplate(
            mode=WanMode.ANIMATE,
            template_id="animate-test",
            template_version="1",
            output_node_id="2",
            required_nodes=frozenset({"SpecializedAnimate", "VHS_VideoCombine"}),
            workflow={
                "1": {
                    "class_type": "SpecializedAnimate",
                    "inputs": {
                        "model": "$model.filename",
                        "reference": "$asset.reference_character",
                        "driving": "$asset.driving_video",
                        "frames": "$request.frame_count",
                        "seed": "$revision.seed",
                    },
                },
                "2": {
                    "class_type": "VHS_VideoCombine",
                    "inputs": {"images": ["1", 0], "filename_prefix": "$output.filename_prefix"},
                },
            },
        ),
        WanMode.REPLACE: ModeWorkflowTemplate(
            mode=WanMode.REPLACE,
            template_id="replace-test",
            template_version="2",
            output_node_id="1",
            required_nodes=frozenset({"SpecializedReplace"}),
            workflow={
                "1": {
                    "class_type": "SpecializedReplace",
                    "inputs": {
                        "source": "$asset.source_video",
                        "reference": "$asset.reference_character",
                        "mask": "$asset.mask",
                    },
                }
            },
        ),
    }
    return ComfyWanWorkflowBuilder(info, capabilities, selections, templates)


def model_id(workflow_builder: ComfyWanWorkflowBuilder, token: str) -> str:
    return next(
        item.model_id
        for item in workflow_builder.capabilities.model_variants
        if token in item.display_name
    )


def request(
    workflow_builder: ComfyWanWorkflowBuilder,
    mode: WanMode,
    token: str,
    **changes,
) -> SegmentRequest:
    values = {
        "request_id": f"request-{mode.value}",
        "segment_id": "segment-1",
        "mode": mode,
        "backend_id": BACKEND_ID,
        "model_id": model_id(workflow_builder, token),
        "start_ms": 0,
        "end_ms": 5_000,
        "width": 832,
        "height": 480,
        "generation_fps": 16,
        "frame_count": 81,
        "prompt": "a person turns toward the camera",
        "negative_prompt": "flicker",
    }
    values.update(changes)
    return SegmentRequest(**values)


class ComfyWorkflowTests(unittest.TestCase):
    def test_default_active_easycache_is_bound_to_the_standard_sampler(self) -> None:
        workflow_builder = builder(easycache=True)
        segment_request = request(
            workflow_builder,
            WanMode.PROMPT,
            "t2v",
        )
        model = workflow_builder.capabilities.model(segment_request.model_id)
        acceleration = resolve_wan_acceleration(
            project_policy=WanAccelerationPolicy(),
            segment_policy=SegmentAccelerationPolicy(),
            methods=model.acceleration_methods,
            model_id=model.model_id,
            model_family=model.model_family,
            mode=WanMode.PROMPT,
            accelerator_vendor="cuda",
        )

        plan = workflow_builder.build(
            segment_request,
            asset_inputs={},
            filename_prefix="wan2lab/cache",
            seed=1,
            acceleration=acceleration,
        )

        cache_node = next(
            (key, value)
            for key, value in plan.workflow.items()
            if value["class_type"] == "WanVideoEasyCache"
        )
        self.assertTrue(acceleration.active)
        self.assertEqual(
            plan.workflow["6"]["inputs"]["cache_args"],
            [cache_node[0], 0],
        )
        self.assertEqual(cache_node[1]["inputs"]["easycache_thresh"], 0.015)

    def test_prompt_graph_is_api_format_and_resolves_backend_parameters(self) -> None:
        workflow_builder = builder()
        plan = workflow_builder.build(
            request(
                workflow_builder,
                WanMode.PROMPT,
                "t2v",
                parameters={"steps": 24},
                action_spec_id="action-1",
                action_spec=ActionSpec(
                    action_id="action-1",
                    motion_instruction="walk slowly toward the window",
                    camera_trajectory="gentle clockwise orbit",
                    contact_constraints=("left hand remains on railing",),
                    speed_easing="ease in and settle",
                    starting_pose_ref="pose-start",
                ),
            ),
            asset_inputs={},
            filename_prefix="wan2lab/segment-1/revision-1",
            seed=44,
        )
        self.assertEqual(plan.workflow["5"]["class_type"], "WanVideoEmptyEmbeds")
        self.assertEqual(plan.workflow["6"]["inputs"]["steps"], 24)
        self.assertEqual(plan.workflow["6"]["inputs"]["seed"], 44)
        self.assertEqual(plan.workflow["4"]["inputs"]["negative_prompt"], "flicker")
        positive = plan.workflow["4"]["inputs"]["positive_prompt"]
        self.assertIn("walk slowly toward the window", positive)
        self.assertIn("gentle clockwise orbit", positive)
        self.assertEqual(
            plan.resolved_parameters["action_controls"]["starting_pose_ref"],
            "pose-start",
        )
        self.assertEqual(plan.output_node_id, "8")

    def test_unified_ti2v_prompt_uses_wrapper_5b_empty_embed_expansion(self) -> None:
        workflow_builder = builder()
        plan = workflow_builder.build(
            request(
                workflow_builder,
                WanMode.PROMPT,
                "TI2V-5B",
                width=1280,
                height=704,
                generation_fps=24,
                frame_count=121,
                parameters={"batched_cfg": True, "rope_function": "default"},
            ),
            asset_inputs={},
            filename_prefix="wan2lab/ti2v-prompt",
            seed=45,
        )

        self.assertEqual(plan.workflow["5"]["class_type"], "WanVideoEmptyEmbeds")
        self.assertEqual(plan.workflow["5"]["inputs"]["num_frames"], 121)
        self.assertNotIn("9", plan.workflow)
        self.assertTrue(plan.workflow["6"]["inputs"]["batched_cfg"])
        self.assertEqual(plan.workflow["6"]["inputs"]["rope_function"], "default")

    def test_unified_ti2v_uses_constrained_vram_decode_defaults(self) -> None:
        workflow_builder = builder(
            {
                "devices": [
                    {
                        "name": "AMD Radeon",
                        "type": "rocm",
                        "vram_total": 16 * 1024**3,
                    }
                ]
            }
        )
        plan = workflow_builder.build(
            request(
                workflow_builder,
                WanMode.PROMPT,
                "TI2V-5B",
                width=1280,
                height=704,
                generation_fps=24,
                frame_count=121,
            ),
            asset_inputs={},
            filename_prefix="wan2lab/ti2v-low-vram",
            seed=46,
        )

        self.assertTrue(plan.workflow["7"]["inputs"]["enable_vae_tiling"])
        self.assertEqual(plan.workflow["7"]["inputs"]["tile_x"], 128)
        self.assertEqual(plan.workflow["7"]["inputs"]["tile_y"], 128)
        self.assertEqual(plan.workflow["7"]["inputs"]["tile_stride_x"], 64)
        self.assertEqual(plan.workflow["7"]["inputs"]["tile_stride_y"], 64)
        self.assertEqual(plan.resolved_parameters["tile_x"], 128)

    def test_unified_ti2v_i2v_uses_encoded_extra_latent(self) -> None:
        workflow_builder = builder()
        plan = workflow_builder.build(
            request(
                workflow_builder,
                WanMode.I2V,
                "TI2V-5B",
                width=1280,
                height=704,
                generation_fps=24,
                frame_count=5,
                start_image_asset_id="first-frame",
                parameters={
                    "tiled_vae": True,
                    "noise_aug_strength": 0.1,
                    "start_latent_strength": 0.8,
                },
            ),
            asset_inputs={"first-frame": "input/first.png"},
            filename_prefix="wan2lab/ti2v-i2v",
            seed=46,
        )

        self.assertEqual(plan.workflow["9"]["class_type"], "LoadImage")
        self.assertEqual(plan.workflow["11"]["class_type"], "ImageScale")
        self.assertEqual(plan.workflow["11"]["inputs"]["image"], ["9", 0])
        self.assertEqual(plan.workflow["11"]["inputs"]["width"], 1280)
        self.assertEqual(plan.workflow["11"]["inputs"]["height"], 704)
        self.assertEqual(plan.workflow["11"]["inputs"]["crop"], "center")
        self.assertEqual(plan.workflow["10"]["class_type"], "WanVideoEncode")
        self.assertEqual(plan.workflow["10"]["inputs"]["image"], ["11", 0])
        self.assertTrue(plan.workflow["10"]["inputs"]["enable_vae_tiling"])
        self.assertEqual(plan.workflow["10"]["inputs"]["noise_aug_strength"], 0.1)
        self.assertEqual(plan.workflow["10"]["inputs"]["latent_strength"], 0.8)
        self.assertEqual(plan.workflow["5"]["class_type"], "WanVideoEmptyEmbeds")
        self.assertEqual(plan.workflow["5"]["inputs"]["extra_latents"], ["10", 0])

    def test_first_last_graph_binds_individual_immutable_assets(self) -> None:
        workflow_builder = builder()
        segment_request = request(
            workflow_builder,
            WanMode.FIRST_LAST,
            "flf2v",
            start_image_asset_id="first-frame",
            end_image_asset_id="last-frame",
        )
        plan = workflow_builder.build(
            segment_request.model_copy(
                update={
                    "parameters": {
                        "noise_aug_strength": 0.25,
                        "enable_vae_tiling": False,
                        "tile_x": 384,
                    }
                }
            ),
            asset_inputs={"first-frame": "input/first.png", "last-frame": "input/last.png"},
            filename_prefix="segment-1",
            seed=1,
        )
        self.assertEqual(plan.workflow["9"]["inputs"]["image"], "input/first.png")
        self.assertEqual(plan.workflow["10"]["inputs"]["image"], "input/last.png")
        self.assertEqual(plan.workflow["5"]["inputs"]["end_image"], ["10", 0])
        self.assertEqual(plan.workflow["11"]["inputs"]["clip_name"], "clip_vision_h.safetensors")
        self.assertEqual(plan.workflow["12"]["inputs"]["image_1"], ["9", 0])
        self.assertEqual(plan.workflow["12"]["inputs"]["image_2"], ["10", 0])
        self.assertEqual(plan.workflow["12"]["inputs"]["combine_embeds"], "concat")
        self.assertEqual(plan.workflow["5"]["inputs"]["clip_embeds"], ["12", 0])
        self.assertEqual(plan.workflow["5"]["inputs"]["noise_aug_strength"], 0.25)
        self.assertFalse(plan.workflow["7"]["inputs"]["enable_vae_tiling"])
        self.assertEqual(plan.workflow["7"]["inputs"]["tile_x"], 384)

    def test_first_last_requires_explicit_clip_vision_selection(self) -> None:
        workflow_builder = builder()
        flf_model_id = model_id(workflow_builder, "flf2v")
        selection = workflow_builder.model_selections[flf_model_id]
        workflow_builder.model_selections = {
            **workflow_builder.model_selections,
            flf_model_id: selection.__class__(
                model_id=selection.model_id,
                model_filename=selection.model_filename,
                vae_filename=selection.vae_filename,
                text_encoder_filename=selection.text_encoder_filename,
            ),
        }
        segment_request = request(
            workflow_builder,
            WanMode.FIRST_LAST,
            "flf2v",
            start_image_asset_id="first-frame",
            end_image_asset_id="last-frame",
        )

        with self.assertRaisesRegex(WorkflowBindingError, "CLIP vision"):
            workflow_builder.build(
                segment_request,
                asset_inputs={
                    "first-frame": "input/first.png",
                    "last-frame": "input/last.png",
                },
                filename_prefix="segment-1",
                seed=1,
            )

    def test_constrained_14b_selection_binds_block_swap_to_loader(self) -> None:
        workflow_builder = builder()
        prompt_model_id = model_id(workflow_builder, "t2v")
        selection = workflow_builder.model_selections[prompt_model_id]
        workflow_builder.model_selections = {
            **workflow_builder.model_selections,
            prompt_model_id: selection.__class__(
                model_id=selection.model_id,
                model_filename=selection.model_filename,
                vae_filename=selection.vae_filename,
                text_encoder_filename=selection.text_encoder_filename,
                blocks_to_swap=20,
            ),
        }

        plan = workflow_builder.build(
            request(workflow_builder, WanMode.PROMPT, "t2v"),
            asset_inputs={},
            filename_prefix="segment-1",
            seed=1,
        )

        self.assertEqual(plan.workflow["1"]["inputs"]["block_swap_args"], ["13", 0])
        self.assertEqual(plan.workflow["13"]["inputs"]["blocks_to_swap"], 20)

    def test_animate_and_replace_use_explicit_versioned_templates(self) -> None:
        workflow_builder = builder()
        animate = request(
            workflow_builder,
            WanMode.ANIMATE,
            "animate",
            reference_character_asset_id="character",
            driving_video_asset_id="driving",
        )
        animate_plan = workflow_builder.build(
            animate,
            asset_inputs={"character": "input/ref.png", "driving": "input/drive.mp4"},
            filename_prefix="animate",
            seed=9,
        )
        self.assertEqual(animate_plan.template_id, "animate-test")
        self.assertEqual(animate_plan.workflow["1"]["inputs"]["seed"], 9)

        replace = request(
            workflow_builder,
            WanMode.REPLACE,
            "replace",
            reference_character_asset_id="character",
            source_video_asset_id="source",
            mask_asset_id="mask",
        )
        replace_plan = workflow_builder.build(
            replace,
            asset_inputs={
                "character": "input/ref.png",
                "source": "input/source.mp4",
                "mask": "input/mask.png",
            },
            filename_prefix="replace",
            seed=10,
        )
        self.assertEqual(replace_plan.template_version, "2")
        self.assertEqual(replace_plan.workflow["1"]["inputs"]["mask"], "input/mask.png")

    def test_unsafe_asset_path_and_unknown_parameters_fail_before_queue(self) -> None:
        workflow_builder = builder()
        segment_request = request(
            workflow_builder,
            WanMode.I2V,
            "i2v",
            start_image_asset_id="first-frame",
        )
        with self.assertRaisesRegex(WorkflowBindingError, "safe upload-relative"):
            workflow_builder.build(
                segment_request,
                asset_inputs={"first-frame": "../secret.png"},
                filename_prefix="segment",
                seed=1,
            )
        with self.assertRaisesRegex(WorkflowBindingError, "unsupported backend parameters"):
            workflow_builder.build(
                segment_request.model_copy(update={"parameters": {"invented": 1}}),
                asset_inputs={"first-frame": "input/first.png"},
                filename_prefix="segment",
                seed=1,
            )


if __name__ == "__main__":
    unittest.main()
