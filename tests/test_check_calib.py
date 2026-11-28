"""tools.check_calib 的判定逻辑。"""
import unittest

import numpy as np

from tools.check_calib import (
    cell_of,
    coverage,
    decide,
    duplicate_pairs,
    evaluate_pair,
    quad_metrics,
    size_span,
)


def _verdict(i, cx=150.0, cy=120.0, size=600.0, sharp=1000.0, touch=False, mismatch=0.0,
             fill=0.5):
    return {
        "index": i, "ok": True, "found_left": True, "found_right": True,
        "n_left": 54, "n_right": 54, "expected": 54,
        "size_px": size, "center": [cx, cy], "fill_frac": fill,
        "touch_edge": touch, "size_mismatch_pct": mismatch,
        "sharp_left": sharp, "sharp_right": sharp,
    }


def _spread(n=15, ratio=2.0):
    """n 张铺满 9 格，尺寸从 size 到 size*ratio。"""
    out = []
    for k in range(n):
        c, r = k % 3, (k // 3) % 3
        size = 400.0 * (ratio ** (k / max(n - 1, 1)))
        out.append(_verdict(k, (c + 0.5) * 100.0, (r + 0.5) * 80.0, size=size))
    return out


class TestGeometry(unittest.TestCase):
    def test_square(self):
        quad = np.array([[0, 0], [100, 0], [100, 100], [0, 100]], float)
        m = quad_metrics(quad)
        self.assertAlmostEqual(m["area_px"], 10000.0)
        self.assertAlmostEqual(m["size_px"], 100.0)
        self.assertAlmostEqual(m["persp_ratio"], 1.0, places=6)
        self.assertAlmostEqual(m["edge_ratio"], 1.0, places=6)
        self.assertAlmostEqual(m["cx"], 50.0)
        self.assertAlmostEqual(m["cy"], 50.0)

    def test_perspective_grows_when_tilted(self):
        # 近边 200 px、远边 60 px：上小下大
        quad = np.array([[70, 0], [130, 0], [200, 100], [0, 100]], float)
        m = quad_metrics(quad)
        self.assertGreater(m["persp_ratio"], 1.3)
        self.assertAlmostEqual(m["edge_ratio"], 200.0 / 60.0, places=3)

    def test_cell_of(self):
        self.assertEqual(cell_of(10, 10, 300, 240), (0, 0))
        self.assertEqual(cell_of(150, 120, 300, 240), (1, 1))
        self.assertEqual(cell_of(299, 239, 300, 240), (2, 2))


class TestCoverage(unittest.TestCase):
    def test_nine_cells(self):
        self.assertEqual(coverage(_spread(9), 300, 240)["n_cells"], 9)

    def test_center_only(self):
        vs = [_verdict(i, 150, 120) for i in range(5)]
        g = coverage(vs, 300, 240)
        self.assertEqual(g["n_cells"], 1)
        self.assertEqual(g["grid"][1][1], 5)

    def test_rejected_not_counted(self):
        bad = _verdict(9, 150, 120)
        bad["ok"] = False
        self.assertEqual(coverage([bad], 300, 240)["n_cells"], 0)

    def test_corner_can_reach_corner_cell(self):
        # 板中心还在中间格，但一个角伸到了右下角：按角点算要覆盖到角格
        v = _verdict(0, 150, 120)
        v["corners_left"] = [[150, 120], [299, 239]]
        g = coverage([v], 300, 240)
        self.assertEqual(g["n_cells"], 2)
        self.assertEqual(g["grid"][2][2], 1)

    def test_corner_points_beat_centre(self):
        # 同一个"板中心在正中"的姿态：中心法只算 1 格，角点法能算到四角和本身
        v = _verdict(0, 150, 120)
        v["corners_left"] = [[20, 20], [280, 20], [280, 220], [20, 220], [150, 120]]
        g = coverage([v], 300, 240)
        self.assertEqual(g["n_cells"], 5)
        for col, row in ((0, 0), (2, 0), (2, 2), (0, 2), (1, 1)):
            self.assertEqual(g["grid"][row][col], 1)


class TestDuplicates(unittest.TestCase):
    def _quad(self, dx=0.0):
        q = np.array([[0, 0], [100, 0], [100, 80], [0, 80]], float)
        return (q + np.array([dx, 0.0])).tolist()

    def test_identical_flagged(self):
        vs = [_verdict(0), _verdict(1)]
        for v in vs:
            v["quad"] = self._quad()
        self.assertEqual(len(duplicate_pairs(vs)), 1)

    def test_moved_is_not_duplicate(self):
        vs = [_verdict(0), _verdict(1)]
        vs[0]["quad"] = self._quad(0.0)
        vs[1]["quad"] = self._quad(40.0)  # 位移 40% 板宽
        self.assertEqual(duplicate_pairs(vs), [])

    def test_flipped_order_still_duplicate(self):
        # 角点顺序反 180 度（经典检测与 SB 的常见差异）不该算两张
        vs = [_verdict(0), _verdict(1)]
        vs[0]["quad"] = self._quad()
        vs[1]["quad"] = self._quad()[::-1]
        self.assertEqual(len(duplicate_pairs(vs)), 1)


class TestDecide(unittest.TestCase):
    def test_good_batch_passes(self):
        vs = _spread(15)
        grid = coverage(vs, 300, 240)
        d = decide(vs, grid, duplicate_pairs(vs), min_views=15)
        self.assertTrue(d["passed"], d["reasons"])

    def test_too_few(self):
        vs = _spread(8)
        grid = coverage(vs, 300, 240)
        d = decide(vs, grid, [], min_views=15)
        self.assertFalse(d["passed"])
        self.assertTrue(any("可用帧对" in r for r in d["reasons"]))

    def test_bad_coverage(self):
        vs = [_verdict(i, 150, 120, size=400.0 + i) for i in range(16)]
        grid = coverage(vs, 300, 240)
        d = decide(vs, grid, [], min_views=15)
        self.assertFalse(d["passed"])
        self.assertTrue(any("覆盖" in r for r in d["reasons"]))

    def test_small_size_span(self):
        vs = _spread(15, ratio=1.05)
        grid = coverage(vs, 300, 240)
        d = decide(vs, grid, [], min_views=15)
        self.assertFalse(d["passed"])
        self.assertTrue(any("尺寸跨度" in r for r in d["reasons"]))

    def test_warns_but_passes_on_edge_and_blur(self):
        vs = _spread(15)
        vs[0]["touch_edge"] = True
        vs[1]["sharp_left"] = vs[1]["sharp_right"] = 10.0
        grid = coverage(vs, 300, 240)
        d = decide(vs, grid, duplicate_pairs(vs), min_views=15)
        self.assertTrue(d["passed"], d["reasons"])
        self.assertTrue(any("边缘" in w for w in d["warnings"]))
        self.assertTrue(any("糊" in w for w in d["warnings"]))

    def test_size_span(self):
        vs = _spread(5, ratio=3.0)
        span = size_span(vs)
        self.assertAlmostEqual(span["ratio"], 3.0, places=3)

    def test_board_too_small_fails(self):
        vs = _spread(15)
        for v in vs:
            v["fill_frac"] = 0.20
        grid = coverage(vs, 300, 240)
        d = decide(vs, grid, [], min_views=15)
        self.assertFalse(d["passed"])
        self.assertTrue(any("板太小" in r for r in d["reasons"]))
        self.assertAlmostEqual(d["max_fill"], 0.20, places=3)

    def test_small_fill_only_warns(self):
        vs = _spread(15)
        for v in vs:
            v["fill_frac"] = 0.40
        grid = coverage(vs, 300, 240)
        d = decide(vs, grid, [], min_views=15)
        self.assertTrue(d["passed"], d["reasons"])
        self.assertTrue(any("还能再近一点" in w for w in d["warnings"]))


class TestRotationSource(unittest.TestCase):
    """采集时如果转过画面（capture.json 的 rotation_deg），体检要用同一个角度。"""

    def test_reads_rotation_from_capture_json(self):
        import json
        import tempfile
        from pathlib import Path

        from tools.check_calib import _resolve_rotation

        class Args:
            rotate = None
            root = None

        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "capture.json").write_text(json.dumps({"rotation_deg": 90}))
            Args.root = tmp
            self.assertEqual(_resolve_rotation(Args()), (90, "capture.json"))
            Args.rotate = 270
            self.assertEqual(_resolve_rotation(Args()), (270, "--rotate"))
            Args.rotate = None
            Args.root = None
            self.assertEqual(_resolve_rotation(Args()), (0, "默认"))


class TestEndToEnd(unittest.TestCase):
    def test_detects_synthetic_pair(self):
        # 同一块板给两台相机各渲染一次（右目沿 -x 平移一个基线），两只眼都该检出
        from shared.synthetic import render_checkerboard

        w, h, fx = 640, 480, 530.0
        K = np.array([[fx, 0, (w - 1) / 2], [0, fx, (h - 1) / 2], [0, 0, 1]], float)
        ang = np.deg2rad(20)
        R = np.array([[1, 0, 0], [0, np.cos(ang), -np.sin(ang)], [0, np.sin(ang), np.cos(ang)]], float)
        left, _ = render_checkerboard(K, np.zeros(5), R, np.array([0.0, 0.0, 0.45]), 9, 6, 0.03, (w, h))
        right, _ = render_checkerboard(K, np.zeros(5), R, np.array([-0.083, 0.0, 0.45]), 9, 6, 0.03, (w, h))
        rec = evaluate_pair(left, right, (9, 6), "classic", 1.0, 0)
        self.assertTrue(rec["ok"], rec["reasons"])
        self.assertEqual(rec["n_left"], 54)
        self.assertEqual(rec["n_right"], 54)
        self.assertEqual(len(rec["quad"]), 4)

    def test_missing_board_is_reported(self):
        blank = np.full((480, 640, 3), 255, np.uint8)
        rec = evaluate_pair(blank, blank, (9, 6), "classic", 1.0, 0)
        self.assertFalse(rec["ok"])
        self.assertEqual(rec["reasons"], ["左目未检出", "右目未检出"])


if __name__ == "__main__":
    unittest.main()
