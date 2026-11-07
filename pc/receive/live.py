"""PC 端在线处理：订阅 Pi 推流（或回放录制目录）→ 实时双目 3D 感知输出。

本模块产生的是实时场景感知结果：深度图、点云、对象级 3D 中心与导航消费
所需的输出，不追求完整 3D 建模或可编辑 CAD 产物。

    python -m pc.receive.live --endpoint tcp://192.168.1.10:5555 \
        --calib data/calib/stereo_calib.npz --out runs/live

    python -m pc.receive.live --replay-root data/recordings/day1 \
        --calib data/calib/stereo_calib.npz --out runs/replay
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Optional

import cv2

from pc.calib.calibrate import CalibrationResult
from pc.depth.pointcloud import cloud_from_result, write_ply
from pc.pipeline import build_pipeline
from pc.receive.sources import PairFeed
from pc.validate.resources import ResourceMonitor
from pc.validate.visualize import draw_objects, source_map
from shared.config import load_config
from shared.stats import summarize


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="PC 端在线/回放实时双目 3D 感知")
    ap.add_argument("--endpoint", default="tcp://127.0.0.1:5555")
    ap.add_argument("--replay-root", default=None)
    ap.add_argument("--calib", required=True)
    ap.add_argument("--config", default=None)
    ap.add_argument("--out", default="runs/live")
    ap.add_argument("--max-pairs", type=int, default=None)
    ap.add_argument("--idle-timeout", type=float, default=15.0)
    ap.add_argument("--save-every", type=int, default=30, help="每 N 对存一次可视化，0 关闭")
    ap.add_argument("--save-cloud-every", type=int, default=0,
                    help="每 N 对存一次体素降采样点云 PLY，0 关闭")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    calib = CalibrationResult.load(args.calib)
    pipeline = build_pipeline(cfg, calib)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "objects.jsonl").write_text("", encoding="utf-8")

    feed = PairFeed(
        endpoint=args.endpoint,
        replay_root=args.replay_root,
        max_pairs=args.max_pairs,
        idle_timeout_s=args.idle_timeout,
    )
    frames = 0
    timings = []
    monitor = ResourceMonitor()
    t_start = time.time()
    try:
        for item in feed:
            result = pipeline.process(item.left, item.right)
            cloud_points = None
            if args.save_cloud_every and (frames + 1) % args.save_cloud_every == 0:
                pts, cols = cloud_from_result(
                    result, calib,
                    stride=cfg.pointcloud.stride,
                    max_depth_m=cfg.pointcloud.max_depth_m,
                    voxel_size_m=cfg.pointcloud.voxel_size_m,
                )
                write_ply(out / f"cloud_{frames + 1:05d}.ply", pts, cols)
                cloud_points = len(pts)
            record = {
                "pair_id": item.pair_id,
                "skew_ms": item.skew_ns / 1e6,
                "wall_ns": time.time_ns(),
                "objects": [o.to_dict() for o in result.objects],
                "timings_ms": {k: round(v, 3) for k, v in result.timings_ms.items()},
                "coverage": float(result.fused.coverage),
                "cloud_points": cloud_points,
            }
            with open(out / "objects.jsonl", "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            timings.append(result.timings_ms["total"])
            frames += 1
            if args.save_every and frames % args.save_every == 0:
                cv2.imwrite(str(out / f"overlay_{frames:05d}.png"),
                            draw_objects(result.rectified_left, result.objects, calib))
                cv2.imwrite(str(out / f"source_{frames:05d}.png"), source_map(result.fused))
            print(
                f"[live] pair={item.pair_id} objects={len(result.objects)} "
                f"total={result.timings_ms['total']:.0f}ms",
                flush=True,
            )
    except KeyboardInterrupt:
        print("\n[live] 中断", flush=True)
    finally:
        if feed.stats is not None:
            print(json.dumps({"pairing": feed.stats.as_dict()}, ensure_ascii=False))

    elapsed = max(time.time() - t_start, 1e-6)
    summary = {
        "pairs": frames,
        "elapsed_s": elapsed,
        "fps": frames / elapsed,
        "timings_ms": summarize(timings),
        "calib": calib.summary(),
        "detector": pipeline.detector.name,
        "learned": pipeline.learned.name if pipeline.learned is not None else None,
        "pointcloud": {
            "voxel_size_m": cfg.pointcloud.voxel_size_m,
            "stride": cfg.pointcloud.stride,
            "max_depth_m": cfg.pointcloud.max_depth_m,
        },
        "resources": monitor.snapshot().as_dict(),
    }
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if frames else 2


if __name__ == "__main__":
    sys.exit(main())
