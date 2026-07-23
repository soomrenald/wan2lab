"""JSON Schema generation from authoritative Python models."""

from __future__ import annotations

import json
from pathlib import Path

from wan2core.backends import BackendCapabilities
from wan2core.projects import Wan2LabProject
from wan2core.segments import SegmentRequest


def schema_bundle() -> dict[str, object]:
    return {
        "project": Wan2LabProject.model_json_schema(),
        "backend_capabilities": BackendCapabilities.model_json_schema(),
        "segment_request": SegmentRequest.model_json_schema(),
    }


def write_schema_bundle(path: Path) -> None:
    path.write_text(
        json.dumps(schema_bundle(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


__all__ = ["schema_bundle", "write_schema_bundle"]

