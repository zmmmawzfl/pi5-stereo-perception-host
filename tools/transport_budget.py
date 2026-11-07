"""链路带宽预算：640x480 / 640x640 下 raw 与压缩视频的以太网占用。

不做采集，只做算术；用于回答“要不要把 JPEG 换成未压缩流”。
默认按 30 fps、1 GbE（1000 Mbps）计算，并给出每对帧的线速序列化时间。

    python -m tools.transport_budget --width 640 --height 640 --fps 30
    python -m tools.transport_budget --jpeg-kb 160 --h264-kb 22

注意：
  * raw_bgr8 是管线真正收到的 BGR 三通道，不是 sensor Bayer；
  * raw_gray8 只够 SGBM/深度，检测会失去颜色；
  * bayer10 是 sensor 原生 10-bit 的近似（1.25 B/px），需要 PC 端 debayer；
  * JPEG/H.264 的每对字节数需用实测值传入，不能拿这里的默认值当验收。
"""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass
from typing import Iterable, List


@dataclass(frozen=True)
class Estimate:
    encoding: str
    pair_bytes: int
    mbps: float
    link_util_pct: float
    serial_ms: float
    verdict: str


def _verdict(util_pct: float) -> str:
    if util_pct < 50.0:
        return "宽裕"
    if util_pct < 70.0:
        return "可用，留余量"
    if util_pct < 85.0:
        return "偏紧"
    return "不建议"


def estimate(
    width: int,
    height: int,
    fps: float,
    encoding: str,
    *,
    jpeg_kb: float = 150.0,
    h264_kb: float = 22.0,
    link_mbps: float = 1000.0,
) -> Estimate:
    """估算一帧对（左右两目）的字节数与链路占用。"""
    if width <= 0 or height <= 0 or fps <= 0:
        raise ValueError("width/height/fps 必须为正")
    enc = encoding.lower()
    px = width * height
    if enc == "raw_bgr8":
        pair_bytes = 2 * px * 3
    elif enc == "raw_gray8":
        pair_bytes = 2 * px
    elif enc == "bayer10":
        pair_bytes = 2 * math.ceil(px * 10 / 8)
    elif enc == "jpeg":
        pair_bytes = int(round(jpeg_kb * 1024))
    elif enc == "h264":
        pair_bytes = int(round(h264_kb * 1024))
    else:
        raise ValueError(f"未知编码 {encoding!r}")

    mbps = pair_bytes * 8.0 * fps / 1e6
    util = mbps / link_mbps * 100.0
    # bits / (Mbps * 1e6) = 秒；乘 1000 得毫秒
    serial_ms = pair_bytes * 8.0 / (link_mbps * 1000.0)
    return Estimate(
        encoding=enc,
        pair_bytes=pair_bytes,
        mbps=round(mbps, 1),
        link_util_pct=round(util, 1),
        serial_ms=round(serial_ms, 2),
        verdict=_verdict(util),
    )


def _rows(args: argparse.Namespace) -> List[Estimate]:
    encodings: Iterable[str] = [e.strip() for e in args.encodings.split(",") if e.strip()]
    return [
        estimate(
            args.width, args.height, args.fps, enc,
            jpeg_kb=args.jpeg_kb, h264_kb=args.h264_kb, link_mbps=args.link_mbps,
        )
        for enc in encodings
    ]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="双目视频链路带宽预算")
    ap.add_argument("--width", type=int, default=640)
    ap.add_argument("--height", type=int, default=640)
    ap.add_argument("--fps", type=float, default=30.0)
    ap.add_argument("--link-mbps", type=float, default=1000.0)
    ap.add_argument("--jpeg-kb", type=float, default=150.0, help="每对 JPEG 实测 KB（默认 640x640 q90 估值）")
    ap.add_argument("--h264-kb", type=float, default=22.0, help="每对 H.264 实测 KB（默认 2.5Mbps 估值）")
    ap.add_argument("--encodings", default="raw_bgr8,raw_gray8,bayer10,jpeg,h264")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    rows = _rows(args)
    if args.json:
        print(json.dumps([asdict(r) for r in rows], ensure_ascii=False, indent=2))
        return 0

    print(f"{args.width}x{args.height} @ {args.fps:g} fps, link={args.link_mbps:g} Mbps")
    print(f"{'encoding':<12}{'pair KB':>10}{'Mbps':>9}{'link %':>9}{'serial ms':>11}  verdict")
    for r in rows:
        print(
            f"{r.encoding:<12}{r.pair_bytes/1024:>10.1f}{r.mbps:>9.1f}"
            f"{r.link_util_pct:>9.1f}{r.serial_ms:>11.2f}  {r.verdict}"
        )
    print("\n提示：1GbE 实际可用约 110–118 MB/s；TCP/ZMQ 还有头部拷贝与突发排队。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
