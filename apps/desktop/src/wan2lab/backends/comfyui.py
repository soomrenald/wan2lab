"""ComfyUI Wan wrapper discovery without leaking node names into wan2core."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from wan2core.backends import (
    AdapterCompatibility,
    BackendCapabilities,
    FrameDurationBasis,
    ModelVariantCapabilities,
    MultiplePlusOffsetFrameCount,
    ParameterDescriptor,
    ParameterGroup,
    ParameterType,
    Resolution,
    WanAccelerationKind,
    WanAccelerationMethodCapabilities,
    WanAccelerationQuality,
    WanMode,
)


BACKEND_ID = "comfyui-wan-video-wrapper"
_CONSTRAINED_VRAM_GIB = 18.0


class ComfyUIUnavailable(ConnectionError):
    pass


@dataclass(frozen=True, slots=True)
class ComfyUIClient:
    base_url: str = "http://127.0.0.1:8188"
    timeout_seconds: float = 3.0

    def object_info(self) -> dict[str, object]:
        return self._json("GET", "/object_info")

    def system_stats(self) -> dict[str, object]:
        return self._json("GET", "/system_stats")

    def queue_prompt(self, workflow: Mapping[str, object], *, client_id: str) -> dict[str, object]:
        return self._json(
            "POST",
            "/prompt",
            {"prompt": workflow, "client_id": client_id},
        )

    def history(self, prompt_id: str) -> dict[str, object]:
        return self._json("GET", f"/history/{quote(prompt_id, safe='')}")

    def queue(self) -> dict[str, object]:
        return self._json("GET", "/queue")

    def interrupt(self) -> dict[str, object]:
        return self._json("POST", "/interrupt", {})

    def free_models(self) -> dict[str, object]:
        return self._json("POST", "/free", {"unload_models": True, "free_memory": True})

    def _json(
        self,
        method: str,
        path: str,
        payload: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(
            self.base_url.rstrip("/") + path,
            data=body,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:  # noqa: S310
                raw_response = response.read()
                decoded = (
                    {}
                    if not raw_response.strip()
                    else json.loads(raw_response.decode("utf-8"))
                )
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
            raise ComfyUIUnavailable(f"ComfyUI request failed: {method} {path}: {error}") from error
        if not isinstance(decoded, dict):
            raise ComfyUIUnavailable(f"ComfyUI returned a non-object for {path}")
        return decoded


@dataclass(frozen=True, slots=True)
class WrapperNodes:
    model_loader: str = "WanVideoModelLoader"
    vae_loader: str = "WanVideoVAELoader"
    text_encoder_loader: str = "LoadWanVideoT5TextEncoder"
    text_encoder: str = "WanVideoTextEncode"
    sampler: str = "WanVideoSampler"
    decoder: str = "WanVideoDecode"
    prompt_embeds: str = "WanVideoEmptyEmbeds"
    image_embeds: str = "WanVideoImageToVideoEncode"
    clip_vision_encoder: str = "WanVideoClipVisionEncode"
    clip_vision_loader: str = "CLIPVisionLoader"
    block_swap: str = "WanVideoBlockSwap"
    latent_encoder: str = "WanVideoEncode"
    image_scaler: str = "ImageScale"
    animate_embeds: str = "WanVideoAnimateEmbeds"
    replace_embeds: str = "WanVideoMiniMaxRemoverEmbeds"


def inspect_comfyui_wan(
    object_info: Mapping[str, object],
    system_stats: Mapping[str, object],
    *,
    backend_version: str = "1",
    wrapper_version: str = "unknown",
    nodes: WrapperNodes = WrapperNodes(),
    executable_specialized_modes: frozenset[WanMode] = frozenset(),
) -> BackendCapabilities:
    """Normalize a live ComfyUI registry into strict shared capability records."""

    available = set(object_info)
    common = {
        nodes.model_loader,
        nodes.vae_loader,
        nodes.text_encoder_loader,
        nodes.text_encoder,
        nodes.sampler,
        nodes.decoder,
    }
    if missing := common - available:
        raise ValueError(f"Wan wrapper is incomplete; missing nodes: {', '.join(sorted(missing))}")
    wrapper_modes = set()
    if nodes.prompt_embeds in available:
        wrapper_modes.add(WanMode.PROMPT)
    if nodes.image_embeds in available:
        wrapper_modes.add(WanMode.I2V)
        if {
            nodes.clip_vision_encoder,
            nodes.clip_vision_loader,
            nodes.image_scaler,
        }.issubset(available) and _choices(
            _node(object_info, nodes.clip_vision_loader),
            "clip_name",
        ):
            wrapper_modes.add(WanMode.FIRST_LAST)
    if nodes.animate_embeds in available:
        wrapper_modes.update((WanMode.ANIMATE, WanMode.REPLACE))
    if nodes.replace_embeds in available:
        wrapper_modes.add(WanMode.REPLACE)
    if not wrapper_modes:
        raise ValueError("Wan wrapper exposes no supported generation modes")

    executable_modes = {
        WanMode.PROMPT,
        WanMode.I2V,
        WanMode.FIRST_LAST,
    }.intersection(wrapper_modes)
    executable_modes.update(executable_specialized_modes.intersection(wrapper_modes))
    model_loader_info = _node(object_info, nodes.model_loader)
    model_names = tuple(str(item) for item in _choices(model_loader_info, "model"))
    descriptors = _workflow_descriptors(object_info, frozenset(executable_modes), nodes)
    detected_accelerator = accelerator_vendor(system_stats)
    total_vram_gib = _total_vram_gib(system_stats)
    variants = tuple(
        _model_capabilities(
            name,
            executable_modes,
            descriptors,
            accelerator=detected_accelerator,
            total_vram_gib=total_vram_gib,
            unified_i2v_available={
                nodes.latent_encoder,
                nodes.image_scaler,
            }.issubset(available),
            acceleration_node_info=object_info,
        )
        for name in model_names
        if _modes_for_model(
            name,
            executable_modes,
            unified_i2v_available={
                nodes.latent_encoder,
                nodes.image_scaler,
            }.issubset(available),
        )
    )
    runtime_features = {
        "object_info_probe",
        "prompt_queue",
        "history_polling",
        "cancellation",
        "model_residency_via_comfyui_cache",
        *(f"wrapper_node_mode:{mode.value}" for mode in wrapper_modes),
        *(f"executable_mode:{mode.value}" for mode in executable_modes),
    }
    package_versions = {
        str(key): str(value)
        for key, value in _mapping(system_stats.get("system", {})).items()
        if key in {"comfyui_version", "pytorch_version", "python_version"}
    }
    return BackendCapabilities(
        backend_id=BACKEND_ID,
        backend_version=backend_version,
        accelerator_vendors=frozenset({detected_accelerator}),
        model_variants=variants,
        runtime_features=frozenset(runtime_features),
        parameter_descriptors=descriptors,
        wrapper_version=wrapper_version,
        package_versions=package_versions,
    )


def _model_capabilities(
    filename: str,
    wrapper_modes: set[WanMode],
    descriptors: tuple[ParameterDescriptor, ...],
    *,
    accelerator: str,
    total_vram_gib: float | None,
    unified_i2v_available: bool,
    acceleration_node_info: Mapping[str, object],
) -> ModelVariantCapabilities:
    modes = _modes_for_model(
        filename,
        wrapper_modes,
        unified_i2v_available=unified_i2v_available,
    )
    normalized_name = filename.casefold()
    is_wan22_ti2v_5b = (
        ("wan2_2" in normalized_name or "wan2.2" in normalized_name)
        and "ti2v" in normalized_name
        and "5b" in normalized_name
    )
    model_family = (
        "wan2.2-ti2v-5b"
        if is_wan22_ti2v_5b
        else "wan-14b-animate-replace"
        if "14b" in normalized_name
        and any(token in normalized_name for token in ("animate", "replace"))
        else "wan-14b-general"
        if "14b" in normalized_name
        else "wan-other"
    )
    model_id = "wan-" + re.sub(r"[^A-Za-z0-9._:-]+", "-", filename).strip("-")
    required = {
        WanMode.PROMPT: (),
        WanMode.I2V: ("start_image_asset_id",),
        WanMode.FIRST_LAST: ("start_image_asset_id", "end_image_asset_id"),
        WanMode.ANIMATE: ("reference_character_asset_id", "driving_video_asset_id"),
        WanMode.REPLACE: ("reference_character_asset_id", "source_video_asset_id"),
    }
    applicable = tuple(
        descriptor
        for descriptor in descriptors
        if descriptor.applicable_modes.intersection(modes)
    )
    if (
        (is_wan22_ti2v_5b or "14b" in normalized_name)
        and total_vram_gib is not None
        and total_vram_gib <= _CONSTRAINED_VRAM_GIB
    ):
        safe_defaults = {
            "enable_vae_tiling": True,
            "tile_x": 128,
            "tile_y": 128,
            "tile_stride_x": 64,
            "tile_stride_y": 64,
        }
        applicable = tuple(
            descriptor.model_copy(
                update={
                    "default": safe_defaults[descriptor.key],
                    "help_text": (
                        f"{descriptor.help_text} "
                        "Wan2Lab selected a constrained-VRAM default for this host."
                    ).strip(),
                }
            )
            if descriptor.key in safe_defaults
            else descriptor
            for descriptor in applicable
        )
    if is_wan22_ti2v_5b:
        resolutions = [Resolution(width=1280, height=704), Resolution(width=704, height=1280)]
        default_resolution = resolutions[0]
        default_frame_count = 121
        max_frame_count = 121
        default_generation_fps = 24.0
        supported_generation_fps = (24.0,)
    else:
        resolutions = [Resolution(width=832, height=480), Resolution(width=480, height=832)]
        if "1.3b" not in normalized_name:
            resolutions.extend(
                (Resolution(width=1280, height=720), Resolution(width=720, height=1280))
            )
        default_resolution = Resolution(width=832, height=480)
        default_frame_count = 81
        max_frame_count = 1001
        default_generation_fps = 16.0
        supported_generation_fps = (16.0, 24.0)
    return ModelVariantCapabilities(
        model_id=model_id,
        display_name=filename,
        model_family=model_family,
        supported_modes=frozenset(modes),
        required_inputs_by_mode={mode: required[mode] for mode in modes},
        optional_inputs_by_mode={
            mode: ("negative_prompt", "action_spec_id", "adapters") for mode in modes
        },
        supported_resolutions=tuple(resolutions),
        default_resolution=default_resolution,
        frame_count_rule=MultiplePlusOffsetFrameCount(multiple=4, offset=1),
        duration_basis=FrameDurationBasis.INTERVALS,
        default_frame_count=default_frame_count,
        min_frame_count=1,
        max_frame_count=max_frame_count,
        default_generation_fps=default_generation_fps,
        supported_generation_fps=supported_generation_fps,
        supported_precisions=(
            ("bf16", "fp16", "fp32", "fp16_fast")
            if accelerator == "cuda"
            else ("bf16", "fp16", "fp32")
            if accelerator == "rocm"
            else ("fp32",)
        ),
        supported_quantizations=(
            ("disabled",)
            if filename.casefold().endswith(".gguf") or accelerator != "cuda"
            else (
                "disabled",
                "fp8_e4m3fn",
                "fp8_e4m3fn_scaled",
                "fp8_e5m2",
            )
        ),
        supported_offload_modes=("offload_device", "main_device"),
        adapter_compatibility=tuple(
            AdapterCompatibility(mode=mode, maximum_reference_characters=1)
            for mode in (WanMode.ANIMATE, WanMode.REPLACE)
            if mode in modes
        ),
        estimated_memory_profiles={"safe_16gb": 16.0, "performance_24gb": 24.0},
        parameter_descriptors=applicable,
        acceleration_methods=_cache_acceleration_methods(
            acceleration_node_info,
            model_id=model_id,
            modes=frozenset(modes),
            accelerator=accelerator,
        ),
    )


def _cache_acceleration_methods(
    object_info: Mapping[str, object],
    *,
    model_id: str,
    modes: frozenset[WanMode],
    accelerator: str,
) -> tuple[WanAccelerationMethodCapabilities, ...]:
    cache_modes = modes.intersection(frozenset(WanMode))
    if not cache_modes:
        return ()
    declarations = (
        (
            "WanVideoEasyCache",
            "comfy-wan-easycache",
            "EasyCache",
            300,
            frozenset(WanAccelerationQuality),
            {
                "easycache_thresh": 0.015,
                "start_step": 10,
                "end_step": -1,
                "cache_device": "offload_device",
            },
            "Conservative residual caching after the initial denoising steps.",
        ),
        (
            "WanVideoMagCache",
            "comfy-wan-magcache",
            "MagCache",
            250,
            frozenset(
                {
                    WanAccelerationQuality.PREVIEW,
                    WanAccelerationQuality.BALANCED,
                }
            ),
            {
                "magcache_thresh": 0.02,
                "magcache_K": 4,
                "start_step": 1,
                "end_step": -1,
                "cache_device": "offload_device",
            },
            "More aggressive step skipping for preview and balanced renders.",
        ),
        (
            "WanVideoTeaCache",
            "comfy-wan-teacache",
            "TeaCache",
            200,
            frozenset(WanAccelerationQuality),
            {
                "rel_l1_thresh": 0.3,
                "start_step": 1,
                "end_step": -1,
                "cache_device": "offload_device",
                "use_coefficients": True,
                "mode": "e",
            },
            "Time-embedding cache with wrapper-provided Wan coefficients.",
        ),
    )
    methods = []
    for (
        node_name,
        method_id,
        display_name,
        rank,
        qualities,
        parameters,
        description,
    ) in declarations:
        node_info = object_info.get(node_name)
        if not isinstance(node_info, Mapping):
            continue
        inputs = node_info.get("input")
        accepted_inputs = set()
        if isinstance(inputs, Mapping):
            for group_name in ("required", "optional"):
                group = inputs.get(group_name)
                if isinstance(group, Mapping):
                    accepted_inputs.update(str(key) for key in group)
        if not set(parameters) <= accepted_inputs:
            continue
        methods.append(
            WanAccelerationMethodCapabilities(
                method_id=method_id,
                display_name=display_name,
                kind=WanAccelerationKind.CACHE,
                supported_modes=cache_modes,
                supported_model_ids=(model_id,),
                accelerator_vendors=frozenset({accelerator}),
                supported_quality_profiles=qualities,
                rank=rank,
                default_parameters=parameters,
                schedule_description=description,
                speed_summary="Skips compatible diffusion-model work through a cache node.",
                quality_tradeoff="Higher cache thresholds may reduce temporal or fine detail.",
            )
        )
    return tuple(methods)


def _modes_for_model(
    filename: str,
    wrapper_modes: set[WanMode],
    *,
    unified_i2v_available: bool = True,
) -> set[WanMode]:
    name = filename.casefold()
    modes: set[WanMode] = set()
    if "animate" in name:
        modes.update((WanMode.ANIMATE, WanMode.REPLACE))
    if any(token in name for token in ("replace", "remover")):
        modes.add(WanMode.REPLACE)
    if any(token in name for token in ("ti2v", "ti-2-v", "text-image-to-video")):
        modes.update((WanMode.PROMPT, WanMode.I2V))
    elif any(token in name for token in ("flf2v", "first-last", "first_last")):
        modes.update((WanMode.I2V, WanMode.FIRST_LAST))
    elif "i2v" in name:
        modes.add(WanMode.I2V)
    if any(token in name for token in ("t2v", "text-to-video", "text_to_video")):
        modes.add(WanMode.PROMPT)
    if "ti2v" in name and "5b" in name and not unified_i2v_available:
        modes.discard(WanMode.I2V)
    return modes.intersection(wrapper_modes)


def _workflow_descriptors(
    object_info: Mapping[str, object],
    modes: frozenset[WanMode],
    nodes: WrapperNodes,
) -> tuple[ParameterDescriptor, ...]:
    descriptors: dict[str, ParameterDescriptor] = {}
    sources = (
        (
            nodes.sampler,
            (
                "steps",
                "cfg",
                "shift",
                "scheduler",
                "force_offload",
                "riflex_freq_index",
                "denoise_strength",
                "batched_cfg",
                "rope_function",
                "start_step",
                "end_step",
                "add_noise_to_samples",
            ),
            modes,
        ),
        (
            nodes.image_embeds,
            (
                "noise_aug_strength",
                "start_latent_strength",
                "end_latent_strength",
                "force_offload",
                "tiled_vae",
                "augment_empty_frames",
            ),
            modes.intersection({WanMode.I2V, WanMode.FIRST_LAST}),
        ),
        (
            nodes.animate_embeds,
            (
                "force_offload",
                "frame_window_size",
                "colormatch",
                "pose_strength",
                "face_strength",
                "tiled_vae",
            ),
            modes.intersection({WanMode.ANIMATE, WanMode.REPLACE}),
        ),
        (
            nodes.decoder,
            (
                "enable_vae_tiling",
                "tile_x",
                "tile_y",
                "tile_stride_x",
                "tile_stride_y",
                "normalization",
            ),
            modes,
        ),
        (
            nodes.text_encoder,
            ("use_disk_cache", "device"),
            modes,
        ),
    )
    for node_name, keys, applicable_modes in sources:
        if not applicable_modes or node_name not in object_info:
            continue
        for descriptor in _node_descriptors(
            _node(object_info, node_name),
            keys=keys,
            modes=frozenset(applicable_modes),
            backend_node=node_name,
        ):
            existing = descriptors.get(descriptor.key)
            if existing is None:
                descriptors[descriptor.key] = descriptor
            else:
                descriptors[descriptor.key] = existing.model_copy(
                    update={
                        "applicable_modes": existing.applicable_modes.union(
                            descriptor.applicable_modes
                        )
                    }
                )
    return tuple(descriptors.values())


def _node_descriptors(
    node_info: Mapping[str, object],
    *,
    keys: tuple[str, ...],
    modes: frozenset[WanMode],
    backend_node: str,
) -> tuple[ParameterDescriptor, ...]:
    inputs = _mapping(node_info.get("input", {}))
    specifications = {
        **_mapping(inputs.get("required", {})),
        **_mapping(inputs.get("optional", {})),
    }
    descriptors = []
    common_keys = {"steps", "cfg", "scheduler", "seed"}
    for key in keys:
        specification = specifications.get(key)
        if not isinstance(specification, (list, tuple)) or not specification:
            continue
        type_value = specification[0]
        options = _mapping(specification[1] if len(specification) > 1 else {})
        if isinstance(type_value, (list, tuple)):
            parameter_type = ParameterType.ENUM
            choices = tuple(type_value)
        else:
            parameter_type = {
                "INT": ParameterType.INTEGER,
                "FLOAT": ParameterType.NUMBER,
                "BOOLEAN": ParameterType.BOOLEAN,
                "STRING": ParameterType.STRING,
            }.get(str(type_value), ParameterType.STRING)
            choices = ()
        default = options.get("default")
        if default is None:
            default = choices[0] if choices else {ParameterType.BOOLEAN: False}.get(parameter_type, 0)
        descriptors.append(
            ParameterDescriptor(
                key=key,
                display_name=key.replace("_", " ").title(),
                parameter_type=parameter_type,
                default=default,
                minimum=_number_or_none(options.get("min")),
                maximum=_number_or_none(options.get("max")),
                choices=choices,
                applicable_modes=modes,
                group=ParameterGroup.COMMON if key in common_keys else ParameterGroup.ADVANCED,
                backend_key=f"{backend_node}.{key}",
                help_text=str(options.get("tooltip", "")),
            )
        )
    return tuple(descriptors)


def accelerator_vendor(system_stats: Mapping[str, object]) -> str:
    devices = system_stats.get("devices", ())
    device = devices[0] if isinstance(devices, list) and devices else {}
    text = " ".join(str(value) for value in _mapping(device).values()).casefold()
    if any(token in text for token in ("amd", "rocm", "hip")):
        return "rocm"
    if any(token in text for token in ("nvidia", "cuda")):
        return "cuda"
    return "cpu"


def _total_vram_gib(system_stats: Mapping[str, object]) -> float | None:
    devices = system_stats.get("devices", ())
    device = devices[0] if isinstance(devices, list) and devices else {}
    total = _mapping(device).get("vram_total")
    if not isinstance(total, (int, float)) or total <= 0:
        return None
    return float(total) / (1024**3)


def _node(object_info: Mapping[str, object], name: str) -> Mapping[str, object]:
    value = object_info.get(name)
    if not isinstance(value, Mapping):
        raise ValueError(f"invalid ComfyUI object_info for {name}")
    return value


def _choices(node_info: Mapping[str, object], key: str) -> tuple[object, ...]:
    required = _mapping(_mapping(node_info.get("input", {})).get("required", {}))
    specification = required.get(key)
    if not isinstance(specification, (list, tuple)) or not specification:
        return ()
    choices = specification[0]
    return tuple(choices) if isinstance(choices, (list, tuple)) else ()


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _number_or_none(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


__all__ = [
    "BACKEND_ID",
    "ComfyUIClient",
    "ComfyUIUnavailable",
    "WrapperNodes",
    "inspect_comfyui_wan",
]
