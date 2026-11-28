import json
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from tools.rescale_record import rescale_capture, rescale_record


class TestRescaleCapture(unittest.TestCase):
    def test_scales_geometry_and_sizes(self):
        meta = {
            "image_size": [1640, 1232],
            "rotation_deg": 90,
            "quads": [[[100.0, 200.0], [900.0, 220.0], [880.0, 1000.0], [120.0, 980.0]]],
            "corners": [[[100.0, 200.0], [200.0, 210.0]]],
            "size_px": {"min": 300.0, "max": 600.0},
        }
        out = rescale_capture(meta, 640 / 1640, 480 / 1232, 640, 480)
        self.assertEqual(out["image_size"], [640, 480])
        self.assertEqual(out["image_size_eval"], [640, 480])  # 旋转 90° 后宽高本来就已经对调
        self.assertAlmostEqual(out["quads"][0][0][0], 100.0 * 640 / 1640, places=3)
        self.assertAlmostEqual(out["corners"][0][1][1], 210.0 * 480 / 1232, places=3)
        self.assertAlmostEqual(out["size_px"]["max"], 600.0 * 640 / 1640, places=1)
        self.assertEqual(out["rescaled_from"]["image_size"], [1640, 1232])
        self.assertEqual(meta["image_size"], [1640, 1232])  # 不改原对象

    def test_record_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "in"; root.mkdir()
            img = np.random.default_rng(0).integers(0, 255, (1232, 1640, 3), np.uint8)
            cv2.imwrite(str(root / "000000_left.png"), img)
            cv2.imwrite(str(root / "000000_right.png"), img)
            (root / "capture.json").write_text(json.dumps({
                "image_size": [1640, 1232], "rotation_deg": 90,
                "quads": [[[0, 0], [1639, 0], [1639, 1231], [0, 1231]]],
            }), encoding="utf-8")
            (root / "pairs.jsonl").write_text("{}\n", encoding="utf-8")
            info = rescale_record(root, Path(td) / "out", 640, 480)
            self.assertEqual(info["frames"], 2)
            got = cv2.imread(str(Path(td) / "out" / "000000_left.png"))
            self.assertEqual(got.shape, (480, 640, 3))
            meta = json.loads((Path(td) / "out" / "capture.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["image_size"], [640, 480])
            self.assertTrue((Path(td) / "out" / "pairs.jsonl").exists())


if __name__ == "__main__":
    unittest.main()