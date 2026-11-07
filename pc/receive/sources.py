"""统一的帧对来源：在线 ZMQ 或录制目录回放。

`live.py` 与 `ros2/pointcloud_node.py` 之前各自实现了一遍“ZMQ / 回放 →
(pair_id, skew, left, right)”，细节逐渐分叉。这里收敛成一个 `PairFeed`：
消费者只关心 ``PairFrame``，不需要知道数据来自网络还是磁盘。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Optional

import numpy as np

from pc.receive.subscriber import ZmqPairSource
from shared.messages import PairingStats
from shared.recording import PairReplay


@dataclass(frozen=True)
class PairFrame:
    """一对待处理图像 + 最小元数据。图像由调用方拥有，不做拷贝。"""

    pair_id: int
    skew_ns: int
    left: np.ndarray
    right: np.ndarray


class PairFeed:
    """把 ZMQ 订阅或录制回放包装成同一个可迭代对象。

    - ``replay_root`` 非空时只读磁盘，不建立 ZMQ 连接；
    - ``max_pairs`` 同时约束两种来源；
    - ZMQ 结束后可通过 ``stats`` 取得 PairAssembler 的配对计数。
    """

    def __init__(
        self,
        endpoint: str = "tcp://127.0.0.1:5555",
        replay_root: Optional[str] = None,
        max_pairs: Optional[int] = None,
        idle_timeout_s: float = 15.0,
        stale_s: float = 1.0,
    ) -> None:
        self.endpoint = endpoint
        self.replay_root = replay_root
        self.max_pairs = max_pairs
        self.idle_timeout_s = float(idle_timeout_s)
        self.stale_s = float(stale_s)
        self._stats: Optional[PairingStats] = None

    @property
    def stats(self) -> Optional[PairingStats]:
        return self._stats

    def __iter__(self) -> Iterator[PairFrame]:
        if self.replay_root:
            yield from self._iter_replay()
        else:
            yield from self._iter_zmq()

    def _iter_replay(self) -> Iterator[PairFrame]:
        for i, (rec, left, right) in enumerate(PairReplay(self.replay_root)):
            yield PairFrame(pair_id=rec.pair_id, skew_ns=rec.ts_diff_ns, left=left, right=right)
            if self.max_pairs and i + 1 >= self.max_pairs:
                break

    def _iter_zmq(self) -> Iterator[PairFrame]:
        src = ZmqPairSource(self.endpoint, stale_s=self.stale_s)
        try:
            for rp in src.pairs(max_pairs=self.max_pairs, idle_timeout_s=self.idle_timeout_s):
                yield PairFrame(
                    pair_id=rp.pair_id,
                    skew_ns=rp.ts_diff_ns,
                    left=rp.left,
                    right=rp.right,
                )
        finally:
            self._stats = src.stats
