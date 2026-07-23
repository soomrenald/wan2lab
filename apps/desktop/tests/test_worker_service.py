from __future__ import annotations

from io import StringIO
import json
import unittest

from wan2core.backends import WanMode
from wan2core.workers import (
    GenerateSegmentRequest,
    InspectCapabilitiesRequest,
    LoadModelRequest,
    parse_worker_event,
)
from wan2lab.backends.comfyui import BACKEND_ID
from wan2lab.worker import (
    ComfyWorkerService,
    StdioWanWorker,
    ThreadCancellation,
    WanOutOfMemory,
)

from test_comfy_workflow import model_id, request
from test_comfyui_backend import node, object_info


class WorkerClient:
    def __init__(
        self,
        *,
        free_vram_gib: float | None = None,
        fail_with_oom: bool = False,
    ) -> None:
        self.info = object_info()
        self.info.update(
            {
                "LoadWanVideoT5TextEncoder": node(
                    {"model_name": [["umt5_xxl_fp16.safetensors"]]}
                ),
                "LoadImage": node(),
                "VHS_VideoCombine": node(),
            }
        )
        self.info["WanVideoVAELoader"] = node(
            {"model_name": [["wan_2.1_vae.safetensors"]]}
        )
        self.frees = 0
        self.free_vram_gib = free_vram_gib
        self.fail_with_oom = fail_with_oom

    def object_info(self):
        return self.info

    def system_stats(self):
        device = {"name": "NVIDIA RTX", "type": "cuda"}
        if self.free_vram_gib is not None:
            device["vram_free"] = round(self.free_vram_gib * 1024**3)
        return {"devices": [device]}

    def queue_prompt(self, workflow, *, client_id):
        return {"prompt_id": "prompt-worker-1"}

    def history(self, prompt_id):
        if self.fail_with_oom:
            return {
                prompt_id: {
                    "status": {
                        "completed": False,
                        "status_str": "error",
                        "messages": [["execution_error", "CUDA out of memory"]],
                    }
                }
            }
        return {
            prompt_id: {
                "status": {"completed": True, "status_str": "success"},
                "outputs": {
                    "8": {
                        "gifs": [
                            {
                                "filename": "revision.mp4",
                                "subfolder": "wan2lab/segment-1",
                                "type": "output",
                            }
                        ]
                    }
                },
            }
        }

    def queue(self):
        return {"queue_running": [], "queue_pending": []}

    def interrupt(self):
        return {}

    def free_models(self):
        self.frees += 1
        return {}


class WorkerServiceTests(unittest.TestCase):
    def test_runtime_oom_releases_resident_model_for_recovery(self) -> None:
        client = WorkerClient(fail_with_oom=True)
        service = ComfyWorkerService(client, poll_interval_seconds=0)
        service.inspect("inspect")
        assert service.capabilities is not None
        prompt_model = model_id(
            type("BuilderView", (), {"capabilities": service.capabilities})(),
            "t2v",
        )
        service.load(
            LoadModelRequest(
                command_id="load",
                backend_id=BACKEND_ID,
                model_id=prompt_model,
                precision="bf16",
                quantization="disabled",
                offload_mode="offload_device",
                component_model_ids={
                    "vae": "wan_2.1_vae.safetensors",
                    "text_encoder": "umt5_xxl_fp16.safetensors",
                },
            )
        )
        segment_request = request(
            type("BuilderView", (), {"capabilities": service.capabilities})(),
            WanMode.PROMPT,
            "t2v",
        )
        with self.assertRaises(WanOutOfMemory):
            service.generate(
                GenerateSegmentRequest(
                    command_id="generate",
                    job_id="job",
                    request=segment_request,
                    seed=1,
                    output_prefix="wan2lab/oom",
                ),
                ThreadCancellation(),
                lambda _event: None,
            )
        self.assertEqual(client.frees, 1)
        self.assertIsNone(service.residency.resident)

    def test_model_load_preflight_requires_offload_when_vram_is_constrained(self) -> None:
        service = ComfyWorkerService(WorkerClient(free_vram_gib=8), poll_interval_seconds=0)
        service.inspect("inspect")
        assert service.capabilities is not None
        prompt_model = model_id(
            type("BuilderView", (), {"capabilities": service.capabilities})(),
            "t2v",
        )
        with self.assertRaisesRegex(MemoryError, "Select offload"):
            service.load(
                LoadModelRequest(
                    command_id="load-main",
                    backend_id=BACKEND_ID,
                    model_id=prompt_model,
                    precision="bf16",
                    quantization="disabled",
                    offload_mode="main_device",
                    component_model_ids={
                        "vae": "wan_2.1_vae.safetensors",
                        "text_encoder": "umt5_xxl_fp16.safetensors",
                    },
                )
            )
        service.load(
            LoadModelRequest(
                command_id="load-offload",
                backend_id=BACKEND_ID,
                model_id=prompt_model,
                precision="bf16",
                quantization="disabled",
                offload_mode="offload_device",
                component_model_ids={
                    "vae": "wan_2.1_vae.safetensors",
                    "text_encoder": "umt5_xxl_fp16.safetensors",
                },
            )
        )

    def test_runtime_status_reports_accelerator_and_vram_diagnostics(self) -> None:
        service = ComfyWorkerService(WorkerClient(free_vram_gib=8), poll_interval_seconds=0)

        event = service.status("runtime-status")

        self.assertEqual(event.status["accelerator_vendor"], "cuda")
        self.assertEqual(event.status["devices"][0]["name"], "NVIDIA RTX")
        self.assertEqual(event.status["devices"][0]["vram_free"], round(8 * 1024**3))

    def test_service_requires_explicit_components_and_returns_typed_result(self) -> None:
        client = WorkerClient()
        service = ComfyWorkerService(client, poll_interval_seconds=0)
        capabilities_event = service.inspect("inspect-1")
        capabilities = service.capabilities
        assert capabilities is not None
        prompt_model = model_id(
            type("BuilderView", (), {"capabilities": capabilities})(), "t2v"
        )
        with self.assertRaisesRegex(ValueError, "component model selections"):
            service.load(
                LoadModelRequest(
                    command_id="load-bad",
                    backend_id=BACKEND_ID,
                    model_id=prompt_model,
                    precision="bf16",
                )
            )
        service.load(
            LoadModelRequest(
                command_id="load-1",
                backend_id=BACKEND_ID,
                model_id=prompt_model,
                precision="bf16",
                quantization="disabled",
                offload_mode="offload_device",
                component_model_ids={
                    "vae": "wan_2.1_vae.safetensors",
                    "text_encoder": "umt5_xxl_fp16.safetensors",
                },
            )
        )
        segment_request = request(
            type("BuilderView", (), {"capabilities": capabilities})(),
            WanMode.PROMPT,
            "t2v",
        )
        events = []
        result = service.generate(
            GenerateSegmentRequest(
                command_id="generate-1",
                job_id="job-1",
                request=segment_request,
                seed=12,
                output_prefix="wan2lab/segment-1/revision-1",
            ),
            ThreadCancellation(),
            events.append,
        )
        self.assertEqual(capabilities_event.kind.value, "capabilities")
        self.assertEqual(
            capabilities_event.capabilities["component_models"],
            {
                "vae": ["wan_2.1_vae.safetensors"],
                "text_encoder": ["umt5_xxl_fp16.safetensors"],
            },
        )
        self.assertTrue(result.result.result_asset_id.startswith("comfy-video-"))
        self.assertEqual(
            result.result.metadata["output_storage_keys"],
            ("output/wan2lab/segment-1/revision.mp4",),
        )
        self.assertEqual(events[-1].progress.stage, "complete")

    def test_stdio_inspection_emits_parseable_json_line(self) -> None:
        output = StringIO()
        worker = StdioWanWorker(
            ComfyWorkerService(WorkerClient(), poll_interval_seconds=0), output
        )
        worker.dispatch(
            InspectCapabilitiesRequest(
                command_id="inspect-1",
                backend_id=BACKEND_ID,
            )
        )
        event = parse_worker_event(json.loads(output.getvalue().strip()))
        self.assertEqual(event.kind.value, "capabilities")


if __name__ == "__main__":
    unittest.main()
