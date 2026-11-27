"""标定采集的逐张体检：每对帧能否都检出 9x6 棋盘、9 格覆盖够不够。

采完就急着标定，最常见的坑是"拍了 20 对，标定说有效视图只有 9 对"——
因为 pc.calib.calibrate 会**静默跳过**检不出角点的帧，等它报错时板子已经收走了。
这个工具就是提前把这件事说清楚：哪张废、为什么废、还缺哪个方位。

体检（采集目录，pairs.jsonl + frames/）::

    python -m tools.check_calib --root data/recordings/calib_board \
        --out runs/calib_check.json --montage runs/calib_coverage.png

左右目录（没用录制器时的散图）::

    python -m tools.check_calib --left data/calib/left --right data/calib/right

快速模式（半分辨率 + 经典检测，20 对不到 2 秒；正式标定前先跑一遍）::

    python -m tools.check_calib --root ... --scale 0.5

退出码 0 = 可以开标定，1 = 不合格（原因打在最后一行）。

检测器说明：默认 classic（findChessboardCorners + cornerSubPix，快）。
标定时用的是 SB（findChessboardCornersSB，更宽容但慢得多，Pi 上会超时）。
classic 没检出而 SB 能检出的边界情况会被这里判成废片 —— 宁可多拍一张，
也不要拿边界片去标定。--detector sb 可以按标定的口径复核。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np

# ---- 验收线（改这里就等于改标准）----
MIN_VIEWS = 15          # 可用帧对下限；calibrate 的 --min-views 默认 10，但 15 更稳
WARN_VIEWS = 20         # 低于这个数只提示"建议多拍"，不算不合格
COVERAGE_MIN_CELLS = 8  # 9 格里至少要占 8 格（按"角点落在哪格"算，见 coverage）
SIZE_RATIO_MIN = 1.5    # 最近的棋盘 / 最远的棋盘 >= 1.5 倍，否则距离没拉开
DUP_MAX = 2             # 允许最多 2 张"几乎同一个姿态"的重复
EDGE_MARGIN_PX = 4      # 棋盘顶点离画面边缘小于这个距离算"出画"
SHARP_WARN_RATIO = 0.5  # 锐度低于中位数一半的给提示
# 棋盘占画面宽度的比例。太小 = 焦距/距离/畸变互相能抵消，标出来的参数不可信。
# 目标是 45%~55%；A3 板（300 mm 宽）在 1640 宽、f~1400 时要离 0.65~0.75 m。
FILL_MIN = 0.35         # 低于这个直接判不合格
FILL_GOOD = 0.45        # 低于这个只提示"还能更近一点"

CLASSIC_FLAGS = (
    cv2.CALIB_CB_ADAPTIVE_THRESH
    | cv2.CALIB_CB_NORMALIZE_IMAGE
    | cv2.CALIB_CB_FAST_CHECK
)


# --------------------------------------------------------------------------- #
# 检测
# --------------------------------------------------------------------------- #
def to_gray(img: np.ndarray) -> np.ndarray:
    return img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def detect(
    gray: np.ndarray,
    pattern: Tuple[int, int],
    detector: str = "classic",
) -> Optional[np.ndarray]:
    """返回 (N,1,2) float32 或 None。detector: classic / sb / auto。"""
    if detector in ("sb", "auto"):
        flags = cv2.CALIB_CB_ACCURACY
        found, corners = cv2.findChessboardCornersSB(gray, pattern, flags=flags)
        if found:
            return corners.astype(np.float32).reshape(-1, 1, 2)
        if detector == "sb":
            return None
    found, corners = cv2.findChessboardCorners(gray, pattern, flags=CLASSIC_FLAGS)
    if not found:
        return None
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 40, 1e-3)
    corners = cv2.cornerSubPix(gray, corners, (5, 5), (-1, -1), criteria)
    return corners.astype(np.float32)


# --------------------------------------------------------------------------- #
# 几何指标
# --------------------------------------------------------------------------- #
def board_width_px(quad: np.ndarray) -> float:
    """棋盘长边在画面里的长度（px）：q0-q1 是长边方向，q2-q3 是它的对边。"""
    q = np.asarray(quad, np.float64)
    return float(0.5 * (np.linalg.norm(q[1] - q[0]) + np.linalg.norm(q[2] - q[3])))


def quad_from_corners(corners: np.ndarray, pattern: Tuple[int, int]) -> np.ndarray:
    """取出棋盘最外圈四个角（顺序与 corners 的行优先顺序一致）。"""
    cols, rows = pattern
    pts = np.asarray(corners, np.float32).reshape(-1, 2)
    idx = [0, cols - 1, cols * rows - 1, cols * (rows - 1)]
    return pts[idx]


def _cross2(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """二维叉积的 z 分量（np.cross 在 numpy 2.x 对 (N,2) 已弃用）。"""
    return a[..., 0] * b[..., 1] - a[..., 1] * b[..., 0]


def quad_metrics(quad: np.ndarray) -> Dict[str, float]:
    """面积、表观尺寸、中心、边长比、透视压缩比。"""
    q = np.asarray(quad, np.float64)
    area = 0.5 * abs(_cross2(q, np.roll(q, -1, axis=0)).sum())
    size = float(np.sqrt(max(area, 1e-9)))
    center = q.mean(axis=0)
    edges = np.linalg.norm(np.roll(q, -1, axis=0) - q, axis=1)
    edge_ratio = float(edges.max() / max(edges.min(), 1e-9))
    # 透视压缩比：两条对角线把四边形分成 4 个三角形，正视时四块面积相等，
    # 越倾斜（远小近大）差别越大。1.0 = 正对，>1.3 = 明显倾斜。
    o = q.mean(axis=0)
    tri = []
    for i in range(4):
        a, b = q[i], q[(i + 1) % 4]
        tri.append(0.5 * abs(_cross2(b - a, o - a)))
    tri = np.asarray(tri, np.float64)
    persp = float(tri.max() / max(tri.min(), 1e-9))
    return {
        "area_px": float(area),
        "size_px": size,
        "cx": float(center[0]),
        "cy": float(center[1]),
        "edge_ratio": edge_ratio,
        "persp_ratio": persp,
    }


def sharpness(gray: np.ndarray, quad: np.ndarray) -> float:
    """棋盘区域内的 Laplacian 方差（越小越糊）。"""
    h, w = gray.shape[:2]
    x0 = max(int(quad[:, 0].min()) - 2, 0)
    x1 = min(int(quad[:, 0].max()) + 3, w)
    y0 = max(int(quad[:, 1].min()) - 2, 0)
    y1 = min(int(quad[:, 1].max()) + 3, h)
    if x1 - x0 < 8 or y1 - y0 < 8:
        return 0.0
    roi = gray[y0:y1, x0:x1]
    mask = np.zeros(roi.shape, np.uint8)
    poly = (quad - np.array([x0, y0])).astype(np.int32)
    cv2.fillConvexPoly(mask, poly, 255)
    lap = cv2.Laplacian(roi, cv2.CV_64F)
    vals = lap[mask > 0]
    return float(vals.var()) if vals.size else 0.0


def touches_edge(quad: np.ndarray, width: int, height: int) -> bool:
    return bool(
        quad[:, 0].min() < EDGE_MARGIN_PX
        or quad[:, 1].min() < EDGE_MARGIN_PX
        or quad[:, 0].max() > width - 1 - EDGE_MARGIN_PX
        or quad[:, 1].max() > height - 1 - EDGE_MARGIN_PX
    )


# --------------------------------------------------------------------------- #
# 覆盖与重复
# --------------------------------------------------------------------------- #
def cell_of(cx: float, cy: float, width: int, height: int) -> Tuple[int, int]:
    """棋盘中心落在 3x3 的哪一格：返回 (列, 行)，行 0 = 上。"""
    col = min(int(cx / (width / 3.0)), 2)
    row = min(int(cy / (height / 3.0)), 2)
    return max(col, 0), max(row, 0)


def coverage(verdicts: Sequence[Dict], width: int, height: int) -> Dict:
    """3x3 覆盖：按**检测到的角点**落在哪格算（没有角点数据时退回棋盘中心）。

    为什么不按棋盘中心：角格在物理上到不了 —— A3 板要占画面一半宽度，
    板中心最多只能偏轴半幅的 49%，于是中心法永远缺四角，而缺的恰好是
    畸变最需要约束的画幅边缘。按角点算，"板上一个角伸进画面左下"就算数。
    """
    grid = [[0] * 3 for _ in range(3)]
    for v in verdicts:
        if not v["ok"]:
            continue
        pts = v.get("corners_left")
        if pts:
            hit = {cell_of(float(x), float(y), width, height) for x, y in pts}
        else:
            hit = {cell_of(v["center"][0], v["center"][1], width, height)}
        for col, row in hit:
            grid[row][col] += 1
    cells = [[c, r] for r in range(3) for c in range(3) if grid[r][c] > 0]
    return {"grid": grid, "cells": cells, "n_cells": len(cells)}


def duplicate_pairs(verdicts: Sequence[Dict]) -> List[Tuple[int, int, float]]:
    """姿态几乎相同的帧对（按棋盘四角平均位移 / 尺寸 判断）。"""
    acc = [v for v in verdicts if v["ok"] and v.get("quad")]
    out: List[Tuple[int, int, float]] = []
    for i in range(len(acc)):
        for j in range(i + 1, len(acc)):
            a = np.asarray(acc[i]["quad"], np.float64)
            b = np.asarray(acc[j]["quad"], np.float64)
            scale = 0.5 * (acc[i]["size_px"] + acc[j]["size_px"])
            d1 = np.linalg.norm(a - b, axis=1).mean() / max(scale, 1e-9)
            d2 = np.linalg.norm(a - b[::-1], axis=1).mean() / max(scale, 1e-9)
            d = float(min(d1, d2))
            if d < 0.05:
                out.append((acc[i]["index"], acc[j]["index"], d))
    return out


# --------------------------------------------------------------------------- #
# 逐对评估
# --------------------------------------------------------------------------- #
def _downscale(gray: np.ndarray, scale: float) -> np.ndarray:
    if not scale or scale == 1.0:
        return gray
    size = (max(int(gray.shape[1] * scale), 40), max(int(gray.shape[0] * scale), 40))
    return cv2.resize(gray, size, interpolation=cv2.INTER_AREA)


def evaluate_pair(
    left: np.ndarray,
    right: np.ndarray,
    pattern: Tuple[int, int],
    detector: str = "classic",
    scale: float = 1.0,
    index: int = 0,
) -> Dict:
    """一对帧 → 一条记录。坏帧也返回完整字段，方便定位是"没拍进去"还是"糊了"。"""
    gl, gr = to_gray(left), to_gray(right)
    inv = 1.0 / scale if scale and scale > 0 else 1.0
    cl = detect(_downscale(gl, scale), pattern, detector)
    cr = detect(_downscale(gr, scale), pattern, detector)
    expected = pattern[0] * pattern[1]

    rec: Dict = {
        "index": index,
        "ok": False,
        "found_left": cl is not None,
        "found_right": cr is not None,
        "n_left": 0 if cl is None else int(len(cl)),
        "n_right": 0 if cr is None else int(len(cr)),
        "expected": expected,
        "image_size": [int(left.shape[1]), int(left.shape[0])],
        "reasons": [],
    }
    if not rec["found_left"]:
        rec["reasons"].append("左目未检出")
    if not rec["found_right"]:
        rec["reasons"].append("右目未检出")
    if rec["found_left"] and rec["n_left"] != expected:
        rec["reasons"].append(f"左目角点数 {rec['n_left']}!={expected}")
    if rec["found_right"] and rec["n_right"] != expected:
        rec["reasons"].append(f"右目角点数 {rec['n_right']}!={expected}")
    if rec["reasons"]:
        return rec

    # 角点换回原图坐标，后续所有几何量都在原图坐标系里算
    ql = quad_from_corners(cl, pattern) * inv
    qr = quad_from_corners(cr, pattern) * inv
    ml, mr = quad_metrics(ql), quad_metrics(qr)
    size_px = 0.5 * (ml["size_px"] + mr["size_px"])
    cx = 0.5 * (ml["cx"] + mr["cx"])
    cy = 0.5 * (ml["cy"] + mr["cy"])
    rec.update(
        {
            "ok": True,
            "quad": [[round(float(x), 2), round(float(y), 2)] for x, y in qr],
            "quad_left": [[round(float(x), 2), round(float(y), 2)] for x, y in ql],
            "size_px": float(size_px),
            "center": [float(cx), float(cy)],
            # 全部内角点（原图坐标），覆盖统计和拼图都靠它
            "corners_left": [
                [round(float(x), 2), round(float(y), 2)]
                for x, y in np.asarray(cl, np.float64).reshape(-1, 2) * inv
            ],
            "board_w_px": float(0.5 * (board_width_px(ql) + board_width_px(qr))),
            "fill_frac": float(board_width_px(ql) / max(left.shape[1], 1)),
            "persp_ratio": float(0.5 * (ml["persp_ratio"] + mr["persp_ratio"])),
            "edge_ratio": float(0.5 * (ml["edge_ratio"] + mr["edge_ratio"])),
            # 锐度一律在原图上算，跨帧才可比
            "sharp_left": sharpness(gl, ql),
            "sharp_right": sharpness(gr, qr),
            "touch_edge": bool(
                touches_edge(ql, left.shape[1], left.shape[0])
                or touches_edge(qr, left.shape[1], left.shape[0])
            ),
            "size_mismatch_pct": float(
                abs(ml["size_px"] - mr["size_px"]) / max(size_px, 1e-9) * 100.0
            ),
        }
    )
    return rec


def decide(
    verdicts: Sequence[Dict],
    grid: Dict,
    dups: Sequence[Tuple[int, int, float]],
    min_views: int = MIN_VIEWS,
) -> Dict:
    """把体检结果收敛成"能不能开标定"。"""
    ok = [v for v in verdicts if v["ok"]]
    reasons: List[str] = []
    warnings: List[str] = []

    if len(ok) < min_views:
        reasons.append(f"可用帧对 {len(ok)} < {min_views}")
    elif len(ok) < WARN_VIEWS:
        warnings.append(f"可用帧对 {len(ok)}，建议补到 {WARN_VIEWS} 张以上")

    if grid["n_cells"] < COVERAGE_MIN_CELLS:
        reasons.append(
            f"角点只覆盖了 {grid['n_cells']}/9 格（要求 >= {COVERAGE_MIN_CELLS}）："
            f"画面边缘要有棋盘角伸进去"
        )
    if grid["grid"][1][1] == 0:
        reasons.append("画面正中一格没有角点：至少要有 2 张棋盘放在正中")

    fills = [float(v["fill_frac"]) for v in ok if "fill_frac" in v]
    best_fill = max(fills) if fills else 0.0
    if fills and best_fill < FILL_MIN:
        reasons.append(
            f"板太小：最大只占画面宽度的 {best_fill * 100:.0f}%（要求 >= {FILL_MIN * 100:.0f}%），"
            f"把板拿近，让 300 mm 的长边横跨半个画面"
        )
    elif fills and best_fill < FILL_GOOD:
        warnings.append(
            f"板最大占画面宽度 {best_fill * 100:.0f}%，还能再近一点（目标 >= {FILL_GOOD * 100:.0f}%）"
        )

    if len(ok) >= 2:
        span = size_span(verdicts)
        if span["ratio"] < SIZE_RATIO_MIN:
            reasons.append(
                f"尺寸跨度只有 {span['ratio']:.2f}x（{span['min']:.0f}..{span['max']:.0f} px，"
                f"要求 >= {SIZE_RATIO_MIN}）：远近没拉开"
            )
    if len(dups) > DUP_MAX:
        reasons.append(f"有 {len(dups)} 组几乎重复的姿态（允许 <= {DUP_MAX}）")

    edge_hit = [v["index"] for v in ok if v["touch_edge"]]
    if edge_hit:
        warnings.append(f"棋盘贴到画面边缘，标定时可能被判废：{edge_hit}")

    big_mismatch = [v["index"] for v in ok if v["size_mismatch_pct"] > 12.0]
    if big_mismatch:
        warnings.append(f"左右目棋盘尺寸差 >12%（对焦/遮挡可疑）：{big_mismatch}")

    if ok:
        med = float(np.median([0.5 * (v["sharp_left"] + v["sharp_right"]) for v in ok]))
        blurry = [
            v["index"]
            for v in ok
            if 0.5 * (v["sharp_left"] + v["sharp_right"]) < SHARP_WARN_RATIO * med
        ]
        if blurry:
            warnings.append(f"明显比其他帧糊（运动模糊/失焦）：{blurry}")

    left_miss = sum(1 for v in verdicts if not v["found_left"])
    right_miss = sum(1 for v in verdicts if not v["found_right"])
    if right_miss >= 3 and right_miss > 2 * max(left_miss, 1):
        warnings.append(
            f"右目失败 {right_miss} 次、左目 {left_miss} 次 —— 先查右目对焦/曝光/是否被挡"
        )

    return {
        "passed": not reasons,
        "n_total": len(verdicts),
        "n_ok": len(ok),
        "max_fill": round(best_fill, 4),
        "reasons": reasons,
        "warnings": warnings,
    }


def size_span(verdicts: Sequence[Dict]) -> Dict[str, float]:
    sizes = sorted(v["size_px"] for v in verdicts if v["ok"])
    if not sizes:
        return {"min": 0.0, "max": 0.0, "ratio": 0.0}
    return {"min": sizes[0], "max": sizes[-1], "ratio": sizes[-1] / max(sizes[0], 1e-9)}


# --------------------------------------------------------------------------- #
# 拼图（一眼看覆盖）
# --------------------------------------------------------------------------- #
def write_montage(
    path: str,
    lefts: Sequence[np.ndarray],
    verdicts: Sequence[Dict],
    summary: Dict,
    grid: Dict,
    cols: int = 4,
    thumb: Tuple[int, int] = (300, 225),
) -> str:
    """每张左目缩略图 + 检出的棋盘边框 + 右上角覆盖示意图。文字只用 ASCII。"""
    n = len(lefts)
    if n == 0:
        return ""
    tw, th = thumb
    rows = (n + cols - 1) // cols
    head = 92
    canvas = np.full((head + rows * th + 10, cols * tw, 3), 246, np.uint8)
    span = summary.get("size_span", {})
    line1 = "calib check: %d/%d usable   cells %d/9   size span %.2fx   %s" % (
        summary["verdict"]["n_ok"],
        summary["verdict"]["n_total"],
        grid["n_cells"],
        span.get("ratio", 0.0),
        "PASS" if summary["verdict"]["passed"] else "FAIL",
    )
    color = (30, 120, 30) if summary["verdict"]["passed"] else (20, 20, 200)
    cv2.putText(canvas, line1, (12, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.78, color, 2, cv2.LINE_AA)
    line2 = "grid: 3x3 where the detected corners landed   (red border = this frame is unusable)"
    cv2.putText(canvas, line2, (12, 62), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (90, 90, 90), 1, cv2.LINE_AA)

    # 右上角 3x3 覆盖示意
    cell = 20
    gx0, gy0 = cols * tw - 3 * cell - 16, 14
    for r in range(3):
        for c in range(3):
            hit = grid["grid"][r][c] > 0
            p0 = (gx0 + c * cell, gy0 + r * cell)
            p1 = (p0[0] + cell - 2, p0[1] + cell - 2)
            cv2.rectangle(canvas, p0, p1, (60, 160, 60) if hit else (215, 215, 215), -1)

    for i, img in enumerate(lefts):
        r, c = divmod(i, cols)
        x0, y0 = c * tw, head + r * th
        tile = cv2.resize(img, (tw, th), interpolation=cv2.INTER_AREA)
        v = verdicts[i]
        if v.get("quad_left"):
            q = np.asarray(v["quad_left"], np.float64) * np.array(
                [tw / img.shape[1], th / img.shape[0]]
            )
            cv2.polylines(tile, [q.astype(np.int32)], True,
                          (40, 180, 40) if v["ok"] else (30, 30, 220), 2)
        label = "#%02d  %s" % (i, "ok" if v["ok"] else "FAIL")
        if v["ok"]:
            label += "  %.0fpx  %.0f%%" % (v["size_px"], v.get("fill_frac", 0.0) * 100.0)
            for x, y in v.get("corners_left", []):
                cv2.circle(tile, (int(x * tw / img.shape[1]), int(y * th / img.shape[0])), 1,
                           (60, 200, 60), -1)
        # 标签压在图内左下角（底部一条浅底色保证可读），不额外占行高
        cv2.rectangle(tile, (0, th - 22), (tw - 1, th - 1), (236, 236, 236), -1)
        cv2.putText(tile, label, (8, th - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.46,
                    (40, 40, 40) if v["ok"] else (30, 30, 200), 1, cv2.LINE_AA)
        canvas[y0:y0 + th, x0:x0 + tw] = tile
        cv2.rectangle(canvas, (x0, y0), (x0 + tw - 1, y0 + th - 1), (200, 200, 200), 1)

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(path, canvas)
    return path


# --------------------------------------------------------------------------- #
# 输入
# --------------------------------------------------------------------------- #
_IMG_EXT = (".png", ".jpg", ".jpeg", ".bmp")


def load_pairs(args) -> Tuple[List[np.ndarray], List[np.ndarray], List[str]]:
    """返回 (左图列表, 右图列表, 每对的名字)。"""
    lefts: List[np.ndarray] = []
    rights: List[np.ndarray] = []
    names: List[str] = []
    if args.root:
        from shared.recording import PairReplay

        replay = PairReplay(args.root)
        for i, (rec, left, right) in enumerate(replay):
            if args.max_pairs and i >= args.max_pairs:
                break
            lefts.append(left)
            rights.append(right)
            names.append("seq%06d" % rec.seq)
        return lefts, rights, names
    if not (args.left and args.right):
        raise SystemExit("要么给 --root，要么同时给 --left 和 --right")
    lf = sorted(p for p in Path(args.left).iterdir() if p.suffix.lower() in _IMG_EXT)
    rf = sorted(p for p in Path(args.right).iterdir() if p.suffix.lower() in _IMG_EXT)
    if len(lf) != len(rf):
        raise SystemExit(f"左右图数量不一致: {len(lf)} vs {len(rf)}")
    for i, (a, b) in enumerate(zip(lf, rf)):
        if args.max_pairs and i >= args.max_pairs:
            break
        la = cv2.imread(str(a), cv2.IMREAD_COLOR)
        rb = cv2.imread(str(b), cv2.IMREAD_COLOR)
        if la is None or rb is None:
            raise SystemExit(f"读不了: {a} / {b}")
        lefts.append(la)
        rights.append(rb)
        names.append(a.stem)
    return lefts, rights, names


# --------------------------------------------------------------------------- #
# 报告
# --------------------------------------------------------------------------- #
def print_report(verdicts, grid, dups, summary, width, height) -> None:
    print(f"画面 {width}x{height}   帧对 {len(verdicts)}   检测器 {summary['detector']}   缩放 {summary['scale']}")
    print("")
    for v in verdicts:
        head = f"  #{v['index']:02d} {'ok  ' if v['ok'] else 'FAIL'}"
        if v["ok"]:
            print(
                f"{head} L{v['n_left']} R{v['n_right']}  {v['size_px']:5.0f}px  "
                f"占宽{v.get('fill_frac', 0.0) * 100:3.0f}%  "
                f"倾斜{v['persp_ratio']:.2f}  锐度{v['sharp_left']:5.0f}/{v['sharp_right']:5.0f}  "
                f"中心({v['center'][0] / width:.2f},{v['center'][1] / height:.2f})"
                + ("  [贴边]" if v["touch_edge"] else "")
                + ("  [左右尺寸差 %.0f%%]" % v["size_mismatch_pct"] if v["size_mismatch_pct"] > 12 else "")
            )
        else:
            print(f"{head} {' / '.join(v['reasons'])}")
    print("")
    marks = "●"
    print("覆盖 3x3（检测到的角点落在哪格，行 0 = 上）")
    for r in range(3):
        row = "".join(
            (marks if grid["grid"][r][c] else "·") + (f"{grid['grid'][r][c]}" if grid["grid"][r][c] else " ") + "  "
            for c in range(3)
        )
        print("      " + row)
    span = size_span(verdicts)
    fills = [v["fill_frac"] for v in verdicts if v["ok"] and "fill_frac" in v]
    fill_txt = f"   占宽 最大 {max(fills) * 100:.0f}%（目标 >= {FILL_GOOD * 100:.0f}%）" if fills else ""
    print(f"   格子 {grid['n_cells']}/9   尺寸 {span['min']:.0f}..{span['max']:.0f} px（跨度 {span['ratio']:.2f}x）{fill_txt}")
    if dups:
        print(f"   重复姿态 {len(dups)} 组: " + ", ".join(f"#{i}/#{j}" for i, j, _ in dups))
    if any(not v["ok"] for v in verdicts):
        bad = [v["index"] for v in verdicts if not v["ok"]]
        print(f"   废片 {len(bad)} 张: {bad}")
    print("")
    for w in summary["verdict"]["warnings"]:
        print(f"  提示: {w}")
    if summary["verdict"]["passed"]:
        print(f"结论: 可以开标定（{summary['verdict']['n_ok']} 张可用）")
    else:
        print("结论: 先别标定")
        for r in summary["verdict"]["reasons"]:
            print(f"  - {r}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="tools.check_calib",
        description="标定采集的逐张体检：哪些帧对能用、9 格覆盖够不够。",
    )
    ap.add_argument("--root", help="录制目录（pairs.jsonl + frames/）")
    ap.add_argument("--left", help="左目图目录")
    ap.add_argument("--right", help="右目图目录")
    ap.add_argument("--pattern", default="9x6", help="内角点，默认 9x6")
    ap.add_argument("--square-mm", type=float, default=30.0, help="方格边长 mm（只写进报告）")
    ap.add_argument("--detector", default="classic", choices=("classic", "sb", "auto"),
                    help="classic 快；sb/auto 与标定口径一致但慢得多")
    ap.add_argument("--scale", type=float, default=1.0, help="降采样比例，0.5 = 半分辨率")
    ap.add_argument("--min-views", type=int, default=MIN_VIEWS, help=f"可用帧对下限，默认 {MIN_VIEWS}")
    ap.add_argument("--max-pairs", type=int, default=0, help="只体检前 N 对")
    ap.add_argument("--out", help="写 JSON 报告")
    ap.add_argument("--montage", help="写拼图 PNG")
    args = ap.parse_args(argv)

    cols, rows = (int(x) for x in args.pattern.lower().split("x"))
    pattern = (cols, rows)
    lefts, rights, names = load_pairs(args)
    if not lefts:
        raise SystemExit("没读到任何帧对")
    width, height = lefts[0].shape[1], lefts[0].shape[0]
    sizes = {(im.shape[1], im.shape[0]) for im in lefts}

    verdicts = [
        evaluate_pair(l, r, pattern, args.detector, args.scale, i)
        for i, (l, r) in enumerate(zip(lefts, rights))
    ]
    grid = coverage(verdicts, width, height)
    dups = duplicate_pairs(verdicts)
    decision = decide(verdicts, grid, dups, args.min_views)
    summary = {
        "pattern": [cols, rows],
        "square_mm": args.square_mm,
        "detector": args.detector,
        "scale": args.scale,
        "image_size": [width, height],
        "n_pairs": len(verdicts),
        "coverage": grid,
        "size_span": size_span(verdicts),
        "duplicates": [list(map(float, d)) for d in dups],
        "verdict": decision,
        "pairs": verdicts,
    }
    print_report(verdicts, grid, dups, summary, width, height)
    if len(sizes) > 1:
        print(f"  警告: 帧分辨率不一致 {sorted(sizes)}")
    if args.montage:
        p = write_montage(args.montage, lefts, verdicts, summary, grid)
        print(f"  拼图: {p}")
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  报告: {args.out}")
    print("")
    print("下一步:")
    if args.root:
        print(f"  python -m pc.calib.cli calibrate --replay-root {args.root} "
              f"--pattern {args.pattern} --square-mm {args.square_mm:g} --min-views {args.min_views}")
    else:
        print(f"  python -m pc.calib.cli calibrate --left {args.left} --right {args.right} "
              f"--pattern {args.pattern} --square-mm {args.square_mm:g} --min-views {args.min_views}")
    return 0 if decision["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
