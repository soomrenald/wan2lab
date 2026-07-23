from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from k2core.backends import FaceDetectionResult
from k2core.face_detail import DetectedFace
from k2core.regions import PixelBox
from k2core.worker.protocol import CommandKind

from wan2lab.krea_worker import KreaCancellation, KreaWorkerService


class FakeRuntime:
    loaded = True

    def __init__(self) -> None:
        self.model = object()
        self.clip = object()
        self.vae = object()

    @staticmethod
    def generate(*, output_directory: Path, **_values):
        output = output_directory / "generated.png"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"generated")
        return {"image_path": str(output), "seed": 4}

    @staticmethod
    def edit_image(*, output_directory: Path, **_values):
        output = output_directory / "edited.png"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"edited")
        return {"image_path": str(output)}


class KreaWorkerTests(unittest.TestCase):
    def test_service_uses_shared_backend_for_generate_and_edit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = FakeRuntime()
            service = KreaWorkerService(
                root / "results",
                runtime_loader=lambda _payload: (runtime, {"loaded": True}),
            )
            loaded = service.load({})
            progress = []
            generated = service.execute(
                CommandKind.GENERATE_BASELINE,
                {
                    "request": {
                        "operation": "generate_image",
                        "prompt": "portrait",
                        "width": 512,
                        "height": 512,
                        "seed": 4,
                    }
                },
                cancellation=KreaCancellation(),
                progress=lambda *event: progress.append(event),
            )
            source = root / "source.png"
            source.write_bytes(b"source")
            edited = service.execute(
                CommandKind.EDIT_IMAGE,
                {
                    "request": {
                        "operation": "image_edit",
                        "source_asset_id": "source",
                        "prompt": "repair",
                    },
                    "asset_paths": {"source": str(source)},
                },
                cancellation=KreaCancellation(),
                progress=lambda *_event: None,
            )
            with patch.object(
                type(service.backend),
                "detect_faces",
                return_value=FaceDetectionResult(
                    faces=(DetectedFace(PixelBox(1, 2, 30, 40), 0.9),),
                    metadata={"provider": "CPUExecutionProvider"},
                ),
            ):
                detected = service.execute(
                    CommandKind.DETECT_FACES,
                    {
                        "request": {
                            "source_asset_id": "source",
                            "threshold": 0.4,
                        },
                        "asset_paths": {"source": str(source)},
                    },
                    cancellation=KreaCancellation(),
                    progress=lambda *_event: None,
                )

            self.assertTrue(loaded["loaded"])
            self.assertEqual(Path(generated["asset_paths"][0]).read_bytes(), b"generated")
            self.assertEqual(Path(edited["asset_paths"][0]).read_bytes(), b"edited")
            self.assertEqual(detected["faces"][0]["box"]["x1"], 30)
            self.assertEqual(detected["metadata"]["provider"], "CPUExecutionProvider")
            service.release()
            self.assertIsNone(runtime.model)
            self.assertIsNone(runtime.clip)
            self.assertIsNone(runtime.vae)

    def test_cancel_token_uses_k2core_contract(self) -> None:
        cancellation = KreaCancellation()
        cancellation.cancel()
        with self.assertRaises(InterruptedError):
            cancellation.raise_if_cancelled()


if __name__ == "__main__":
    unittest.main()
