"""Local immutable asset storage adapter."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from uuid import uuid4

from PIL import Image

from k2core.assets import AssetRecord


class LocalAssetStore:
    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().resolve()

    def register_imported(
        self,
        source: Path,
        *,
        media_type: str,
        metadata: dict[str, object] | None = None,
    ) -> AssetRecord:
        return self._copy(source, media_type=media_type, parent_asset_ids=(), metadata=metadata)

    def create_derived(
        self,
        source: Path,
        *,
        parent_asset_ids: tuple[str, ...],
        media_type: str,
        metadata: dict[str, object] | None = None,
    ) -> AssetRecord:
        if not parent_asset_ids:
            raise ValueError("derived assets require at least one parent")
        return self._copy(
            source,
            media_type=media_type,
            parent_asset_ids=parent_asset_ids,
            metadata=metadata,
        )

    def resolve(self, asset: AssetRecord) -> Path:
        resolved = (self.root / asset.relative_path).resolve()
        if self.root not in resolved.parents:
            raise ValueError("asset path escapes the local store")
        return resolved

    def verify(self, asset: AssetRecord) -> bool:
        path = self.resolve(asset)
        return path.is_file() and _sha256(path) == asset.sha256

    def _copy(
        self,
        source: Path,
        *,
        media_type: str,
        parent_asset_ids: tuple[str, ...],
        metadata: dict[str, object] | None,
    ) -> AssetRecord:
        resolved = source.expanduser().resolve()
        if not resolved.is_file():
            raise FileNotFoundError(resolved)
        digest = _sha256(resolved)
        asset_id = f"asset-{uuid4().hex}"
        suffix = resolved.suffix.casefold()
        relative = Path("objects") / digest[:2] / f"{asset_id}{suffix}"
        destination = self.root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise FileExistsError(destination)
        shutil.copy2(resolved, destination)
        width = height = None
        if media_type.startswith("image/"):
            with Image.open(destination) as image:
                width, height = image.size
        return AssetRecord(
            asset_id=asset_id,
            relative_path=relative.as_posix(),
            sha256=digest,
            media_type=media_type,
            byte_size=destination.stat().st_size,
            width=width,
            height=height,
            parent_asset_ids=parent_asset_ids,
            metadata=metadata or {},
        )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


__all__ = ["LocalAssetStore"]

