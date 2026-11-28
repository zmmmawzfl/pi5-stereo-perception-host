"""把一段标定录制整体缩放到目标分辨率（默认 640×480），并同步 capture.json。

为什么需要它：树莓派按 1640×1232 采集能保住视场，但实时档只用到 640×480。
标定必须和推理用同一个投影——把已经标好的那批录制整体缩放、重标，比重新摆一次板可靠。

用法：
    python -m tools.rescale_record --root ../raspberry-pi/data/recordings/calib_board2 \
        --out ../raspberry-pi/data/recordings/calib_board2_640 --width 640 --height 480
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import cv2

FRAME_SUFFIXES = ("left", "right")


def scale_points(points, sx: float, sy: float):
    return [[float(p[0]) * sx, float(p[1]) * sy] for p in points]


def rescale_capture(meta: dict, sx: float, sy: float, width: int, height: int) -> dict:
    """返回缩放后的 capture.json 副本（不改原对象）。"""
    out = dict(meta)
    out["image_size"] = [int(width), int(height)]
    rot = int(meta.get("rotation_deg", 0) or 0) % 360
    if rot % 180 == 90:
        out["image_size_eval"] = [int(width), int(height)]
    else:
        out["image_size_eval"] = [int(width), int(height)]
    if isinstance(meta.get("quads"), list):
        out["quads"] = [scale_points(q, sx, sy) for q in meta["quads"]]
    if isinstance(meta.get("corners"), list):
        out["corners"] = [scale_points(c, sx, sy) for c in meta["corners"]]
    size = meta.get("size_px")
    if isinstance(size, dict) and size:
        out["size_px"] = {
            "min": round(float(size.get("min", 0.0)) * sx, 1),
            "max": round(float(size.get("max", 0.0)) * sx, 1),
        }
    out["rescaled_from"] = {
        "image_size": list(meta.get("image_size", [])),
        "sx": round(sx, 6),
        "sy": round(sy, 6),
    }
    return out


def rescale_record(root: str | Path, out: str | Path, width: int, height: int) -> dict:
    root = Path(root)
    out = Path(out)
    meta_path = root / "capture.json"
    frame_dir = root / "frames" if (root / "frames").is_dir() else root
    if not meta_path.exists():
        raise FileNotFoundError(f"缺少 {meta_path}")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    src_w, src_h = meta.get("image_size") or (0, 0)
    if not src_w or not src_h:
        frames = sorted(frame_dir.glob("*_left.*"))
        if not frames:
            raise FileNotFoundError(f"{frame_dir} 里没有 *_left.* 帧")
        img = cv2.imread(str(frames[0]))
        src_h, src_w = img.shape[:2]
    sx, sy = width / float(src_w), height / float(src_h)

    out.mkdir(parents=True, exist_ok=True)
    out_frames = out / frame_dir.relative_to(root)
    out_frames.mkdir(parents=True, exist_ok=True)
    frames = sorted(p for p in frame_dir.iterdir() if p.suffix.lower() in (".png", ".jpg", ".jpeg"))
    if not frames:
        raise FileNotFoundError(f"{frame_dir} 里没有图像")
    for path in frames:
        img = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        if img is None:
            raise RuntimeError(f"读不了 {path}")
        if img.ndim == 2:
            resized = cv2.resize(img, (width, height), interpolation=cv2.INTER_AREA)
        else:
            resized = cv2.resize(img, (width, height), interpolation=cv2.INTER_AREA)
        cv2.imwrite(str(out_frames / path.name), resized)

    (out / "capture.json").write_text(
        json.dumps(rescale_capture(meta, sx, sy, width, height), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    pairs = root / "pairs.jsonl"
    if pairs.exists():
        shutil.copy2(pairs, out / pairs.name)
    return {"frames": len(frames), "width": width, "height": height, "sx": sx, "sy": sy}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="整体缩放一段标定录制并同步 capture.json")
    ap.add_argument("--root", required=True, help="原始录制目录（含 capture.json 与 *_left/right 帧）")
    ap.add_argument("--out", required=True, help="输出目录")
    ap.add_argument("--width", type=int, default=640)
    ap.add_argument("--height", type=int, default=480)
    args = ap.parse_args(argv)
    if args.width <= 0 or args.height <= 0:
        ap.error("--width/--height 必须为正")
    info = rescale_record(args.root, args.out, args.width, args.height)
    print(f"[rescale] {info['frames']} 帧 -> {info['width']}x{info['height']}（sx={info['sx']:.6f}）")
    print(f"[rescale] 输出 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())