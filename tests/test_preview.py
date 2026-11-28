"""预览服务器与合成图测试（不起相机、不连 Pi）。"""
from __future__ import annotations

import json
import time
import unittest
import urllib.error
import urllib.request
from types import SimpleNamespace

import numpy as np

from pc.receive.preview import PreviewServer, composite, depth_colormap


def fake_result(frame_id=1, h=24, w=32):
    left = np.zeros((h, w, 3), np.uint8)
    depth = np.ones((h, w), np.float32)
    depth[:, : w // 2] = 0.0
    valid = depth > 0
    return SimpleNamespace(
        frame_id=frame_id,
        rectified_left=left,
        depth=depth,
        fused=SimpleNamespace(valid=valid, coverage=float(valid.mean())),
        objects=[],
        timings_ms={"total": 42.0},
    )


class CompositeTest(unittest.TestCase):
    def test_two_panels_plus_status_bar(self):
        img = composite(fake_result(), calib=None, scale=1.0)
        self.assertEqual(img.shape, (24 + 28, 64, 3))
        self.assertEqual(img.dtype, np.uint8)

    def test_scale_applies(self):
        img = composite(fake_result(), calib=None, scale=0.5)
        self.assertEqual(img.shape[0], (24 + 28) // 2)

    def test_grayscale_left_is_promoted(self):
        res = fake_result()
        res.rectified_left = np.zeros((24, 32), np.uint8)
        img = composite(res, calib=None)
        self.assertEqual(img.shape[2], 3)

    def test_depth_colormap_leaves_invalid_black(self):
        vis = depth_colormap(np.array([[0.0, 1.0], [2.0, 0.0]], np.float32))
        self.assertEqual(vis.shape, (2, 2, 3))
        self.assertEqual(vis[0, 0].tolist(), [0, 0, 0])
        self.assertGreater(int(vis[0, 1].sum()), 0)


class PreviewServerTest(unittest.TestCase):
    def setUp(self):
        self.srv = PreviewServer(host="127.0.0.1", port=0, quality=70, title="t").start()
        self.base = "http://127.0.0.1:{}".format(self.srv.port)

    def tearDown(self):
        self.srv.close()

    def test_page_snapshot_and_stats(self):
        self.srv.update(np.zeros((16, 24, 3), np.uint8),
                        {"pair_id": 7, "fps": 12.5, "coverage_pct": 31.0,
                         "total_ms": 57.0})
        with urllib.request.urlopen(self.base + "/", timeout=5) as r:
            page = r.read().decode("utf-8")
        self.assertIn("stream", page)
        self.assertIn("snapshot.jpg", page)

        with urllib.request.urlopen(self.base + "/snapshot.jpg", timeout=5) as r:
            jpg = r.read()
            ctype = r.headers["Content-Type"]
        self.assertTrue(ctype.startswith("image/jpeg"))
        self.assertEqual(jpg[:2], b"\xff\xd8")

        with urllib.request.urlopen(self.base + "/stats.json", timeout=5) as r:
            stats = json.loads(r.read().decode("utf-8"))
        self.assertEqual(stats["pair_id"], 7)
        self.assertAlmostEqual(stats["fps"], 12.5)

    def test_snapshot_before_first_frame_is_503(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(self.base + "/snapshot.jpg", timeout=5)
        self.assertEqual(ctx.exception.code, 503)

    def test_unknown_path_is_404(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(self.base + "/nope", timeout=5)
        self.assertEqual(ctx.exception.code, 404)

    def test_updates_without_clients(self):
        for i in range(5):
            self.srv.update(np.full((8, 8, 3), i, np.uint8), {"pair_id": i})
            time.sleep(0.01)
