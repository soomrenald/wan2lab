from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtGui import QGuiApplication

from wan2core.editing.workflows import plan_frame_extraction, plan_frame_revision_assembly
from wan2core.export import build_export_plan
from wan2core.segments import RevisionReviewState, SegmentState
from wan2lab.media import (
    execute_export_plan,
    execute_frame_extraction,
    execute_frame_revision_assembly,
)
from wan2lab.frame_runner import (
    BatchFrameModificationProcessRunner,
    FrameModificationProcessRunner,
)

from test_frame_workflows import source_project


class Token:
    cancelled = False


@unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg is required")
class MediaExecutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.application = QGuiApplication.instance() or QGuiApplication([])

    def test_nonblocking_frame_modification_runner_chains_ffmpeg_steps(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            subprocess.run(
                (
                    "ffmpeg",
                    "-loglevel",
                    "error",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=blue:s=64x64:r=16:d=0.3125",
                    "-frames:v",
                    "5",
                    "-pix_fmt",
                    "yuv420p",
                    str(source),
                ),
                check=True,
            )
            replacement = root / "replacement.jpg"
            Image.new("RGB", (64, 64), "red").save(replacement)
            extraction = plan_frame_extraction(
                ffmpeg_executable="ffmpeg",
                source_video_path=str(source),
                frame_index=2,
                frame_count=5,
                output_path=str(root / "original.png"),
            )
            assembly = plan_frame_revision_assembly(
                ffmpeg_executable="ffmpeg",
                source_video_path=str(source),
                replacement_paths={2: str(root / "staged.png")},
                generation_fps=16,
                frame_count=5,
                output_path=str(root / "revised.mp4"),
                work_directory=str(root / "work"),
            )
            runner = FrameModificationProcessRunner()
            completed = []
            failures = []
            loop = QEventLoop()
            runner.completed.connect(lambda *paths: (completed.append(paths), loop.quit()))
            runner.failed.connect(lambda message: (failures.append(message), loop.quit()))
            QTimer.singleShot(10_000, loop.quit)

            runner.start(extraction, assembly, replacement_source=replacement)
            loop.exec()

            self.assertFalse(failures)
            self.assertEqual(len(completed), 1)
            self.assertGreater((root / "revised.mp4").stat().st_size, 0)
            self.assertTrue((root / "original.png").is_file())

    def test_nonblocking_batch_frame_runner_encodes_once_for_all_replacements(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            subprocess.run(
                (
                    "ffmpeg",
                    "-loglevel",
                    "error",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=blue:s=64x64:r=16:d=0.3125",
                    "-frames:v",
                    "5",
                    "-pix_fmt",
                    "yuv420p",
                    str(source),
                ),
                check=True,
            )
            replacement_one = root / "replacement-one.jpg"
            replacement_three = root / "replacement-three.jpg"
            Image.new("RGB", (64, 64), "red").save(replacement_one)
            Image.new("RGB", (64, 64), "green").save(replacement_three)
            extractions = tuple(
                plan_frame_extraction(
                    ffmpeg_executable="ffmpeg",
                    source_video_path=str(source),
                    frame_index=index,
                    frame_count=5,
                    output_path=str(root / f"original-{index}.png"),
                )
                for index in (1, 3)
            )
            assembly = plan_frame_revision_assembly(
                ffmpeg_executable="ffmpeg",
                source_video_path=str(source),
                replacement_paths={
                    1: str(root / "staged-one.png"),
                    3: str(root / "staged-three.png"),
                },
                generation_fps=16,
                frame_count=5,
                output_path=str(root / "revised.mp4"),
                work_directory=str(root / "work"),
            )
            runner = BatchFrameModificationProcessRunner()
            completed = []
            failures = []
            loop = QEventLoop()
            runner.completed.connect(lambda *paths: (completed.append(paths), loop.quit()))
            runner.failed.connect(lambda message: (failures.append(message), loop.quit()))
            QTimer.singleShot(10_000, loop.quit)

            runner.start(
                extractions,
                assembly,
                replacement_sources=(replacement_one, replacement_three),
            )
            loop.exec()

            self.assertFalse(failures)
            self.assertEqual(len(completed), 1)
            self.assertEqual(len(completed[0][0]), 2)
            self.assertEqual(len(completed[0][1]), 2)
            self.assertGreater((root / "revised.mp4").stat().st_size, 0)
            self.assertTrue(all(Path(item).is_file() for item in completed[0][0]))

    def test_extract_replace_assemble_and_fps_export(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            subprocess.run(
                (
                    "ffmpeg",
                    "-loglevel",
                    "error",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=blue:s=64x64:r=16:d=0.3125",
                    "-frames:v",
                    "5",
                    "-pix_fmt",
                    "yuv420p",
                    str(source),
                ),
                check=True,
            )
            extracted = root / "frame-3.png"
            execute_frame_extraction(
                plan_frame_extraction(
                    ffmpeg_executable="ffmpeg",
                    source_video_path=str(source),
                    frame_index=3,
                    frame_count=5,
                    output_path=str(extracted),
                ),
                cancellation=Token(),
            )
            with Image.open(extracted) as image:
                self.assertEqual(image.size, (64, 64))

            replacement = root / "replacement.png"
            Image.new("RGB", (64, 64), "red").save(replacement)
            revised = root / "revised.mp4"
            execute_frame_revision_assembly(
                plan_frame_revision_assembly(
                    ffmpeg_executable="ffmpeg",
                    source_video_path=str(source),
                    replacement_paths={2: str(replacement)},
                    generation_fps=16,
                    frame_count=5,
                    output_path=str(revised),
                    work_directory=str(root / "revision-work"),
                ),
                cancellation=Token(),
            )
            self.assertGreater(revised.stat().st_size, 0)

            project = source_project()
            revision = project.segment_revisions[0].model_copy(
                update={"review_state": RevisionReviewState.APPROVED}
            )
            segment = project.segments[0].model_copy(
                update={
                    "state": SegmentState.APPROVED_LOCKED,
                    "current_approved_revision_id": revision.revision_id,
                }
            )
            exported = root / "export.mp4"
            export = build_export_plan(
                export_id="export-1",
                segments=(segment,),
                revisions=(revision,),
                source_paths={revision.result_asset_id: str(revised)},
                output_path=str(exported),
                output_fps=24,
                ffmpeg_executable="ffmpeg",
                work_directory=str(root / "export-work"),
                provenance_id="export-provenance",
            )
            execute_export_plan(export, cancellation=Token())
            self.assertGreater(exported.stat().st_size, 0)


if __name__ == "__main__":
    unittest.main()
