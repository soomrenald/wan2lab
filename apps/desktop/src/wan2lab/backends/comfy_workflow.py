"""Normalized Wan requests to ComfyUI API-format workflow graphs."""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Mapping

from wan2core.backends import BackendCapabilities, ResolvedWanAcceleration, WanMode
from wan2core.actions import compile_action_prompt
from wan2core.segments import SegmentRequest


class WorkflowBindingError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ComfyModelSelection:
    model_id: str
    model_filename: str
    vae_filename: str
    text_encoder_filename: str
    clip_vision_filename: str | None = None
    precision: str = "bf16"
    vae_precision: str = "bf16"
    text_encoder_precision: str = "bf16"
    quantization: str = "disabled"
    load_device: str = "offload_device"
    blocks_to_swap: int = 0


@dataclass(frozen=True, slots=True)
class ModeWorkflowTemplate:
    """A versioned graph for specialized modes with explicit placeholders."""

    mode: WanMode
    template_id: str
    template_version: str
    workflow: Mapping[str, object]
    output_node_id: str
    required_nodes: frozenset[str]


@dataclass(frozen=True, slots=True)
class ComfyWorkflowPlan:
    workflow: dict[str, object]
    output_node_id: str
    model_selection: ComfyModelSelection
    template_id: str
    template_version: str
    resolved_parameters: dict[str, object]


@dataclass(slots=True)
class ComfyWanWorkflowBuilder:
    object_info: Mapping[str, object]
    capabilities: BackendCapabilities
    model_selections: Mapping[str, ComfyModelSelection]
    specialized_templates: Mapping[WanMode, ModeWorkflowTemplate] = field(default_factory=dict)

    def build(
        self,
        request: SegmentRequest,
        *,
        asset_inputs: Mapping[str, str],
        filename_prefix: str,
        seed: int,
        acceleration: ResolvedWanAcceleration | None = None,
    ) -> ComfyWorkflowPlan:
        if request.backend_id != self.capabilities.backend_id:
            raise WorkflowBindingError("request targets a different backend")
        try:
            model = self.capabilities.model(request.model_id)
            selection = self.model_selections[request.model_id]
        except KeyError as error:
            raise WorkflowBindingError(f"unknown ComfyUI model: {request.model_id}") from error
        if request.mode not in model.supported_modes:
            raise WorkflowBindingError(
                f"model {request.model_id} does not support {request.mode.value}"
            )
        if not model.supports_resolution(request.width, request.height):
            raise WorkflowBindingError("request resolution is not supported by the model")
        if request.frame_count not in model.valid_frame_counts():
            raise WorkflowBindingError("request frame count violates the model rule")
        if request.generation_fps not in model.supported_generation_fps:
            raise WorkflowBindingError("request generation FPS is not supported by the model")
        self._validate_asset_inputs(request, asset_inputs)
        parameters = self._resolve_parameters(request)
        effective_prompt, action_controls = compile_action_prompt(
            request.prompt,
            request.action_spec,
        )
        if request.action_spec is not None:
            parameters["action_controls"] = {
                "prompt_controls": list(action_controls),
                "starting_pose_ref": request.action_spec.starting_pose_ref,
                "ending_pose_ref": request.action_spec.ending_pose_ref,
                "driving_video_asset_id": request.action_spec.driving_video_asset_id,
            }
        effective_request = request.model_copy(update={"prompt": effective_prompt})
        prefix = _safe_prefix(filename_prefix)
        if request.mode in {WanMode.PROMPT, WanMode.I2V, WanMode.FIRST_LAST}:
            workflow = self._standard_workflow(
                effective_request,
                selection=selection,
                asset_inputs=asset_inputs,
                filename_prefix=prefix,
                parameters=parameters,
                seed=seed,
            )
            self._apply_acceleration(workflow, acceleration)
            self._validate_nodes(workflow)
            return ComfyWorkflowPlan(
                workflow=workflow,
                output_node_id="8",
                model_selection=selection,
                template_id="wan2lab-standard-wan-video-wrapper",
                template_version="1",
                resolved_parameters=parameters,
            )
        template = self.specialized_templates.get(request.mode)
        if template is None:
            raise WorkflowBindingError(
                f"{request.mode.value} requires a configured, versioned workflow template"
            )
        missing = template.required_nodes - set(self.object_info)
        if missing:
            raise WorkflowBindingError(
                f"template {template.template_id} requires unavailable nodes: "
                f"{', '.join(sorted(missing))}"
            )
        context = _template_context(
            effective_request,
            selection=selection,
            asset_inputs=asset_inputs,
            filename_prefix=prefix,
            parameters=parameters,
            seed=seed,
        )
        workflow = _resolve_template(template.workflow, context)
        if not isinstance(workflow, dict):
            raise WorkflowBindingError("workflow template root must be an object")
        self._apply_acceleration(workflow, acceleration)
        self._validate_nodes(workflow)
        return ComfyWorkflowPlan(
            workflow=workflow,
            output_node_id=template.output_node_id,
            model_selection=selection,
            template_id=template.template_id,
            template_version=template.template_version,
            resolved_parameters=parameters,
        )

    def _apply_acceleration(
        self,
        workflow: dict[str, object],
        acceleration: ResolvedWanAcceleration | None,
    ) -> None:
        if acceleration is None or not acceleration.active:
            return
        cache_nodes = {
            "comfy-wan-easycache": "WanVideoEasyCache",
            "comfy-wan-magcache": "WanVideoMagCache",
            "comfy-wan-teacache": "WanVideoTeaCache",
        }
        node_name = cache_nodes.get(acceleration.method_id or "")
        if node_name is None:
            raise WorkflowBindingError(
                f"acceleration method {acceleration.method_id!r} has no ComfyUI binding"
            )
        node_info = self.object_info.get(node_name)
        if not isinstance(node_info, Mapping):
            raise WorkflowBindingError(
                f"resolved acceleration node {node_name} is unavailable"
            )
        inputs = node_info.get("input")
        accepted = set()
        if isinstance(inputs, Mapping):
            for group_name in ("required", "optional"):
                group = inputs.get(group_name)
                if isinstance(group, Mapping):
                    accepted.update(str(key) for key in group)
        unsupported = set(acceleration.resolved_parameters) - accepted
        if unsupported:
            raise WorkflowBindingError(
                f"{node_name} does not accept resolved inputs: "
                f"{', '.join(sorted(unsupported))}"
            )
        samplers = [
            item
            for item in workflow.values()
            if isinstance(item, dict) and item.get("class_type") == "WanVideoSampler"
        ]
        if len(samplers) != 1:
            raise WorkflowBindingError(
                "cache acceleration requires exactly one WanVideoSampler node"
            )
        cache_node_id = str(
            max(
                (int(key) for key in workflow if str(key).isdigit()),
                default=0,
            )
            + 1
        )
        workflow[cache_node_id] = {
            "class_type": node_name,
            "inputs": dict(acceleration.resolved_parameters),
        }
        sampler_inputs = samplers[0].get("inputs")
        if not isinstance(sampler_inputs, dict):
            raise WorkflowBindingError("WanVideoSampler inputs must be an object")
        sampler_inputs["cache_args"] = [cache_node_id, 0]

    def _resolve_parameters(self, request: SegmentRequest) -> dict[str, object]:
        descriptors = {
            item.key: item
            for item in self.capabilities.parameters_for(request.model_id, request.mode)
        }
        unknown = set(request.parameters) - set(descriptors)
        if unknown:
            raise WorkflowBindingError(
                f"unsupported backend parameters: {', '.join(sorted(unknown))}"
            )
        resolved = {key: descriptor.default for key, descriptor in descriptors.items()}
        resolved.update(request.parameters)
        for key, value in resolved.items():
            descriptor = descriptors[key]
            try:
                descriptor.validate_value(value)
            except ValueError as error:
                raise WorkflowBindingError(str(error)) from error
        return resolved

    def _standard_workflow(
        self,
        request: SegmentRequest,
        *,
        selection: ComfyModelSelection,
        asset_inputs: Mapping[str, str],
        filename_prefix: str,
        parameters: Mapping[str, object],
        seed: int,
    ) -> dict[str, object]:
        normalized_model_name = selection.model_filename.casefold()
        unified_ti2v_5b = (
            ("wan2_2" in normalized_model_name or "wan2.2" in normalized_model_name)
            and "ti2v" in normalized_model_name
            and "5b" in normalized_model_name
        )
        workflow: dict[str, object] = {
            "1": {
                "class_type": "WanVideoModelLoader",
                "inputs": {
                    "model": selection.model_filename,
                    "base_precision": selection.precision,
                    "quantization": selection.quantization,
                    "load_device": selection.load_device,
                },
            },
            "2": {
                "class_type": "WanVideoVAELoader",
                "inputs": {
                    "model_name": selection.vae_filename,
                    "precision": selection.vae_precision,
                },
            },
            "3": {
                "class_type": "LoadWanVideoT5TextEncoder",
                "inputs": {
                    "model_name": selection.text_encoder_filename,
                    "precision": selection.text_encoder_precision,
                    "load_device": selection.load_device,
                    "quantization": "disabled",
                },
            },
            "4": {
                "class_type": "WanVideoTextEncode",
                "inputs": {
                    "positive_prompt": request.prompt,
                    "negative_prompt": request.negative_prompt,
                    "t5": ["3", 0],
                    "force_offload": bool(parameters.get("force_offload", True)),
                    "model_to_offload": ["1", 0],
                },
            },
            "6": {
                "class_type": "WanVideoSampler",
                "inputs": {
                    "model": ["1", 0],
                    "image_embeds": ["5", 0],
                    "text_embeds": ["4", 0],
                    "steps": int(parameters.get("steps", 30)),
                    "cfg": float(parameters.get("cfg", 6.0)),
                    "shift": float(parameters.get("shift", 5.0)),
                    "seed": seed,
                    "force_offload": bool(parameters.get("force_offload", True)),
                    "scheduler": str(parameters.get("scheduler", "unipc")),
                    "riflex_freq_index": int(parameters.get("riflex_freq_index", 0)),
                },
            },
            "7": {
                "class_type": "WanVideoDecode",
                "inputs": {
                    "vae": ["2", 0],
                    "samples": ["6", 0],
                    "enable_vae_tiling": bool(parameters.get("enable_vae_tiling", True)),
                    "tile_x": int(parameters.get("tile_x", 272)),
                    "tile_y": int(parameters.get("tile_y", 272)),
                    "tile_stride_x": int(parameters.get("tile_stride_x", 144)),
                    "tile_stride_y": int(parameters.get("tile_stride_y", 128)),
                },
            },
            "8": {
                "class_type": "VHS_VideoCombine",
                "inputs": {
                    "images": ["7", 0],
                    "frame_rate": request.generation_fps,
                    "loop_count": 0,
                    "filename_prefix": filename_prefix,
                    "format": "video/h264-mp4",
                    "pingpong": False,
                    "save_output": True,
                },
            },
        }
        if selection.blocks_to_swap:
            workflow["13"] = {
                "class_type": "WanVideoBlockSwap",
                "inputs": {
                    "blocks_to_swap": selection.blocks_to_swap,
                    "offload_img_emb": False,
                    "offload_txt_emb": False,
                    "use_non_blocking": True,
                },
            }
            workflow["1"]["inputs"]["block_swap_args"] = ["13", 0]
        _bind_optional_parameters(
            workflow["4"]["inputs"],
            parameters,
            {
                "use_disk_cache": bool,
                "device": str,
            },
        )
        _bind_optional_parameters(
            workflow["6"]["inputs"],
            parameters,
            {
                "denoise_strength": float,
                "batched_cfg": bool,
                "rope_function": str,
                "start_step": int,
                "end_step": int,
                "add_noise_to_samples": bool,
            },
        )
        _bind_optional_parameters(
            workflow["7"]["inputs"],
            parameters,
            {"normalization": str},
        )
        if request.mode is WanMode.PROMPT:
            workflow["5"] = {
                "class_type": "WanVideoEmptyEmbeds",
                "inputs": {
                    "width": request.width,
                    "height": request.height,
                    "num_frames": request.frame_count,
                },
            }
        elif unified_ti2v_5b:
            workflow["9"] = {
                "class_type": "LoadImage",
                "inputs": {"image": asset_inputs[request.start_image_asset_id]},
            }
            workflow["11"] = {
                "class_type": "ImageScale",
                "inputs": {
                    "image": ["9", 0],
                    "upscale_method": "lanczos",
                    "width": request.width,
                    "height": request.height,
                    "crop": "center",
                },
            }
            workflow["10"] = {
                "class_type": "WanVideoEncode",
                "inputs": {
                    "vae": ["2", 0],
                    "image": ["11", 0],
                    "enable_vae_tiling": bool(parameters.get("tiled_vae", False)),
                    "tile_x": int(parameters.get("tile_x", 272)),
                    "tile_y": int(parameters.get("tile_y", 272)),
                    "tile_stride_x": int(parameters.get("tile_stride_x", 144)),
                    "tile_stride_y": int(parameters.get("tile_stride_y", 128)),
                    "noise_aug_strength": float(parameters.get("noise_aug_strength", 0.0)),
                    "latent_strength": float(parameters.get("start_latent_strength", 1.0)),
                },
            }
            workflow["5"] = {
                "class_type": "WanVideoEmptyEmbeds",
                "inputs": {
                    "width": request.width,
                    "height": request.height,
                    "num_frames": request.frame_count,
                    "extra_latents": ["10", 0],
                },
            }
        else:
            inputs: dict[str, object] = {
                "vae": ["2", 0],
                "width": request.width,
                "height": request.height,
                "num_frames": request.frame_count,
                "noise_aug_strength": float(parameters.get("noise_aug_strength", 0.0)),
                "start_latent_strength": float(parameters.get("start_latent_strength", 1.0)),
                "end_latent_strength": float(parameters.get("end_latent_strength", 1.0)),
                "force_offload": bool(parameters.get("force_offload", True)),
            }
            if request.mode is not WanMode.PROMPT:
                workflow["9"] = {
                    "class_type": "LoadImage",
                    "inputs": {"image": asset_inputs[request.start_image_asset_id]},
                }
                inputs["start_image"] = ["9", 0]
            if request.mode is WanMode.FIRST_LAST:
                if not selection.clip_vision_filename:
                    raise WorkflowBindingError(
                        "first/last-frame generation requires an explicit CLIP vision model"
                    )
                workflow["10"] = {
                    "class_type": "LoadImage",
                    "inputs": {"image": asset_inputs[request.end_image_asset_id]},
                }
                workflow["11"] = {
                    "class_type": "CLIPVisionLoader",
                    "inputs": {"clip_name": selection.clip_vision_filename},
                }
                workflow["12"] = {
                    "class_type": "WanVideoClipVisionEncode",
                    "inputs": {
                        "clip_vision": ["11", 0],
                        "image_1": ["9", 0],
                        "image_2": ["10", 0],
                        "strength_1": 1.0,
                        "strength_2": 1.0,
                        "crop": "center",
                        "combine_embeds": "concat",
                        "force_offload": True,
                        "tiles": 0,
                        "ratio": 0.5,
                    },
                }
                inputs["end_image"] = ["10", 0]
                inputs["clip_embeds"] = ["12", 0]
                inputs["fun_or_fl2v_model"] = True
            _bind_optional_parameters(
                inputs,
                parameters,
                {
                    "tiled_vae": bool,
                    "augment_empty_frames": float,
                },
            )
            workflow["5"] = {"class_type": "WanVideoImageToVideoEncode", "inputs": inputs}
        return workflow

    def _validate_nodes(self, workflow: Mapping[str, object]) -> None:
        missing = {
            str(value.get("class_type"))
            for value in workflow.values()
            if isinstance(value, Mapping) and value.get("class_type") not in self.object_info
        }
        if missing:
            raise WorkflowBindingError(
                f"workflow requires unavailable nodes: {', '.join(sorted(missing))}"
            )

    @staticmethod
    def _validate_asset_inputs(
        request: SegmentRequest,
        asset_inputs: Mapping[str, str],
    ) -> None:
        required = {
            asset_id
            for asset_id in (
                request.start_image_asset_id,
                request.end_image_asset_id,
                request.reference_character_asset_id,
                request.driving_video_asset_id,
                request.source_video_asset_id,
                request.mask_asset_id,
            )
            if asset_id is not None
        }
        if missing := required - set(asset_inputs):
            raise WorkflowBindingError(f"missing ComfyUI asset inputs: {', '.join(sorted(missing))}")
        for value in asset_inputs.values():
            normalized = value.replace("\\", "/")
            if normalized.startswith("/") or ".." in normalized.split("/"):
                raise WorkflowBindingError("ComfyUI asset inputs must be safe upload-relative paths")


def _template_context(
    request: SegmentRequest,
    *,
    selection: ComfyModelSelection,
    asset_inputs: Mapping[str, str],
    filename_prefix: str,
    parameters: Mapping[str, object],
    seed: int,
) -> dict[str, object]:
    values: dict[str, object] = {
        **{f"request.{key}": value for key, value in request.model_dump(mode="json").items()},
        **{f"parameter.{key}": value for key, value in parameters.items()},
        **{f"asset.{key}": value for key, value in asset_inputs.items()},
        "output.filename_prefix": filename_prefix,
        "revision.seed": seed,
        "model.filename": selection.model_filename,
        "model.vae_filename": selection.vae_filename,
        "model.text_encoder_filename": selection.text_encoder_filename,
        "model.clip_vision_filename": selection.clip_vision_filename,
        "model.precision": selection.precision,
        "model.vae_precision": selection.vae_precision,
        "model.text_encoder_precision": selection.text_encoder_precision,
        "model.quantization": selection.quantization,
        "model.load_device": selection.load_device,
        "model.blocks_to_swap": selection.blocks_to_swap,
        "asset.start_image": (
            asset_inputs.get(request.start_image_asset_id)
            if request.start_image_asset_id is not None
            else None
        ),
        "asset.end_image": (
            asset_inputs.get(request.end_image_asset_id)
            if request.end_image_asset_id is not None
            else None
        ),
        "asset.reference_character": (
            asset_inputs.get(request.reference_character_asset_id)
            if request.reference_character_asset_id is not None
            else None
        ),
        "asset.driving_video": (
            asset_inputs.get(request.driving_video_asset_id)
            if request.driving_video_asset_id is not None
            else None
        ),
        "asset.source_video": (
            asset_inputs.get(request.source_video_asset_id)
            if request.source_video_asset_id is not None
            else None
        ),
        "asset.mask": (
            asset_inputs.get(request.mask_asset_id)
            if request.mask_asset_id is not None
            else None
        ),
    }
    return values


def _resolve_template(value: object, context: Mapping[str, object]) -> object:
    if isinstance(value, str) and value.startswith("$"):
        key = value[1:]
        if key not in context:
            raise WorkflowBindingError(f"unresolved workflow placeholder: {value}")
        return context[key]
    if isinstance(value, dict):
        return {key: _resolve_template(item, context) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_template(item, context) for item in value]
    if isinstance(value, tuple):
        return [_resolve_template(item, context) for item in value]
    return value


def _safe_prefix(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_./-]+", "-", value).strip("./-")
    if not cleaned or ".." in cleaned.split("/"):
        raise WorkflowBindingError("invalid output filename prefix")
    return cleaned


def _bind_optional_parameters(
    inputs: dict[str, object],
    parameters: Mapping[str, object],
    conversions: Mapping[str, type],
) -> None:
    for key, conversion in conversions.items():
        if key in parameters:
            inputs[key] = conversion(parameters[key])


__all__ = [
    "ComfyModelSelection",
    "ComfyWanWorkflowBuilder",
    "ComfyWorkflowPlan",
    "ModeWorkflowTemplate",
    "WorkflowBindingError",
]
