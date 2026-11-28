import tempfile
import unittest
from pathlib import Path
from unittest import mock

import cv2
import numpy as np

from pc.calib.calibrate import (
    CalibrationError,
    CalibrationResult,
    _drop_outliers,
    calibrate_stereo,
)
from shared.synthetic import generate_calibration_views


class TestStereoCalibration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.D = np.array([-0.06, 0.01, 0.0, 0.0, 0.0])
        cls.views = generate_calibration_views(
            n_views=14, seed=3, D_left=cls.D, D_right=cls.D, baseline=0.083
        )
        cls.calib = calibrate_stereo(
            cls.views["left"], cls.views["right"], pattern=(9, 6), square_m=0.03, min_views=10
        )

    def test_baseline_recovered(self):
        self.assertAlmostEqual(self.calib.baseline_m, 0.083, delta=0.083 * 0.015)

    def test_reprojection_and_epipolar(self):
        self.assertLess(self.calib.rms_stereo, 0.5)
        self.assertLess(self.calib.rms_mono_left, 0.3)
        epi = self.calib.meta["epipolar_dy"]["median_abs_px"]
        self.assertLess(epi, 0.2)

    def test_focal_consistent_with_depth(self):
        self.assertGreater(self.calib.focal_px, 400)
        z = self.calib.disparity_to_depth(np.array([self.calib.focal_px * self.calib.baseline_m], np.float32))
        self.assertAlmostEqual(float(z[0]), 1.0, places=4)

    def test_save_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "calib.npz"
            self.calib.save(p)
            c2 = CalibrationResult.load(p)
            self.assertAlmostEqual(c2.baseline_m, self.calib.baseline_m, places=9)
            self.assertAlmostEqual(c2.focal_px, self.calib.focal_px, places=9)
            np.testing.assert_allclose(c2.K1, self.calib.K1, rtol=1e-12)

    def test_wrong_view_count_rejected(self):
        from pc.calib.calibrate import CalibrationError

        with self.assertRaises(CalibrationError):
            calibrate_stereo(self.views["left"][:3], self.views["right"][:3], min_views=8)


class TestVerticalBaselineGuard(unittest.TestCase):
    """整套支架侧放时基线在画面里是竖直的，cv2.stereoRectify 算不出有效校正。

    这时必须报错、不写 npz —— 否则会悄悄产出一份"能加载但视差全在竖直方向"的标定。
    """

    def test_raises_when_rectified_residual_is_huge(self):
        views = generate_calibration_views(n_views=10, seed=5, baseline=0.083)
        with mock.patch(
            "pc.calib.calibrate.epipolar_dy",
            return_value={"median_abs_px": 219.0},
        ):
            with self.assertRaises(CalibrationError):
                calibrate_stereo(
                    views["left"], views["right"], pattern=(9, 6), square_m=0.03,
                    min_views=8, progress=False,
                )


class TestOutlierRejection(unittest.TestCase):
    """个别视图极线残差异常（比如某次右镜抖动）应被剔除，而不是拖累整组参数。"""

    def test_drops_gross_outlier(self):
        keep, dropped = _drop_outliers([0.1, 0.1, 0.2, 8.0, 0.1, 0.2, 0.1])
        self.assertEqual(dropped, [3])
        self.assertNotIn(3, keep)
        self.assertEqual(len(keep), 6)

    def test_keeps_everything_when_problem_is_systematic(self):
        # 六成视图残差都很大：说明是标定口径/镜头的问题，剔除只会自欺欺人
        resid = [0.1, 5.0, 6.0, 7.0, 8.0, 0.2]
        keep, dropped = _drop_outliers(resid)
        self.assertEqual(dropped, [])
        self.assertEqual(keep, list(range(len(resid))))

    def test_small_residuals_never_dropped(self):
        keep, dropped = _drop_outliers([0.2, 0.3, 0.25, 0.4])
        self.assertEqual(dropped, [])
        self.assertEqual(keep, [0, 1, 2, 3])


if __name__ == "__main__":
    unittest.main()
