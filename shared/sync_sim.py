"""离线同步仿真：只用于 PC 端 demo 报告，不依赖 Pi 仓库的 `pi` 包。

真机同步环在 Pi 仓库 `pi/sync/p_controller.py`；这里保留同一套前馈式 P 环公式，
用于在无相机时展示“52.8 ms 相位差为什么可以压到亚毫秒”，不参与任何实机控制。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np

from shared.stats import summarize


@dataclass
class PhaseLockState:
    period_us: float
    last_error_us: Optional[float] = None
    steps: int = 0


class PhaseLockController:
    def __init__(
        self,
        kp: float = 0.05,
        target_offset_ns: int = 0,
        min_frame_us: int = 30_000,
        max_frame_us: int = 40_000,
        max_step_us: int = 300,
        base_period_us: Optional[float] = None,
    ) -> None:
        if not (0.0 < kp < 1.0):
            raise ValueError("kp 必须在 (0,1)，推荐从 0.05 起")
        if min_frame_us >= max_frame_us:
            raise ValueError("min_frame_us 必须小于 max_frame_us")
        self.kp = float(kp)
        self.target_offset_ns = int(target_offset_ns)
        self.min_frame_us = int(min_frame_us)
        self.max_frame_us = int(max_frame_us)
        self.max_step_us = int(max_step_us)
        self.base_period_us = float(
            base_period_us if base_period_us is not None else (min_frame_us + max_frame_us) / 2
        )
        self.state = PhaseLockState(period_us=self.base_period_us)

    def update(self, ts_master_ns: int, ts_slave_ns: int) -> int:
        error_us = ((int(ts_slave_ns) - int(ts_master_ns)) - self.target_offset_ns) / 1000.0
        step = float(np.clip(-self.kp * error_us, -self.max_step_us, self.max_step_us))
        period = float(
            np.clip(self.base_period_us + step, self.min_frame_us, self.max_frame_us)
        )
        self.state.period_us = period
        self.state.last_error_us = error_us
        self.state.steps += 1
        return int(round(period))

    @property
    def period_us(self) -> float:
        return self.state.period_us


@dataclass
class SyncSimulationResult:
    before_offset_ms: List[float]
    after_offset_ms: List[float]
    periods_us: List[float]
    stats: Dict[str, object]


def simulate_sync(
    n_frames: int = 300,
    kp: float = 0.05,
    master_period_us: float = 33333.0,
    slave_period_us: float = 33600.0,
    jitter_us: float = 0.10,
    initial_offset_ms: float = 6.0,
    control_from: int = 50,
    settle_frac: float = 0.25,
    seed: int = 0,
) -> SyncSimulationResult:
    rng = np.random.default_rng(seed)
    ctl = PhaseLockController(
        kp=kp,
        min_frame_us=30_000,
        max_frame_us=40_000,
        max_step_us=300,
        base_period_us=master_period_us,
    )
    ctl.state.period_us = float(slave_period_us)
    t_master = 1_000_000_000.0
    t_slave = t_master + initial_offset_ms * 1e6
    offsets: List[float] = []
    periods: List[float] = []
    before: List[float] = []
    after: List[float] = []
    for i in range(n_frames):
        t_master += master_period_us * 1000.0 + rng.normal(0, jitter_us * 1000.0)
        t_slave += ctl.period_us * 1000.0 + rng.normal(0, jitter_us * 1000.0)
        diff_ms = (t_slave - t_master) / 1e6
        offsets.append(diff_ms)
        periods.append(ctl.period_us)
        if i < control_from:
            before.append(diff_ms)
        elif i >= max(control_from, int(n_frames * (1.0 - settle_frac))):
            after.append(diff_ms)
        if i >= control_from:
            ctl.update(int(t_master), int(t_slave))
    before_stats = summarize(before)
    after_stats = summarize(after)
    return SyncSimulationResult(
        before_offset_ms=before,
        after_offset_ms=after,
        periods_us=periods,
        stats={
            "before": before_stats,
            "after": after_stats,
            "before_iqr_ms": before_stats["p75"] - before_stats["p25"] if before_stats["n"] else None,
            "after_iqr_ms": after_stats["p75"] - after_stats["p25"] if after_stats["n"] else None,
            "final_period_us": ctl.period_us,
            "kp": kp,
        },
    )
