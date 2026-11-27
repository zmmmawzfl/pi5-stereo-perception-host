"""合成一组 9x6 标定板图（左/右），用来在没有硬件时验证整条标定链路。

为什么还要一个生成器：`shared.synthetic.generate_calibration_views` 是为 640x480
写的（K 里写死 fx=530），换到 1640x1232 就采不到可用位姿。这个工具把焦距按分辨率
缩放，并**先把真值写进 truth.json**，标定完可以直接对答案：

    python -m tools.synth_calib_views --out runs/synth_calib/views
    python -m pc.calib.cli calibrate --left runs/synth_calib/views/left \
        --right runs/synth_calib/views/right --out runs/synth_calib/calib.npz

2026-11-28 实测（1640x1232、f=1400 px、基线 80 mm、18 个位姿，Mac / cv2 5.1）：
标定回来 **f=1400.014 px、baseline=80.007 mm、stereo RMS=0.0597 px**，
说明「读图 -> 检测角点 -> 标定 -> 保存 npz」这条路本身是通的，
真机上标定不好只可能是图/板/装法的问题。

渲染方式：板是平的，透视变换对平面是精确的，所以把一张高分辨率棋盘纹理
按四个外角做 `cv2.warpPerspective` 就等价于相机拍到它（不含畸变）。
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from typing import Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np

DEFAULT_FOCAL_PX = 1400.0   # 1640x1232 下 IMX219（f≈3.04 mm、像元 2.24 um）约 1360 px
TEXTURE_PPM = 6000.0        # 棋盘纹理的分辨率（px/m）


def board_texture(square_m: float, cols: int, rows: int, ppm: float = TEXTURE_PPM) -> np.ndarray:
    """画 (cols+1) x (rows+1) 个方格的黑白棋盘，左上角是黑格，白边留一格。"""
    sq = max(int(round(square_m * ppm)), 4)
    n_col, n_row = cols + 1, rows + 1
    img = np.full((n_row * sq, n_col * sq), 255, np.uint8)
    for r in range(n_row):
        for c in range(n_col):
            if (r + c) % 2 == 0:
                img[r * sq:(r + 1) * sq, c * sq:(c + 1) * sq] = 0
    return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)


def object_points(square_m: float, cols: int, rows: int) -> np.ndarray:
    """内角点在板坐标系里的位置（行优先，与 OpenCV 的角点顺序一致）。"""
    return np.array(
        [[i * square_m, j * square_m, 0.0] for j in range(rows) for i in range(cols)],
        np.float64,
    )


def render_view(
    K: np.ndarray,
    rvec: np.ndarray,
    tvec: np.ndarray,
    texture: np.ndarray,
    square_m: float,
    cols: int,
    rows: int,
    background: int = 70,
) -> np.ndarray:
    """把平面棋盘按位姿投影到画面上（平面透视变换，无畸变）。"""
    height, width = int(K[1, 2] * 2) + 1, int(K[0, 2] * 2) + 1
    R, _ = cv2.Rodrigues(np.asarray(rvec, np.float64))
    t = np.asarray(tvec, np.float64).reshape(1, 3)
    # 板的外框（含最外一圈方格）
    outs = [(-square_m, -square_m), (cols * square_m, -square_m),
            (cols * square_m, rows * square_m), (-square_m, rows * square_m)]
    dst = []
    for u, v in outs:
        p = np.array([[u, v, 0.0]]) @ R.T + t
        q = K @ p.T
        dst.append((q[0, 0] / q[2, 0], q[1, 0] / q[2, 0]))
    src = np.array([[0, 0], [texture.shape[1], 0],
                    [texture.shape[1], texture.shape[0]], [0, texture.shape[0]]], np.float32)
    M = cv2.getPerspectiveTransform(src, np.array(dst, np.float32))
    canvas = np.full((height, width, 3), background, np.uint8)
    warped = cv2.warpPerspective(texture, M, (width, height), flags=cv2.INTER_AREA,
                                 borderValue=(background,) * 3)
    mask = cv2.warpPerspective(np.full(texture.shape[:2], 255, np.uint8), M, (width, height),
                               flags=cv2.INTER_NEAREST, borderValue=0)
    canvas[mask > 0] = warped[mask > 0]
    return canvas


def _pose_ok(K: np.ndarray, rvec: np.ndarray, tvec: np.ndarray, pts: np.ndarray,
             baseline_m: float, min_span_frac: float, margin: int) -> bool:
    """左右两只眼都要看全整块板，且板在画面里不能太小。"""
    height, width = int(K[1, 2] * 2) + 1, int(K[0, 2] * 2) + 1
    R, _ = cv2.Rodrigues(np.asarray(rvec, np.float64))
    for dx in (0.0, -baseline_m):
        t = np.asarray(tvec, np.float64) + np.array([dx, 0.0, 0.0])
        p = pts @ R.T + t.reshape(1, 3)
        q = (K @ p.T).T
        if np.any(q[:, 2] <= 0.05):
            return False
        uv = q[:, :2] / q[:, 2:3]
        if uv[:, 0].min() < margin or uv[:, 0].max() > width - margin:
            return False
        if uv[:, 1].min() < margin or uv[:, 1].max() > height - margin:
            return False
        if max(np.ptp(uv[:, 0]), np.ptp(uv[:, 1])) < min_span_frac * min(width, height):
            return False
    return True


def render_stereo_views(
    n_views: int = 20,
    image_size: Tuple[int, int] = (1640, 1232),
    focal_px: float = DEFAULT_FOCAL_PX,
    baseline_m: float = 0.080,
    square_m: float = 0.030,
    pattern: Tuple[int, int] = (9, 6),
    seed: int = 11,
    dist_range: Optional[Tuple[float, float]] = None,
    min_span_frac: float = 0.26,
) -> Dict[str, object]:
    """生成 n_views 对左右图（BGR ndarray）+ 真值。位姿随机但保证整块板都在画面里。

    dist_range=None 时按分辨率/焦距自己算：让板在最近处约 0.55×短边、最远处约 0.29×，
    这样 800x600 与 1640x1232 都能用同一套默认值。
    """
    cols, rows = pattern
    width, height = image_size
    if dist_range is None:
        board_w = (cols + 1) * square_m
        z_center = board_w * focal_px / (0.55 * min(width, height))
        dist_range = (0.80 * z_center, 1.90 * z_center)
    K = np.array([[focal_px, 0.0, (width - 1) / 2.0],
                  [0.0, focal_px, (height - 1) / 2.0],
                  [0.0, 0.0, 1.0]], np.float64)
    texture = board_texture(square_m, cols, rows)
    pts = object_points(square_m, cols, rows)
    rng = np.random.default_rng(seed)
    left: List[np.ndarray] = []
    right: List[np.ndarray] = []
    poses: List[Dict[str, List[float]]] = []
    tries = 0
    # 目标落点按 3x3 分格轮流取（体检 tools.check_calib 要求 8/9 格都有），
    # 这样合成数据也能过一遍体检流程，而不是只在画面中央打转。
    # want_u/want_v 是「两眼的中点」落在哪：左眼 +b/2*f/z、右眼 -b/2*f/z，
    # 两只都得看全整块板，所以本钱得靠拉远（dist_range 上界 1.3 m）换。
    cells = [(c, r) for r in range(3) for c in range(3)]
    rng.shuffle(cells)  # type: ignore[arg-type]
    board_c = pts.mean(axis=0)
    while len(left) < n_views and tries < 8000:
        tries += 1
        z = rng.uniform(*dist_range)
        col, row = cells[len(left) % len(cells)]
        want_u = (col + 0.5) / 3.0 * (width - 1)
        want_v = (row + 0.5) / 3.0 * (height - 1)
        rvec = np.array([math.radians(rng.uniform(-28, 28)),
                         math.radians(rng.uniform(-32, 32)),
                         math.radians(rng.uniform(-18, 18))], np.float64)
        R = cv2.Rodrigues(rvec)[0]
        # 让「板中心」投影到 (want_u, want_v) 的两眼中点
        tvec = np.array([(want_u - (width - 1) / 2.0) * z / focal_px,
                         (want_v - (height - 1) / 2.0) * z / focal_px, z], np.float64) \
            - R @ board_c
        if not _pose_ok(K, rvec, tvec, pts, baseline_m, min_span_frac, margin=8):
            continue
        left.append(render_view(K, rvec, tvec, texture, square_m, cols, rows))
        # 右相机在左相机 +x 方向、朝向相同：板相对右相机的平移 t' = t - (b,0,0)
        right.append(render_view(K, rvec, tvec - np.array([baseline_m, 0.0, 0.0]),
                                 texture, square_m, cols, rows))
        poses.append({"rvec": rvec.tolist(), "tvec": tvec.tolist()})
    if len(left) < n_views:
        raise RuntimeError(f"只采到 {len(left)}/{n_views} 个可用位姿，请放宽 --dist 或 --min-span")
    truth = {
        "fx": focal_px, "fy": focal_px,
        "cx": (width - 1) / 2.0, "cy": (height - 1) / 2.0,
        "baseline_m": baseline_m, "square_m": square_m,
        "pattern": list(pattern), "image_size": [width, height],
        "views": len(left), "seed": seed,
    }
    return {"left": left, "right": right, "truth": truth, "poses": poses,
            "K": K, "object_points": pts}


def save_views(out_dir: str, views: Dict[str, object]) -> str:
    """按 <out>/left/left_000.png + <out>/right/right_000.png + truth.json 落盘。"""
    for side in ("left", "right"):
        os.makedirs(os.path.join(out_dir, side), exist_ok=True)
    for i, (l, r) in enumerate(zip(views["left"], views["right"])):
        cv2.imwrite(os.path.join(out_dir, "left", f"left_{i:03d}.png"), l)
        cv2.imwrite(os.path.join(out_dir, "right", f"right_{i:03d}.png"), r)
    path = os.path.join(out_dir, "truth.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(views["truth"], fh, ensure_ascii=False, indent=2)
    return path


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="生成 9x6 合成标定板左右图（带真值）")
    ap.add_argument("--out", required=True, help="输出目录（会建 left/ right/ 与 truth.json）")
    ap.add_argument("--views", type=int, default=20,
                    help="位姿数，默认 20（标定要 >= 12；体检 check_calib 建议 >= 20）")
    ap.add_argument("--width", type=int, default=1640)
    ap.add_argument("--height", type=int, default=1232)
    ap.add_argument("--focal-px", type=float, default=DEFAULT_FOCAL_PX,
                    help="焦距（px）；1640x1232 的 IMX219 约 1360，默认 1400")
    ap.add_argument("--baseline-mm", type=float, default=80.0)
    ap.add_argument("--square-mm", type=float, default=30.0)
    ap.add_argument("--pattern", default="9x6", help="内角点数，默认 9x6")
    ap.add_argument("--seed", type=int, default=11)
    args = ap.parse_args(argv)

    cols, rows = (int(v) for v in args.pattern.lower().split("x"))
    views = render_stereo_views(
        n_views=args.views, image_size=(args.width, args.height),
        focal_px=args.focal_px, baseline_m=args.baseline_mm / 1000.0,
        square_m=args.square_mm / 1000.0, pattern=(cols, rows), seed=args.seed,
    )
    path = save_views(args.out, views)
    print(f"生成 {views['truth']['views']} 对图 -> {args.out}（真值写在 {path}）")
    print(f"  真值：f={args.focal_px:.1f} px  baseline={args.baseline_mm:.1f} mm  "
          f"{args.width}x{args.height}")
    print("下一步：")
    print(f"  python -m pc.calib.cli calibrate --left {args.out}/left --right {args.out}/right "
          f"--pattern {args.pattern} --square-mm {args.square_mm:g} --min-views 12 "
          f"--out {args.out}/calib.npz")
    return 0


if __name__ == "__main__":
    sys.exit(main())
