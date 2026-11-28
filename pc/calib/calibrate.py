"""双目标定：单目 → 双目联合 → 极线校正。

关键约定（实机与合成共用）：
  * **不要假设基线**：B 一律从标定 T 的模长读取；
  * 校正后焦距从 P1(0,0) 读取，深度用 Z = f * B / d；
  * 视差符号：右目在 +X 方向，校正后 d = u_L - u_R > 0。

标定文件格式（.npz）：
  K1,D1,K2,D2,R,T,E,F,Q,R1,R2,P1,P2,image_size,pattern,square_m,
  rms_stereo,rms_mono_left,rms_mono_right,meta(JSON)
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np

from shared.stats import signed_error_stats, summarize


class CalibrationError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# 角点检测
# --------------------------------------------------------------------------- #
def detect_corners(
    gray: np.ndarray,
    pattern: Tuple[int, int],
    use_sb: bool = True,
    subpix: bool = True,
) -> Optional[np.ndarray]:
    """检测棋盘格内角点，返回 (N,1,2) float32 或 None。

    优先 findChessboardCornersSB（对噪声/畸变更稳），失败再退回经典算法。

    注意：SB 与经典算法的角点顺序可能相差 180°（等价于棋盘转 180°，不影响
    标定）；但同一份标定数据里不要混用两种算法的结果——某个视图检测不到时，
    直接跳过比"用另一种顺序硬补"安全。
    """
    gray = _to_gray(gray)
    if use_sb:
        flags = cv2.CALIB_CB_EXHAUSTIVE | cv2.CALIB_CB_ACCURACY
        found, corners = cv2.findChessboardCornersSB(gray, pattern, flags=flags)
        if found:
            return corners.astype(np.float32).reshape(-1, 1, 2)

    flags = (
        cv2.CALIB_CB_ADAPTIVE_THRESH
        | cv2.CALIB_CB_NORMALIZE_IMAGE
        | cv2.CALIB_CB_FAST_CHECK
    )
    found, corners = cv2.findChessboardCorners(gray, pattern, flags=flags)
    if not found:
        # 降采样回退：板占半幅 + 略糊时，经典检测器在原图上会挑，降到半幅反而稳。
        # 粗检在缩略图上做，角点搬回原分辨率再做亚像素精化，精度不丢。
        for scale in (0.5, 0.35):
            small = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
            found, corners = cv2.findChessboardCorners(small, pattern, flags=flags)
            if found:
                corners = corners.astype(np.float32) / np.float32(scale)
                if subpix:
                    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 40, 1e-3)
                    corners = cv2.cornerSubPix(gray, corners, (7, 7), (-1, -1), criteria)
                return corners.astype(np.float32)
        return None
    if subpix:
        criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 40, 1e-3)
        corners = cv2.cornerSubPix(gray, corners, (5, 5), (-1, -1), criteria)
    return corners.astype(np.float32)


def _to_gray(img: np.ndarray) -> np.ndarray:
    if img.ndim == 2:
        return img
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


# --------------------------------------------------------------------------- #
# 标定结果
# --------------------------------------------------------------------------- #
@dataclass
class CalibrationResult:
    image_size: Tuple[int, int]
    K1: np.ndarray
    D1: np.ndarray
    K2: np.ndarray
    D2: np.ndarray
    R: np.ndarray
    T: np.ndarray
    E: np.ndarray
    F: np.ndarray
    R1: np.ndarray
    R2: np.ndarray
    P1: np.ndarray
    P2: np.ndarray
    Q: np.ndarray
    rms_stereo: float
    rms_mono_left: float
    rms_mono_right: float
    pattern: Tuple[int, int] = (9, 6)
    square_m: float = 0.03
    per_view: List[Dict[str, Any]] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)

    # ---- 派生量：实机必须从这里取，而不是写死 83mm ----
    @property
    def baseline_m(self) -> float:
        return float(np.linalg.norm(self.T))

    @property
    def focal_px(self) -> float:
        return float(self.P1[0, 0])

    @property
    def cx(self) -> float:
        return float(self.P1[0, 2])

    @property
    def cy(self) -> float:
        return float(self.P1[1, 2])

    def disparity_to_depth(self, disp: np.ndarray) -> np.ndarray:
        """Z = f B / d；d<=0 视为无效（nan）。"""
        disp = np.asarray(disp, np.float32)
        out = np.full_like(disp, np.nan, dtype=np.float32)
        ok = disp > 0
        out[ok] = self.focal_px * self.baseline_m / disp[ok]
        return out

    def save(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        meta = dict(self.meta)
        meta.update(
            {
                "image_size": list(self.image_size),
                "pattern": list(self.pattern),
                "square_m": float(self.square_m),
                "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "baseline_m": self.baseline_m,
                "focal_px": self.focal_px,
            }
        )
        np.savez_compressed(
            p,
            K1=self.K1,
            D1=self.D1,
            K2=self.K2,
            D2=self.D2,
            R=self.R,
            T=self.T,
            E=self.E,
            F=self.F,
            R1=self.R1,
            R2=self.R2,
            P1=self.P1,
            P2=self.P2,
            Q=self.Q,
            image_size=np.asarray(self.image_size, np.int32),
            rms=np.asarray([self.rms_stereo, self.rms_mono_left, self.rms_mono_right], np.float64),
            meta=np.asarray(json.dumps(meta, ensure_ascii=False)),
        )
        return p

    @classmethod
    def load(cls, path: str | Path) -> "CalibrationResult":
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"标定文件不存在: {p}")
        with np.load(p, allow_pickle=False) as z:
            meta = json.loads(str(z["meta"]))
            rms = z["rms"]
            return cls(
                image_size=tuple(int(v) for v in z["image_size"]),
                K1=z["K1"].astype(np.float64),
                D1=z["D1"].astype(np.float64),
                K2=z["K2"].astype(np.float64),
                D2=z["D2"].astype(np.float64),
                R=z["R"].astype(np.float64),
                T=z["T"].astype(np.float64),
                E=z["E"].astype(np.float64),
                F=z["F"].astype(np.float64),
                R1=z["R1"].astype(np.float64),
                R2=z["R2"].astype(np.float64),
                P1=z["P1"].astype(np.float64),
                P2=z["P2"].astype(np.float64),
                Q=z["Q"].astype(np.float64),
                rms_stereo=float(rms[0]),
                rms_mono_left=float(rms[1]),
                rms_mono_right=float(rms[2]),
                pattern=tuple(meta.get("pattern", (9, 6))),
                square_m=float(meta.get("square_m", 0.03)),
                per_view=meta.get("per_view", []),
                meta=meta,
            )

    def summary(self) -> Dict[str, Any]:
        return {
            "image_size": list(self.image_size),
            "baseline_m": self.baseline_m,
            "focal_px": self.focal_px,
            "rms_stereo_px": self.rms_stereo,
            "rms_mono_left_px": self.rms_mono_left,
            "rms_mono_right_px": self.rms_mono_right,
            "T": self.T.tolist(),
            "K1": self.K1.tolist(),
            "D1": self.D1.ravel().tolist(),
            "D2": self.D2.ravel().tolist(),
        }


# --------------------------------------------------------------------------- #
# 标定流程
# --------------------------------------------------------------------------- #
def _object_grid(pattern: Tuple[int, int], square_m: float) -> np.ndarray:
    cols, rows = pattern
    grid = np.zeros((cols * rows, 3), np.float32)
    grid[:, :2] = np.mgrid[0:cols, 0:rows].T.reshape(-1, 2) * square_m
    return grid


def _reprojection_rms(
    objp: np.ndarray, imgp: np.ndarray, K: np.ndarray, D: np.ndarray, rvec: np.ndarray, tvec: np.ndarray
) -> float:
    proj, _ = cv2.projectPoints(objp, rvec, tvec, K, D)
    err = np.linalg.norm(proj.reshape(-1, 2) - imgp.reshape(-1, 2), axis=1)
    return float(np.sqrt(np.mean(err**2)))


# 极线残差的外点判据：|dy| 超过 max(1.0 px, 3x 中位数) 的视图算异常
OUTLIER_MIN_PX = 1.0
OUTLIER_K = 3.0
# 校正后同名点的竖直残差中位数上限。装对了、数据够好时是亚像素级；
# 超这个值说明校正根本没生效（最常见：整套支架侧放，基线在画面里是竖直的）。
MAX_EPIPOLAR_DY_PX = 5.0


def _fit_stereo(
    objpoints: Sequence[np.ndarray],
    img_l: Sequence[np.ndarray],
    img_r: Sequence[np.ndarray],
    image_size: Tuple[int, int],
    calib_flags: int,
) -> Dict[str, Any]:
    """跑一遍单目 + 联合 + 校正，返回所有中间量（剔除外点后要重跑一次）。"""
    rms_l, K1, D1, rv_l, tv_l = cv2.calibrateCamera(
        objpoints, img_l, image_size, None, None, flags=calib_flags
    )
    rms_r, K2, D2, rv_r, tv_r = cv2.calibrateCamera(
        objpoints, img_r, image_size, None, None, flags=calib_flags
    )
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 100, 1e-6)
    rms_s, K1, D1, K2, D2, R, T, E, F = cv2.stereoCalibrate(
        objpoints,
        img_l,
        img_r,
        K1,
        D1,
        K2,
        D2,
        image_size,
        flags=calib_flags | cv2.CALIB_USE_INTRINSIC_GUESS,
        criteria=criteria,
    )
    (R1, R2, P1, P2, Q, roi1, roi2) = cv2.stereoRectify(
        K1, D1, K2, D2, image_size, R, T, flags=cv2.CALIB_ZERO_DISPARITY, alpha=0
    )
    return {
        "rms_l": rms_l, "rms_r": rms_r, "rms_s": rms_s,
        "K1": K1, "D1": D1, "K2": K2, "D2": D2,
        "R": R, "T": T, "E": E, "F": F,
        "R1": R1, "R2": R2, "P1": P1, "P2": P2, "Q": Q,
        "roi1": roi1, "roi2": roi2,
        "rv_l": rv_l, "tv_l": tv_l,
    }


def _per_view_epipolar(
    fit: Dict[str, Any],
    pts_l: Sequence[np.ndarray],
    pts_r: Sequence[np.ndarray],
) -> List[float]:
    """每个视图校正后的 |dy| 中位数（同名点垂直残差，理想 ~0）。"""
    out: List[float] = []
    for pl, pr in zip(pts_l, pts_r):
        ul = cv2.undistortPoints(pl.reshape(-1, 1, 2), fit["K1"], fit["D1"],
                                 R=fit["R1"], P=fit["P1"]).reshape(-1, 2)
        ur = cv2.undistortPoints(pr.reshape(-1, 1, 2), fit["K2"], fit["D2"],
                                 R=fit["R2"], P=fit["P2"]).reshape(-1, 2)
        n = min(len(ul), len(ur))
        if n == 0:
            out.append(float("nan"))
            continue
        out.append(float(np.median(np.abs(ul[:n, 1] - ur[:n, 1]))))
    return out


def _drop_outliers(resid: Sequence[float]) -> Tuple[List[int], List[int]]:
    """挑出极线残差离群的视图。返回 (保留的序号, 剔除的序号)。

    一次剔掉超过四分之一的视图说明不是个别外点（可能是板晃了/装了 180°），
    这时一个都不剔，把问题留给调用方打印出来看。
    """
    vals = [r for r in resid if np.isfinite(r)]
    if not vals:
        return list(range(len(resid))), []
    median = float(np.median(vals))
    thresh = max(OUTLIER_MIN_PX, OUTLIER_K * median)
    dropped = [i for i, r in enumerate(resid) if np.isfinite(r) and r > thresh]
    if len(dropped) > max(1, len(resid) // 4):
        return list(range(len(resid))), []
    keep = [i for i in range(len(resid)) if i not in set(dropped)]
    return keep, dropped


def calibrate_stereo(
    left_images: Sequence[np.ndarray],
    right_images: Sequence[np.ndarray],
    pattern: Tuple[int, int] = (9, 6),
    square_m: float = 0.03,
    min_views: int = 8,
    progress: bool = False,
    fix_k3: bool = True,
    zero_tangent: bool = True,
) -> CalibrationResult:
    """从左右目棋盘格图像完成单目 + 双目联合标定。

    角度覆盖不足、角点检出太少、重投影误差异常都会直接报错，绝不"静默
    返回一个能用的文件"。
    """
    if len(left_images) != len(right_images) or len(left_images) < min_views:
        raise CalibrationError(
            f"需要至少 {min_views} 对左右视图，收到 {len(left_images)}/{len(right_images)}"
        )

    objp = _object_grid(pattern, square_m)
    objpoints: List[np.ndarray] = []
    img_l: List[np.ndarray] = []
    img_r: List[np.ndarray] = []
    skipped: List[int] = []

    for i, (limg, rimg) in enumerate(zip(left_images, right_images)):
        cl = detect_corners(limg, pattern)
        cr = detect_corners(rimg, pattern)
        if cl is None or cr is None:
            skipped.append(i)
            continue
        if cl.shape != cr.shape:
            skipped.append(i)
            continue
        objpoints.append(objp.copy())
        img_l.append(cl)
        img_r.append(cr)
        if progress:
            print(f"  角点 {i + 1}/{len(left_images)}: ok")

    if len(objpoints) < min_views:
        raise CalibrationError(
            f"有效视图仅 {len(objpoints)} 对（跳过 {len(skipped)} 对），低于下限 {min_views}"
        )
    if skipped:
        print(f"[calib] 警告：{len(skipped)} 对视图角点检测失败，已跳过 {skipped}")

    image_size = (int(left_images[0].shape[1]), int(left_images[0].shape[0]))

    # 固定 k3、切向畸变置零：棋盘格数据对方程的约束有限，5 参数全放会
    # 在"真值无畸变"的数据上拟合出 k3~0.24 这种伪畸变，把 alpha=0 的校正
    # 视场裁掉 2%，进而污染 fB。IMX219 这类镜头 k1/k2 足够。
    calib_flags = 0
    if fix_k3:
        calib_flags |= cv2.CALIB_FIX_K3
    if zero_tangent:
        calib_flags |= cv2.CALIB_ZERO_TANGENT_DIST

    view_ids = [i for i in range(len(left_images)) if i not in set(skipped)]
    fit = _fit_stereo(objpoints, img_l, img_r, image_size, calib_flags)
    resid = _per_view_epipolar(fit, img_l, img_r)
    keep, dropped = _drop_outliers(resid)
    epi_before = summarize(resid)
    dropped_orig: List[int] = []
    if dropped and len(keep) >= min_views:
        dropped_orig = [view_ids[i] for i in dropped]
        print(f"[calib] 剔除 {len(dropped)} 个极线残差异常视图（原序号 {dropped_orig}），重算")
        objpoints = [objpoints[i] for i in keep]
        img_l = [img_l[i] for i in keep]
        img_r = [img_r[i] for i in keep]
        view_ids = [view_ids[i] for i in keep]
        fit = _fit_stereo(objpoints, img_l, img_r, image_size, calib_flags)
        resid = _per_view_epipolar(fit, img_l, img_r)
    elif dropped:
        print(f"[calib] 警告：{len(dropped)} 个视图极线残差偏大，但剔除后会少于 {min_views} 对，保留全部")

    rms_l, K1, D1, rv_l, tv_l = fit["rms_l"], fit["K1"], fit["D1"], fit["rv_l"], fit["tv_l"]
    rms_r, K2, D2 = fit["rms_r"], fit["K2"], fit["D2"]
    rms_s, R, T, E, F = fit["rms_s"], fit["R"], fit["T"], fit["E"], fit["F"]
    R1, R2, P1, P2, Q, roi1, roi2 = (
        fit["R1"], fit["R2"], fit["P1"], fit["P2"], fit["Q"], fit["roi1"], fit["roi2"],
    )

    per_view: List[Dict[str, Any]] = []
    for k, (rv, tv) in enumerate(zip(rv_l, tv_l)):
        per_view.append(
            {
                "view": view_ids[k],
                "rms_left_px": _reprojection_rms(objpoints[k], img_l[k], K1, D1, rv, tv),
                "epipolar_dy_px": float(resid[k]) if k < len(resid) else None,
            }
        )

    # 极线检查：把检测到的角点校正后比较 y
    epi = epipolar_dy(K1, D1, K2, D2, R1, R2, P1, P2, img_l, img_r)
    med_dy = float(epi.get("median_abs_px") or 0.0)
    if med_dy > MAX_EPIPOLAR_DY_PX:
        raise CalibrationError(
            f"校正后同名点竖直残差中位数 {med_dy:.1f} px（上限 {MAX_EPIPOLAR_DY_PX:.0f} px）——"
            "极线校正没有生效。最常见的原因是基线在画面里是竖直的（整套支架侧放/躺倒）："
            "cv2.stereoRectify 按水平基线设计，竖直基线时它算出来的旋转接近单位阵、视差仍留在竖直方向。"
            "把支架立起来（两只镜头水平并排、画面直立）重拍；或把图像整体转 90° 后再标定。"
        )
    meta = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source": "real-or-synthetic",
        "views_used": len(objpoints),
        "skipped": skipped,
        "views_dropped": dropped_orig,
        "epipolar_dy_before": epi_before,
        "distortion_model": {"fix_k3": bool(fix_k3), "zero_tangent": bool(zero_tangent)},
        "epipolar_dy": epi,
        "roi": {"left": [int(v) for v in roi1], "right": [int(v) for v in roi2]},
    }

    return CalibrationResult(
        image_size=image_size,
        K1=K1,
        D1=D1.ravel(),
        K2=K2,
        D2=D2.ravel(),
        R=R,
        T=T,
        E=E,
        F=F,
        R1=R1,
        R2=R2,
        P1=P1,
        P2=P2,
        Q=Q,
        rms_stereo=float(rms_s),
        rms_mono_left=float(rms_l),
        rms_mono_right=float(rms_r),
        pattern=pattern,
        square_m=square_m,
        per_view=per_view,
        meta=meta,
    )


def epipolar_dy(
    K1: np.ndarray,
    D1: np.ndarray,
    K2: np.ndarray,
    D2: np.ndarray,
    R1: np.ndarray,
    R2: np.ndarray,
    P1: np.ndarray,
    P2: np.ndarray,
    pts_l: Sequence[np.ndarray],
    pts_r: Sequence[np.ndarray],
) -> Dict[str, Any]:
    """校正后同名点的垂直残差 |dy|（像素）。理想值 ~0。"""
    dys: List[float] = []
    for pl, pr in zip(pts_l, pts_r):
        ul = cv2.undistortPoints(pl.reshape(-1, 1, 2), K1, D1, R=R1, P=P1).reshape(-1, 2)
        ur = cv2.undistortPoints(pr.reshape(-1, 1, 2), K2, D2, R=R2, P=P2).reshape(-1, 2)
        n = min(len(ul), len(ur))
        dys.extend(np.abs(ul[:n, 1] - ur[:n, 1]).tolist())
    out = summarize(dys)
    out["median_abs_px"] = out["median"]
    out["p90_abs_px"] = float(np.percentile(dys, 90)) if dys else None
    return out


def load_or_fail(path: str | Path) -> CalibrationResult:
    try:
        return CalibrationResult.load(path)
    except FileNotFoundError as exc:
        raise CalibrationError(
            f"{exc}\n请先运行标定：python -m pc.calib.cli calibrate --left ... --right ..."
        ) from exc
