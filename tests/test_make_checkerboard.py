"""tools.make_checkerboard 的尺寸计算。"""
import unittest

from tools.make_checkerboard import PAGES_MM, board_extent_mm, mm_to_px, page_px


class TestGeometry(unittest.TestCase):
    def test_extent_9x6_30mm(self):
        # 9x6 内角点 -> 10x7 格；1 格白边 -> 12x9 格
        w, h = board_extent_mm(9, 6, 30.0, margin_squares=1.0)
        self.assertAlmostEqual(w, 360.0)
        self.assertAlmostEqual(h, 270.0)

    def test_extent_fits_a3(self):
        w, h = board_extent_mm(9, 6, 30.0)
        page_w, page_h = PAGES_MM["a3"]
        self.assertLess(w, page_w)
        self.assertLess(h, page_h)

    def test_25mm_fits_a4_with_half_margin(self):
        # 30 mm 方格 + 1 格白边在 A4 上放不下（300 > 297），25 mm + 半格白边可以
        w, h = board_extent_mm(9, 6, 25.0, margin_squares=0.5)
        page_w, page_h = PAGES_MM["a4"]
        self.assertLess(w, page_w)
        self.assertLess(h, page_h)
        with self.assertRaises(ValueError):
            from tools.make_checkerboard import render
            render(9, 6, 25.0, "a4", margin_squares=1.0)

    def test_px_roundtrip(self):
        self.assertAlmostEqual(mm_to_px(25.4, 300), 300.0)
        self.assertEqual(page_px("a4", 300), (3508, 2480))


if __name__ == "__main__":
    unittest.main()
