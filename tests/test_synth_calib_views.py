"""合成标定板图 -> 标定 -> 对真值：验证整条标定链路没有尺度/顺序错误。"""
from __future__ import annotations

import json
import os
import platform
import tempfile
import unittest

import numpy as np

from pc.calib.calibrate import calibrate_stereo
from tools.synth_calib_views import render_stereo_views, save_views

# 标定时用的是 findChessboardCornersSB，Pi 5 上单张就要几十秒（实测 820x616 两分钟不返回），
# 所以只在非 ARM 机器上默认跑；Pi 上要跑就 RUN_CALIB_IT=1。
SLOW_MACHINE = platform.machine().lower() in ("aarch64", "armv7l")

FOCAL = 680.0
BASELINE = 0.080
SQUARE = 0.030
PATTERN = (9, 6)


class SynthCalibViewsTest(unittest.TestCase):
    @unittest.skipIf(SLOW_MACHINE and os.environ.get("RUN_CALIB_IT") != "1",
                     "SB 检测器在这台 ARM 机器上太慢；RUN_CALIB_IT=1 可强制跑")
    def test_render_and_calibrate_recovers_truth(self):
        views = render_stereo_views(
            n_views=10, image_size=(800, 600), focal_px=FOCAL, baseline_m=BASELINE,
            square_m=SQUARE, pattern=PATTERN, seed=5,
            dist_range=(0.35, 0.60), min_span_frac=0.50,
        )
        self.assertEqual(len(views["left"]), 10)
        self.assertEqual(views["left"][0].shape, (600, 800, 3))

        calib = calibrate_stereo(
            views["left"], views["right"], pattern=PATTERN, square_m=SQUARE,
            min_views=8, progress=False,
        )
        # 焦距 1%、基线 2%：够严到能抓「板纹理尺寸 / 基线方向写错」这类 bug
        # （写错时实测偏 5%~11%）
        self.assertAlmostEqual(calib.focal_px / FOCAL, 1.0, delta=0.01)
        self.assertAlmostEqual(calib.baseline_m / BASELINE, 1.0, delta=0.02)
        self.assertLess(calib.rms_stereo, 0.5)

    def test_save_views_writes_truth_and_pngs(self):
        views = render_stereo_views(
            n_views=3, image_size=(640, 480), focal_px=540.0, baseline_m=BASELINE,
            square_m=SQUARE, pattern=PATTERN, seed=7,
            dist_range=(0.35, 0.55), min_span_frac=0.50,
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = save_views(tmp, views)
            self.assertTrue(os.path.exists(path))
            with open(path, encoding="utf-8") as fh:
                truth = json.load(fh)
            self.assertEqual(truth["views"], 3)
            self.assertAlmostEqual(truth["baseline_m"], BASELINE)
            self.assertEqual(len(os.listdir(os.path.join(tmp, "left"))), 3)
            self.assertEqual(len(os.listdir(os.path.join(tmp, "right"))), 3)


if __name__ == "__main__":
    unittest.main()
