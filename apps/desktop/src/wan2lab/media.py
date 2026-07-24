"""Safe cancellable execution of planned FFmpeg media operations."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Callable, Protocol

from wan2core.editing.workflows import FrameExtractionPlan, FrameRevisionAssemblyPlan
from wan2core.export import ExportPlan


class Cancellation(Protocol):
    @property
    def cancelled(self) -> bool: ...


def execute_frame_extraction(
    plan: FrameExtractionPlan,
    *,
    cancellation: Cancellation,
) -> Path:
    output = Path(plan.output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    _run(plan.arguments, cancellation=cancellation)
    if not output.is_file() or output.stat().st_size == 0:
        raise RuntimeError("FFmpeg did not create the extracted frame")
    return output


def execute_frame_revision_assembly(
    plan: FrameRevisionAssemblyPlan,
    *,
    cancellation: Cancellation,
    progress: Callable[[str, int, int], None] | None = None,
) -> Path:
    frame_directory = Path(plan.frame_directory)
    frame_directory.mkdir(parents=True, exist_ok=True)
    output = Path(plan.output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    total = len(plan.replacements) + 2
    _run(plan.extract_arguments, cancellation=cancellation)
    if progress:
        progress("extract_frames", 1, total)
    for index, replacement in enumerate(plan.replacements, start=2):
        if cancellation.cancelled:
            raise InterruptedError("frame revision assembly cancelled")
        source = Path(replacement.source_path)
        destination = Path(replacement.destination_path)
        if not source.is_file():
            raise FileNotFoundError(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        if progress:
            progress("replace_frame", index, total)
    _run(plan.encode_arguments, cancellation=cancellation)
    if progress:
        progress("encode_revision", total, total)
    if not output.is_file() or output.stat().st_size == 0:
        raise RuntimeError("FFmpeg did not create the revised segment")
    return output


def execute_export_plan(
    plan: ExportPlan,
    *,
    cancellation: Cancellation,
    progress: Callable[[str, int, int], None] | None = None,
) -> Path:
    if not plan.commands:
        raise ValueError("export plan contains no commands")
    concat = plan.commands[-1]
    try:
        manifest_path = Path(concat.arguments[concat.arguments.index("-i") + 1])
    except (ValueError, IndexError) as error:
        raise ValueError("concat command does not identify its manifest") from error
    if len(plan.concat_manifest_entries) != len(plan.segment_inputs):
        raise ValueError("concat manifest and segment inputs differ in length")
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        "".join(
            f"file '{_concat_escape(path)}'\n"
            f"duration {segment.duration_ms / 1000:.9g}\n"
            for path, segment in zip(
                plan.concat_manifest_entries,
                plan.segment_inputs,
                strict=True,
            )
        ),
        encoding="utf-8",
    )
    total = len(plan.commands)
    for index, command in enumerate(plan.commands, start=1):
        Path(command.output_path).parent.mkdir(parents=True, exist_ok=True)
        _run(command.arguments, cancellation=cancellation)
        if progress:
            progress(command.stage, index, total)
    output = Path(plan.output_path)
    if not output.is_file() or output.stat().st_size == 0:
        raise RuntimeError("FFmpeg did not create the export")
    return output


def _run(arguments: tuple[str, ...], *, cancellation: Cancellation) -> None:
    if not arguments or any("\x00" in item for item in arguments):
        raise ValueError("invalid process argument array")
    process = subprocess.Popen(  # noqa: S603 - trusted executable is an explicit project setting
        arguments,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    while process.poll() is None:
        if cancellation.cancelled:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
            raise InterruptedError(f"media process cancelled: {arguments[0]}")
        try:
            process.wait(timeout=0.1)
        except subprocess.TimeoutExpired:
            continue
    _stdout, stderr = process.communicate()
    if process.returncode != 0:
        message = (stderr or "").strip()[-4_000:]
        raise RuntimeError(f"media process failed ({process.returncode}): {message}")


def _concat_escape(path: str) -> str:
    if "\n" in path or "\r" in path:
        raise ValueError("concat paths cannot contain newlines")
    return path.replace("\\", "/").replace("'", "'\\''")


__all__ = [
    "execute_export_plan",
    "execute_frame_extraction",
    "execute_frame_revision_assembly",
]
