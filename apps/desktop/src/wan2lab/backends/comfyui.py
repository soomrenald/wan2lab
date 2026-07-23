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
    BackendCapabilities,
    FrameDurationBasis,
    ModelVariantCapabilities,
    MultiplePlusOffsetFrameCount,
    ParameterDescriptor,
    ParameterGroup,
    ParameterType,
    Resolution,
    WanMode,
)


BACKEND_ID = "comfyui-wan-video-wrapper"


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
                decoded = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
            raise ComfyUIUnavailable(f"ComfyUI request failed: {method} {path}: {error}") from error
        if not isinstance(decoded, dict):
            raise ComfyUIUnavailable(f"ComfyUI returned a non-object for {path}")
        return decoded


@dataclass(frozen=True, slots=True)
class WrapperNodes:
    model_loader: str = "WanVideoModelLoader"
    vae_loader: str = "WanVideoVAELoader"
    text_encoder: str = "WanVideoTextEncode"
    sampler: str = "WanVideoSampler"
    decoder: str = "WanVideoDecode"
    prompt_embeds: str = "WanVideoEmptyEmbeds"
    image_embeds: str = "WanVideoImageToVideoEncode"
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
        wrapper_modes.update((WanMode.I2V, WanMode.FIRST_LAST))
    if nodes.animate_embeds in available:
        wrapper_modes.add(WanMode.ANIMATE)
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
    variants = tuple(
        _model_capabilities(name, executable_modes, descriptors)
        for name in model_names
        if _modes_for_model(name, executable_modes)
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
        accelerator_vendors=frozenset({_accelerator_vendor(system_stats)}),
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
) -> ModelVariantCapabilities:
    modes = _modes_for_model(filename, wrapper_modes)
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
    resolutions = [Resolution(width=832, height=480), Resolution(width=480, height=832)]
    if "1.3b" not in filename.casefold():
        resolutions.extend(
            (Resolution(width=1280, height=720), Resolution(width=720, height=1280))
        )
    return ModelVariantCapabilities(
        model_id=model_id,
        display_name=filename,
        supported_modes=frozenset(modes),
        required_inputs_by_mode={mode: required[mode] for mode in modes},
        optional_inputs_by_mode={
            mode: ("negative_prompt", "action_spec_id", "adapters") for mode in modes
        },
        supported_resolutions=tuple(resolutions),
        default_resolution=Resolution(width=832, height=480),
        frame_count_rule=MultiplePlusOffsetFrameCount(multiple=4, offset=1),
        duration_basis=FrameDurationBasis.INTERVALS,
        default_frame_count=81,
        min_frame_count=1,
        max_frame_count=1001,
        default_generation_fps=16.0,
        supported_generation_fps=(16.0, 24.0),
        supported_precisions=("bf16", "fp16", "fp32", "fp16_fast"),
        supported_quantizations=(
            ("disabled",)
            if filename.casefold().endswith(".gguf")
            else (
                "disabled",
                "fp8_e4m3fn",
                "fp8_e4m3fn_scaled",
                "fp8_e5m2",
            )
        ),
        supported_offload_modes=("offload_device", "main_device"),
        estimated_memory_profiles={"safe_16gb": 16.0, "performance_24gb": 24.0},
        parameter_descriptors=applicable,
    )


def _modes_for_model(filename: str, wrapper_modes: set[WanMode]) -> set[WanMode]:
    name = filename.casefold()
    modes: set[WanMode] = set()
    if "animate" in name:
        modes.add(WanMode.ANIMATE)
    if any(token in name for token in ("replace", "remover")):
        modes.add(WanMode.REPLACE)
    if any(token in name for token in ("flf2v", "first-last", "first_last")):
        modes.update((WanMode.I2V, WanMode.FIRST_LAST))
    elif "i2v" in name:
        modes.add(WanMode.I2V)
    if any(token in name for token in ("t2v", "text-to-video", "text_to_video")):
        modes.add(WanMode.PROMPT)
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
            ("steps", "cfg", "shift", "scheduler", "force_offload", "riflex_freq_index"),
            modes,
        ),
        (
            nodes.image_embeds,
            (
                "noise_aug_strength",
                "start_latent_strength",
                "end_latent_strength",
                "force_offload",
            ),
            modes.intersection({WanMode.I2V, WanMode.FIRST_LAST}),
        ),
        (
            nodes.decoder,
            (
                "enable_vae_tiling",
                "tile_x",
                "tile_y",
                "tile_stride_x",
                "tile_stride_y",
            ),
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
    required = _mapping(_mapping(node_info.get("input", {})).get("required", {}))
    descriptors = []
    common_keys = {"steps", "cfg", "scheduler", "seed"}
    for key in keys:
        specification = required.get(key)
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


def _accelerator_vendor(system_stats: Mapping[str, object]) -> str:
    devices = system_stats.get("devices", ())
    device = devices[0] if isinstance(devices, list) and devices else {}
    text = " ".join(str(value) for value in _mapping(device).values()).casefold()
    if any(token in text for token in ("amd", "rocm", "hip")):
        return "rocm"
    if any(token in text for token in ("nvidia", "cuda")):
        return "cuda"
    return "cpu"


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
