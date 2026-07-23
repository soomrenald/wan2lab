"""Local immutable asset storage adapter."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from uuid import uuid4

from PIL import Image

from k2core.assets import AssetRecord
from wan2core.assets import AssetRef


def image_media_type(path: Path) -> str:
    with Image.open(path.expanduser().resolve()) as image:
        media_type = Image.MIME.get(image.format or "")
    if media_type is None:
        raise ValueError(f"unsupported image format: {path}")
    return media_type


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

    def register_generated(
        self,
        source: Path,
        *,
        media_type: str,
        parent_asset_ids: tuple[str, ...] = (),
        metadata: dict[str, object] | None = None,
    ) -> AssetRecord:
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

    def resolve_ref(self, asset: AssetRef) -> Path:
        resolved = (self.root / asset.storage_path).resolve()
        if self.root not in resolved.parents:
            raise ValueError("asset path escapes the local store")
        if not resolved.is_file():
            raise FileNotFoundError(resolved)
        if _sha256(resolved) != asset.sha256:
            raise ValueError(f"asset hash mismatch: {asset.asset_id}")
        return resolved

    def copy_to(self, target_root: Path, assets: tuple[AssetRef, ...]) -> "LocalAssetStore":
        target = LocalAssetStore(target_root)
        for asset in assets:
            source = self.resolve_ref(asset)
            destination = (target.root / asset.storage_path).resolve()
            if target.root not in destination.parents:
                raise ValueError("asset path escapes target local store")
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                if _sha256(destination) != asset.sha256:
                    raise FileExistsError(f"different asset already exists: {destination}")
                continue
            shutil.copy2(source, destination)
        return target

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


class LocalComfyAssetBridge:
    """Stages immutable project inputs and resolves typed local ComfyUI outputs."""

    def __init__(self, input_root: Path, output_root: Path) -> None:
        self.input_root = input_root.expanduser().resolve()
        self.output_root = output_root.expanduser().resolve()

    def stage_input(self, store: LocalAssetStore, asset: AssetRef) -> str:
        source = store.resolve_ref(asset)
        relative = Path("wan2lab") / asset.sha256[:2] / f"{asset.asset_id}{source.suffix}"
        destination = (self.input_root / relative).resolve()
        if self.input_root not in destination.parents:
            raise ValueError("staged input path escapes the ComfyUI input directory")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            if _sha256(destination) != asset.sha256:
                raise FileExistsError(f"different staged input already exists: {destination}")
        else:
            shutil.copy2(source, destination)
        return relative.as_posix()

    def resolve_output(self, storage_key: str) -> Path:
        parts = storage_key.replace("\\", "/").split("/")
        if len(parts) < 2 or parts[0] != "output" or ".." in parts:
            raise ValueError("ComfyUI result must be a safe persistent output key")
        resolved = (self.output_root / Path(*parts[1:])).resolve()
        if self.output_root not in resolved.parents or not resolved.is_file():
            raise FileNotFoundError(resolved)
        return resolved


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


__all__ = ["LocalAssetStore", "LocalComfyAssetBridge", "image_media_type"]
