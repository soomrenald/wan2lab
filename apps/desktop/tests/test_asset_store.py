from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image

from wan2core.assets import AssetKind, AssetRef
from wan2lab.assets import LocalAssetStore, LocalComfyAssetBridge, image_media_type


class LocalAssetStoreTests(unittest.TestCase):
    def test_local_comfy_bridge_stages_verified_inputs_and_rejects_unsafe_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.png"
            Image.new("RGB", (10, 10), "blue").save(source)
            store = LocalAssetStore(root / "assets")
            record = store.register_imported(source, media_type="image/png")
            asset = AssetRef(
                asset_id=record.asset_id,
                kind=AssetKind.IMAGE,
                storage_path=record.relative_path,
                sha256=record.sha256,
                width=record.width,
                height=record.height,
            )
            bridge = LocalComfyAssetBridge(root / "input", root / "output")

            staged = bridge.stage_input(store, asset)
            self.assertEqual((root / "input" / staged).read_bytes(), source.read_bytes())
            output = root / "output" / "wan2lab" / "result.mp4"
            output.parent.mkdir(parents=True)
            output.write_bytes(b"video")
            self.assertEqual(
                bridge.resolve_output("output/wan2lab/result.mp4"),
                output,
            )
            with self.assertRaises(ValueError):
                bridge.resolve_output("output/../secret")

    def test_image_media_type_uses_file_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "misleading.png"
            Image.new("RGB", (12, 8), "orange").save(source, format="JPEG")
            self.assertEqual(image_media_type(source), "image/jpeg")

    def test_import_copies_and_hash_verifies_immutable_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.png"
            Image.new("RGB", (32, 48), "orange").save(source)
            store = LocalAssetStore(root / "store")
            asset = store.register_imported(source, media_type="image/png")
            destination = store.resolve(asset)
            self.assertNotEqual(destination, source)
            self.assertEqual((asset.width, asset.height), (32, 48))
            self.assertTrue(store.verify(asset))
            source.write_bytes(b"changed")
            self.assertTrue(store.verify(asset))

    def test_generated_assets_may_record_parentage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "guide.png"
            Image.new("RGB", (16, 9), "black").save(source)
            store = LocalAssetStore(root / "store")
            asset = store.register_generated(
                source,
                media_type="image/png",
                parent_asset_ids=("scene-source",),
            )
            self.assertEqual(asset.parent_asset_ids, ("scene-source",))
            self.assertTrue(store.verify(asset))


if __name__ == "__main__":
    unittest.main()
