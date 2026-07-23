from __future__ import annotations

import unittest

from wan2core.backends import WanMode
from wan2lab.backends.comfy_runtime import ComfyWanExecutor, ModelResidencyManager

from test_comfy_workflow import builder, request


class Token:
    def __init__(self, cancelled: bool = False) -> None:
        self.cancelled = cancelled


class FakeClient:
    def __init__(self, histories: list[dict[str, object]]) -> None:
        self.histories = list(histories)
        self.interrupts = 0
        self.frees = 0
        self.workflows = []

    def queue_prompt(self, workflow, *, client_id):
        self.workflows.append(workflow)
        return {"prompt_id": "prompt-1", "number": 1}

    def history(self, prompt_id):
        return self.histories.pop(0) if self.histories else {}

    def queue(self):
        return {"queue_running": [[1, "prompt-1"]], "queue_pending": []}

    def interrupt(self):
        self.interrupts += 1
        return {}

    def free_models(self):
        self.frees += 1
        return {}


def prompt_plan():
    workflow_builder = builder()
    return workflow_builder.build(
        request(workflow_builder, WanMode.PROMPT, "t2v"),
        asset_inputs={},
        filename_prefix="segment/revision",
        seed=7,
    )


class ComfyRuntimeTests(unittest.TestCase):
    def test_executor_requires_completed_history_and_typed_output(self) -> None:
        client = FakeClient(
            [
                {},
                {
                    "prompt-1": {
                        "status": {"completed": True, "status_str": "success"},
                        "outputs": {
                            "8": {
                                "gifs": [
                                    {
                                        "filename": "revision_00001.mp4",
                                        "subfolder": "wan2lab/segment",
                                        "type": "output",
                                    }
                                ]
                            }
                        },
                    }
                },
            ]
        )
        residency = ModelResidencyManager(client)
        executor = ComfyWanExecutor(
            client,
            residency,
            poll_interval_seconds=0,
            sleep=lambda _seconds: None,
        )
        progress = []
        result = executor.execute(
            prompt_plan(),
            job_id="job-1",
            segment_id="segment-1",
            cancellation=Token(),
            progress=progress.append,
        )
        self.assertEqual(result.outputs[0].storage_key, "output/wan2lab/segment/revision_00001.mp4")
        self.assertEqual(progress[-1].stage, "complete")
        self.assertTrue(residency.status()["resident"])

    def test_cancellation_interrupts_comfyui(self) -> None:
        client = FakeClient([])
        executor = ComfyWanExecutor(
            client,
            ModelResidencyManager(client),
            poll_interval_seconds=0,
            sleep=lambda _seconds: None,
        )
        with self.assertRaises(InterruptedError):
            executor.execute(
                prompt_plan(),
                job_id="job-1",
                segment_id="segment-1",
                cancellation=Token(cancelled=True),
                progress=lambda _event: None,
            )
        self.assertEqual(client.interrupts, 1)

    def test_residency_reuses_same_selection_and_releases_on_change(self) -> None:
        client = FakeClient([])
        residency = ModelResidencyManager(client)
        first = prompt_plan().model_selection
        residency.retain(first)
        residency.retain(first)
        self.assertEqual(client.frees, 0)
        residency.retain(
            first.__class__(
                model_id="different-model",
                model_filename="different.safetensors",
                vae_filename=first.vae_filename,
                text_encoder_filename=first.text_encoder_filename,
            )
        )
        self.assertEqual(client.frees, 1)
        residency.release()
        self.assertEqual(client.frees, 2)


if __name__ == "__main__":
    unittest.main()
