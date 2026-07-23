from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image

from wan2core.mannequin.workflows import GuideKind, default_mannequin_scene
from wan2lab.mannequin import render_mannequin_guides


class MannequinRendererTests(unittest.TestCase):
    def test_renderer_writes_all_reproducible_guide_kinds(self) -> None:
        scene = default_mannequin_scene(
            scene_id="scene-1", name="Standing", width=320, height=180
        )
        with tempfile.TemporaryDirectory() as directory:
            first = render_mannequin_guides(scene, Path(directory) / "first")
            second = render_mannequin_guides(scene, Path(directory) / "second")
            self.assertEqual({item.kind for item in first}, set(GuideKind))
            for left, right in zip(first, second, strict=True):
                self.assertEqual(left.path.read_bytes(), right.path.read_bytes())
                with Image.open(left.path) as image:
                    self.assertEqual(image.size, (320, 180))


if __name__ == "__main__":
    unittest.main()
