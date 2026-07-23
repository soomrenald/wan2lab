from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image

from wan2lab.assets import LocalAssetStore


class LocalAssetStoreTests(unittest.TestCase):
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
