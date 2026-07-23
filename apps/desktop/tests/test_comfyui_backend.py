from __future__ import annotations

import unittest

from wan2core.backends import ParameterGroup, WanMode
from wan2lab.backends.comfyui import inspect_comfyui_wan


def node(required: dict[str, object] | None = None) -> dict[str, object]:
    return {"input": {"required": required or {}}}


def object_info() -> dict[str, object]:
    return {
        "WanVideoModelLoader": node(
            {
                "model": [
                    [
                        "wan2.2_i2v_14B_fp16.safetensors",
                        "wan2.2_t2v_1.3B_fp16.safetensors",
                        "wan2.2_flf2v_14B_fp16.safetensors",
                        "wan2.2_animate_14B_fp16.safetensors",
                        "wan2.2_replace_14B_fp16.safetensors",
                        "unclassified.safetensors",
                    ]
                ]
            }
        ),
        "WanVideoVAELoader": node(),
        "WanVideoTextEncode": node(),
        "WanVideoSampler": node(
            {
                "steps": ["INT", {"default": 30, "min": 1, "max": 100}],
                "cfg": ["FLOAT", {"default": 6.0, "min": 0.0, "max": 30.0}],
                "shift": ["FLOAT", {"default": 5.0, "min": 0.0, "max": 1000.0}],
                "scheduler": [["unipc", "dpm++"], {"default": "unipc"}],
                "force_offload": ["BOOLEAN", {"default": True}],
                "riflex_freq_index": ["INT", {"default": 0, "min": 0, "max": 1000}],
            }
        ),
        "WanVideoDecode": node(),
        "WanVideoEmptyEmbeds": node(),
        "WanVideoImageToVideoEncode": node(),
        "WanVideoAnimateEmbeds": node(),
        "WanVideoMiniMaxRemoverEmbeds": node(),
    }


class ComfyUIBackendDiscoveryTests(unittest.TestCase):
    def test_live_registry_normalizes_modes_models_and_parameters(self) -> None:
        capabilities = inspect_comfyui_wan(
            object_info(),
            {
                "system": {
                    "comfyui_version": "0.3.50",
                    "pytorch_version": "2.8.0+rocm",
                },
                "devices": [{"name": "AMD Radeon", "type": "rocm"}],
            },
            wrapper_version="test-revision",
            executable_specialized_modes=frozenset({WanMode.ANIMATE, WanMode.REPLACE}),
        )
        self.assertEqual(capabilities.accelerator_vendors, frozenset({"rocm"}))
        self.assertEqual(capabilities.wrapper_version, "test-revision")
        self.assertEqual(len(capabilities.model_variants), 5)
        by_name = {item.display_name: item for item in capabilities.model_variants}
        self.assertEqual(
            by_name["wan2.2_flf2v_14B_fp16.safetensors"].supported_modes,
            frozenset({WanMode.I2V, WanMode.FIRST_LAST}),
        )
        self.assertEqual(
            by_name["wan2.2_animate_14B_fp16.safetensors"].supported_modes,
            frozenset({WanMode.ANIMATE}),
        )
        self.assertEqual(len(by_name["wan2.2_t2v_1.3B_fp16.safetensors"].supported_resolutions), 2)
        parameters = {item.key: item for item in capabilities.parameter_descriptors}
        self.assertEqual(parameters["steps"].group, ParameterGroup.COMMON)
        self.assertEqual(parameters["shift"].group, ParameterGroup.ADVANCED)
        self.assertEqual(parameters["scheduler"].choices, ("unipc", "dpm++"))

    def test_missing_required_wrapper_node_is_rejected_before_queueing(self) -> None:
        incomplete = object_info()
        incomplete.pop("WanVideoDecode")
        with self.assertRaisesRegex(ValueError, "WanVideoDecode"):
            inspect_comfyui_wan(incomplete, {})

    def test_unknown_model_names_are_not_overclaimed(self) -> None:
        info = object_info()
        info["WanVideoModelLoader"]["input"]["required"]["model"] = [
            ["unclassified.safetensors"]
        ]
        capabilities = inspect_comfyui_wan(info, {"devices": []})
        self.assertEqual(capabilities.model_variants, ())
        self.assertEqual(capabilities.accelerator_vendors, frozenset({"cpu"}))


if __name__ == "__main__":
    unittest.main()
