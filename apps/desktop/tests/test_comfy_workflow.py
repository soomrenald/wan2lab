from __future__ import annotations

import unittest

from wan2core.backends import WanMode
from wan2core.segments import SegmentRequest
from wan2lab.backends.comfy_workflow import (
    ComfyModelSelection,
    ComfyWanWorkflowBuilder,
    ModeWorkflowTemplate,
    WorkflowBindingError,
)
from wan2lab.backends.comfyui import BACKEND_ID, inspect_comfyui_wan

from test_comfyui_backend import object_info


def builder() -> ComfyWanWorkflowBuilder:
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
    capabilities = inspect_comfyui_wan(
        info,
        {"devices": [{"name": "NVIDIA CUDA"}]},
        executable_specialized_modes=frozenset({WanMode.ANIMATE, WanMode.REPLACE}),
    )
    selections = {
        model.model_id: ComfyModelSelection(
            model_id=model.model_id,
            model_filename=model.display_name,
            vae_filename="wan_2.1_vae.safetensors",
            text_encoder_filename="umt5_xxl_fp16.safetensors",
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
    def test_prompt_graph_is_api_format_and_resolves_backend_parameters(self) -> None:
        workflow_builder = builder()
        plan = workflow_builder.build(
            request(workflow_builder, WanMode.PROMPT, "t2v", parameters={"steps": 24}),
            asset_inputs={},
            filename_prefix="wan2lab/segment-1/revision-1",
            seed=44,
        )
        self.assertEqual(plan.workflow["5"]["class_type"], "WanVideoEmptyEmbeds")
        self.assertEqual(plan.workflow["6"]["inputs"]["steps"], 24)
        self.assertEqual(plan.workflow["6"]["inputs"]["seed"], 44)
        self.assertEqual(plan.workflow["4"]["inputs"]["negative_prompt"], "flicker")
        self.assertEqual(plan.output_node_id, "8")

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
            segment_request,
            asset_inputs={"first-frame": "input/first.png", "last-frame": "input/last.png"},
            filename_prefix="segment-1",
            seed=1,
        )
        self.assertEqual(plan.workflow["9"]["inputs"]["image"], "input/first.png")
        self.assertEqual(plan.workflow["10"]["inputs"]["image"], "input/last.png")
        self.assertEqual(plan.workflow["5"]["inputs"]["end_image"], ["10", 0])

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
