import unittest

import cv2
import numpy as np

from pc.stereo.sgbm import SGBMMatcher, auto_num_disparities, median_filter_disparity
from pc.stereo.subpixel import refine_disparity_subpixel
from shared.config import SgbmConfig
from shared.synthetic import default_scene


class TestSGBM(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.render = default_scene().render(seed=1)
        cls.matcher = SGBMMatcher(SgbmConfig())
        cls.result = cls.matcher.compute(cls.render.left, cls.render.right)

    def test_shapes_and_validity(self):
        r = self.result
        self.assertEqual(r.disp.shape, (480, 640))
        self.assertEqual(r.disp.dtype, np.float32)
        self.assertEqual(r.valid.dtype, bool)
        self.assertGreater(float(r.coverage), 0.3)
        self.assertTrue(np.all(r.disp[~r.valid] == 0))

    def test_disparity_accuracy_within_reference(self):
        r = self.result
        sel = r.valid & self.render.valid
        self.assertGreater(int(sel.sum()), 10000)
        err = np.abs(r.disp[sel] - self.render.disp[sel])
        self.assertLess(float(np.median(err)), 0.5)
        self.assertLess(float(np.percentile(err, 90)), 1.5)

    def test_right_reference_disparity_available(self):
        r = self.result
        self.assertIsNotNone(r.right_disp)
        self.assertIsNotNone(r.right_valid)
        self.assertGreater(float(r.right_valid.mean()), 0.1)
        self.assertTrue(np.all(r.right_disp[r.right_valid] > 0))

    def test_depth_formula(self):
        f, b = 530.0, 0.083
        sel = self.result.valid & self.render.valid
        z = f * b / self.result.disp[sel]
        self.assertLess(float(np.median(np.abs(z - self.render.depth[sel]))), 0.02)

    def test_subpixel_refinement_is_bounded(self):
        gl = cv2.cvtColor(self.render.left, cv2.COLOR_BGR2GRAY)
        gr = cv2.cvtColor(self.render.right, cv2.COLOR_BGR2GRAY)
        sub = refine_disparity_subpixel(gl, gr, self.result.disp, self.result.valid)
        self.assertEqual(sub.shape, self.result.disp.shape)
        delta = np.abs(sub[self.result.valid] - self.result.disp[self.result.valid])
        self.assertLess(float(np.max(delta)), 0.51)
        sel = self.result.valid & self.render.valid
        self.assertLess(float(np.median(np.abs(sub[sel] - self.render.disp[sel]))), 1.0)

    def test_wls_optional(self):
        if not hasattr(cv2, "ximgproc"):
            self.skipTest("opencv-contrib 不可用")
        m = SGBMMatcher(SgbmConfig(), use_wls=True)
        res = m.compute(self.render.left, self.render.right)
        self.assertTrue(res.meta.get("used_wls"))
        self.assertGreater(float(res.coverage), 0.3)


class TestPostprocess(unittest.TestCase):
    def test_median_removes_isolated_spike(self):
        disp = np.full((20, 20), 10.0, np.float32)
        valid = np.ones_like(disp, dtype=bool)
        disp[10, 10] = 99.0  # 孤立尖刺
        out, out_valid = median_filter_disparity(disp, valid, 3)
        self.assertAlmostEqual(float(out[10, 10]), 10.0, places=4)
        self.assertTrue(bool(out_valid[10, 10]))

    def test_median_does_not_invent_depth_in_big_hole(self):
        disp = np.full((20, 20), 10.0, np.float32)
        valid = np.ones_like(disp, dtype=bool)
        valid[5:15, 5:15] = False  # 10x10 的大洞，超过中值窗口
        out, out_valid = median_filter_disparity(disp, valid, 3)
        self.assertFalse(bool(out_valid[10, 10]))
        self.assertEqual(float(out[10, 10]), 0.0)

    def test_median_rejects_other_ksize(self):
        with self.assertRaises(ValueError):
            median_filter_disparity(np.zeros((5, 5), np.float32), np.ones((5, 5), bool), 7)
        with self.assertRaises(ValueError):
            SgbmConfig(median_ksize=4)

    def test_median_postprocess_is_recorded(self):
        render = default_scene().render(seed=1)
        m = SGBMMatcher(SgbmConfig(postprocess="median"))
        res = m.compute(render.left, render.right)
        self.assertEqual(res.meta.get("postprocess"), "median")
        self.assertGreater(float(res.coverage), 0.3)

    def test_unknown_postprocess_rejected(self):
        with self.assertRaises(ValueError):
            SgbmConfig(postprocess="bilateral")


class TestAutoDisparity(unittest.TestCase):
    def test_rounds_up_to_multiple_of_16(self):
        # f*B/Z = 1231*0.08/1.0 = 98.5 px -> 112
        self.assertEqual(auto_num_disparities(1.0, 1231.0, 0.08), 112)

    def test_near_range_needs_more_than_default(self):
        # 0.5 m 处需要 ~197 px；默认 96 完全测不到
        self.assertEqual(auto_num_disparities(0.5, 1231.0, 0.08), 208)
        self.assertGreater(auto_num_disparities(0.5, 1231.0, 0.08), 96)

    def test_clamped_by_max(self):
        self.assertEqual(auto_num_disparities(0.05, 1231.0, 0.08, 256), 256)

    def test_half_scale_uses_working_resolution(self):
        # 全分辨率 208；0.5 倍图只需 104，向上取 16 的倍数 = 112
        self.assertEqual(auto_num_disparities(0.5, 1231.0, 0.08, scale=0.5), 112)
        # 0.35 倍：208*0.35 = 72.8 -> 80
        self.assertEqual(auto_num_disparities(0.5, 1231.0, 0.08, scale=0.35), 80)

    def test_scale_must_be_positive(self):
        with self.assertRaises(ValueError):
            auto_num_disparities(0.5, 1231.0, 0.08, scale=0.0)

    def test_rejects_bad_arguments(self):
        with self.assertRaises(ValueError):
            auto_num_disparities(0.0, 1231.0, 0.08)
        with self.assertRaises(ValueError):
            auto_num_disparities(0.5, 0.0, 0.08)
        with self.assertRaises(ValueError):
            SgbmConfig(min_depth_m=-0.1)
        with self.assertRaises(ValueError):
            SgbmConfig(max_disparities=100)


class TestWlsFromConfig(unittest.TestCase):
    def test_config_postprocess_wls_switches_it_on(self):
        if not hasattr(cv2, "ximgproc"):
            self.skipTest("opencv-contrib 不可用")
        render = default_scene().render(seed=1)
        m = SGBMMatcher(SgbmConfig(postprocess="wls", wls_lambda=4000.0, wls_sigma_color=1.2))
        res = m.compute(render.left, render.right)
        self.assertTrue(res.meta.get("used_wls"))
        self.assertEqual(res.meta.get("postprocess"), "wls")
        self.assertAlmostEqual(float(res.meta["wls_params"]["lambda"]), 4000.0)
        self.assertGreater(float(res.coverage), 0.3)


if __name__ == "__main__":
    unittest.main()
