from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from wan2core.backends import ParameterGroup, WanMode
from wan2lab.backends.comfyui import ComfyUIClient, inspect_comfyui_wan


def node(
    required: dict[str, object] | None = None,
    optional: dict[str, object] | None = None,
) -> dict[str, object]:
    return {"input": {"required": required or {}, "optional": optional or {}}}


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
                        "Wan2_2-TI2V-5B_fp8_e4m3fn_scaled_KJ.safetensors",
                        "unclassified.safetensors",
                    ]
                ]
            }
        ),
        "WanVideoVAELoader": node(),
        "LoadWanVideoT5TextEncoder": node(),
        "WanVideoTextEncode": node(),
        "WanVideoSampler": node(
            {
                "steps": ["INT", {"default": 30, "min": 1, "max": 100}],
                "cfg": ["FLOAT", {"default": 6.0, "min": 0.0, "max": 30.0}],
                "shift": ["FLOAT", {"default": 5.0, "min": 0.0, "max": 1000.0}],
                "scheduler": [["unipc", "dpm++"], {"default": "unipc"}],
                "force_offload": ["BOOLEAN", {"default": True}],
                "riflex_freq_index": ["INT", {"default": 0, "min": 0, "max": 1000}],
            },
            {
                "denoise_strength": ["FLOAT", {"default": 1.0, "min": 0.0, "max": 1.0}],
                "batched_cfg": ["BOOLEAN", {"default": False}],
                "rope_function": [["default", "comfy"], {"default": "comfy"}],
                "start_step": ["INT", {"default": 0, "min": 0, "max": 10000}],
                "end_step": ["INT", {"default": -1, "min": -1, "max": 10000}],
                "add_noise_to_samples": ["BOOLEAN", {"default": False}],
            },
        ),
        "WanVideoDecode": node(
            {
                "enable_vae_tiling": ["BOOLEAN", {"default": True}],
                "tile_x": ["INT", {"default": 272, "min": 64, "max": 1024}],
                "tile_y": ["INT", {"default": 272, "min": 64, "max": 1024}],
                "tile_stride_x": ["INT", {"default": 144, "min": 32, "max": 1024}],
                "tile_stride_y": ["INT", {"default": 128, "min": 32, "max": 1024}],
            },
            {"normalization": [["default", "minmax", "none"], {"default": "default"}]},
        ),
        "WanVideoEmptyEmbeds": node(),
        "WanVideoEncode": node(),
        "ImageScale": node(),
        "WanVideoImageToVideoEncode": node(
            {
                "noise_aug_strength": ["FLOAT", {"default": 0.0, "min": 0.0, "max": 1.0}],
                "start_latent_strength": [
                    "FLOAT",
                    {"default": 1.0, "min": 0.0, "max": 1.0},
                ],
                "end_latent_strength": [
                    "FLOAT",
                    {"default": 1.0, "min": 0.0, "max": 1.0},
                ],
            },
            {
                "tiled_vae": ["BOOLEAN", {"default": False}],
                "augment_empty_frames": [
                    "FLOAT",
                    {"default": 0.0, "min": 0.0, "max": 10.0},
                ],
            },
        ),
        "WanVideoAnimateEmbeds": node(),
        "WanVideoMiniMaxRemoverEmbeds": node(),
    }


class ComfyUIBackendDiscoveryTests(unittest.TestCase):
    def test_successful_empty_response_is_normalized_for_release_endpoint(self) -> None:
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b""
        with patch("wan2lab.backends.comfyui.urlopen", return_value=response):
            result = ComfyUIClient().free_models()

        self.assertEqual(result, {})

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
        self.assertEqual(len(capabilities.model_variants), 6)
        by_name = {item.display_name: item for item in capabilities.model_variants}
        self.assertEqual(
            by_name["wan2.2_flf2v_14B_fp16.safetensors"].supported_modes,
            frozenset({WanMode.I2V, WanMode.FIRST_LAST}),
        )
        self.assertEqual(
            by_name["wan2.2_animate_14B_fp16.safetensors"].supported_modes,
            frozenset({WanMode.ANIMATE}),
        )
        ti2v = by_name["Wan2_2-TI2V-5B_fp8_e4m3fn_scaled_KJ.safetensors"]
        self.assertEqual(
            ti2v.supported_modes,
            frozenset({WanMode.PROMPT, WanMode.I2V}),
        )
        self.assertEqual(ti2v.default_resolution.model_dump(), {"width": 1280, "height": 704})
        self.assertEqual(ti2v.supported_generation_fps, (24.0,))
        self.assertEqual(ti2v.default_frame_count, 121)
        self.assertEqual(ti2v.max_frame_count, 121)
        self.assertEqual(len(by_name["wan2.2_t2v_1.3B_fp16.safetensors"].supported_resolutions), 2)
        self.assertEqual(
            by_name["wan2.2_t2v_1.3B_fp16.safetensors"].supported_precisions,
            ("bf16", "fp16", "fp32"),
        )
        self.assertEqual(
            by_name["wan2.2_t2v_1.3B_fp16.safetensors"].supported_quantizations,
            ("disabled",),
        )
        parameters = {item.key: item for item in capabilities.parameter_descriptors}
        self.assertEqual(parameters["steps"].group, ParameterGroup.COMMON)
        self.assertEqual(parameters["shift"].group, ParameterGroup.ADVANCED)
        self.assertEqual(parameters["scheduler"].choices, ("unipc", "dpm++"))
        self.assertEqual(parameters["tile_x"].maximum, 1024)
        self.assertEqual(parameters["rope_function"].choices, ("default", "comfy"))
        self.assertEqual(parameters["normalization"].default, "default")
        self.assertEqual(parameters["tiled_vae"].group, ParameterGroup.ADVANCED)
        self.assertEqual(
            parameters["noise_aug_strength"].applicable_modes,
            frozenset({WanMode.I2V, WanMode.FIRST_LAST}),
        )

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

    def test_unified_i2v_requires_the_wrapper_latent_encoder(self) -> None:
        info = object_info()
        info.pop("WanVideoEncode")
        capabilities = inspect_comfyui_wan(info, {"devices": [{"name": "AMD Radeon"}]})
        ti2v = next(
            model
            for model in capabilities.model_variants
            if "TI2V-5B" in model.display_name
        )

        self.assertEqual(ti2v.supported_modes, frozenset({WanMode.PROMPT}))

    def test_unified_i2v_requires_the_core_image_scaler(self) -> None:
        info = object_info()
        info.pop("ImageScale")
        capabilities = inspect_comfyui_wan(info, {"devices": [{"name": "AMD Radeon"}]})
        ti2v = next(
            model
            for model in capabilities.model_variants
            if "TI2V-5B" in model.display_name
        )

        self.assertEqual(ti2v.supported_modes, frozenset({WanMode.PROMPT}))

    def test_unified_5b_uses_safe_vae_tiles_on_constrained_vram(self) -> None:
        capabilities = inspect_comfyui_wan(
            object_info(),
            {
                "devices": [
                    {
                        "name": "AMD Radeon",
                        "type": "rocm",
                        "vram_total": 16 * 1024**3,
                    }
                ]
            },
        )
        ti2v = next(
            model
            for model in capabilities.model_variants
            if "TI2V-5B" in model.display_name
        )
        parameters = {
            item.key: item
            for item in capabilities.parameters_for(ti2v.model_id, WanMode.PROMPT)
        }

        self.assertTrue(parameters["enable_vae_tiling"].default)
        self.assertEqual(parameters["tile_x"].default, 128)
        self.assertEqual(parameters["tile_y"].default, 128)
        self.assertEqual(parameters["tile_stride_x"].default, 64)
        self.assertEqual(parameters["tile_stride_y"].default, 64)
        self.assertEqual(
            next(
                item.default
                for item in capabilities.parameter_descriptors
                if item.key == "tile_x"
            ),
            272,
        )
        performance_capabilities = inspect_comfyui_wan(
            object_info(),
            {
                "devices": [
                    {
                        "name": "NVIDIA RTX",
                        "type": "cuda",
                        "vram_total": 24 * 1024**3,
                    }
                ]
            },
        )
        performance_ti2v = next(
            model
            for model in performance_capabilities.model_variants
            if "TI2V-5B" in model.display_name
        )
        self.assertEqual(
            next(
                item.default
                for item in performance_capabilities.parameters_for(
                    performance_ti2v.model_id,
                    WanMode.PROMPT,
                )
                if item.key == "tile_x"
            ),
            272,
        )


if __name__ == "__main__":
    unittest.main()
