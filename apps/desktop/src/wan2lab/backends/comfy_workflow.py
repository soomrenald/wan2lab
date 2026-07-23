"""Normalized Wan requests to ComfyUI API-format workflow graphs."""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Mapping

from wan2core.backends import BackendCapabilities, WanMode
from wan2core.segments import SegmentRequest


class WorkflowBindingError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ComfyModelSelection:
    model_id: str
    model_filename: str
    vae_filename: str
    text_encoder_filename: str
    precision: str = "bf16"
    vae_precision: str = "bf16"
    text_encoder_precision: str = "bf16"
    quantization: str = "disabled"
    load_device: str = "offload_device"


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
        prefix = _safe_prefix(filename_prefix)
        if request.mode in {WanMode.PROMPT, WanMode.I2V, WanMode.FIRST_LAST}:
            workflow = self._standard_workflow(
                request,
                selection=selection,
                asset_inputs=asset_inputs,
                filename_prefix=prefix,
                parameters=parameters,
                seed=seed,
            )
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
            request,
            selection=selection,
            asset_inputs=asset_inputs,
            filename_prefix=prefix,
            parameters=parameters,
            seed=seed,
        )
        workflow = _resolve_template(template.workflow, context)
        if not isinstance(workflow, dict):
            raise WorkflowBindingError("workflow template root must be an object")
        self._validate_nodes(workflow)
        return ComfyWorkflowPlan(
            workflow=workflow,
            output_node_id=template.output_node_id,
            model_selection=selection,
            template_id=template.template_id,
            template_version=template.template_version,
            resolved_parameters=parameters,
        )

    def _resolve_parameters(self, request: SegmentRequest) -> dict[str, object]:
        descriptors = {
            item.key: item
            for item in (
                *self.capabilities.parameter_descriptors,
                *self.capabilities.model(request.model_id).parameter_descriptors,
            )
            if request.mode in item.applicable_modes
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
            if descriptor.minimum is not None and float(value) < descriptor.minimum:
                raise WorkflowBindingError(f"{key} is below its backend minimum")
            if descriptor.maximum is not None and float(value) > descriptor.maximum:
                raise WorkflowBindingError(f"{key} exceeds its backend maximum")
            if descriptor.choices and value not in descriptor.choices:
                raise WorkflowBindingError(f"{key} is not an allowed backend choice")
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
                    "enable_vae_tiling": True,
                    "tile_x": 272,
                    "tile_y": 272,
                    "tile_stride_x": 144,
                    "tile_stride_y": 128,
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
        if request.mode is WanMode.PROMPT:
            workflow["5"] = {
                "class_type": "WanVideoEmptyEmbeds",
                "inputs": {
                    "width": request.width,
                    "height": request.height,
                    "num_frames": request.frame_count,
                },
            }
        else:
            workflow["9"] = {
                "class_type": "LoadImage",
                "inputs": {"image": asset_inputs[request.start_image_asset_id]},
            }
            inputs: dict[str, object] = {
                "vae": ["2", 0],
                "width": request.width,
                "height": request.height,
                "num_frames": request.frame_count,
                "noise_aug_strength": float(parameters.get("noise_aug_strength", 0.0)),
                "start_latent_strength": float(parameters.get("start_latent_strength", 1.0)),
                "end_latent_strength": float(parameters.get("end_latent_strength", 1.0)),
                "force_offload": bool(parameters.get("force_offload", True)),
                "start_image": ["9", 0],
            }
            if request.mode is WanMode.FIRST_LAST:
                workflow["10"] = {
                    "class_type": "LoadImage",
                    "inputs": {"image": asset_inputs[request.end_image_asset_id]},
                }
                inputs["end_image"] = ["10", 0]
                inputs["fun_or_fl2v_model"] = True
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
        "model.precision": selection.precision,
        "model.vae_precision": selection.vae_precision,
        "model.text_encoder_precision": selection.text_encoder_precision,
        "model.quantization": selection.quantization,
        "model.load_device": selection.load_device,
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


__all__ = [
    "ComfyModelSelection",
    "ComfyWanWorkflowBuilder",
    "ComfyWorkflowPlan",
    "ModeWorkflowTemplate",
    "WorkflowBindingError",
]
