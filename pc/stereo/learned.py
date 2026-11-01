"""学习型匹配器的延迟加载适配器。

阶段三要用 Fast-FoundationStereo 先实测；HITNet 作为超延迟预算时的备选。
模型与 GPU 都不在当前机器上，所以这里：
  * 定义与 SGBM 完全一致的接口；
  * 在缺模型/缺运行时时**明确报错**，绝不悄悄退回 SGBM 冒充补全；
  * ONNX 路径支持 CoreML EP（macOS 原生）与 CUDA EP（Linux/NVIDIA）；
  * TensorRT 只在 Linux/NVIDIA 上启用，Docker 容器内没有 GPU。
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional, Tuple

import cv2
import numpy as np

from pc.runtime import select_onnx_providers
from shared.config import SgbmConfig

from .base import DisparityResult


class LearnedMatcherUnavailable(RuntimeError):
    pass


class OnnxDisparityMatcher:
    """通用 ONNX 视差模型适配器（输出单通道视差图）。

    约定：模型输入为 1x3xHxW float32，0~255 均值方差归一（按导出脚本配置）；
    输出为 1x1xHxW 视差（像素），无效点 <=0。若模型输出分辨率不同，视差按
    宽度比缩放回原图。
    """

    name = "learned_onnx"

    def __init__(
        self,
        model_path: str,
        input_size: Optional[Tuple[int, int]] = None,
        max_disp: float = 192.0,
        input_mode: str = "batch",
        provider: str = "auto",
        mean: Tuple[float, float, float] = (0.485, 0.456, 0.406),
        std: Tuple[float, float, float] = (0.229, 0.224, 0.225),
    ) -> None:
        try:
            import onnxruntime as ort
        except ImportError as exc:  # pragma: no cover - 依赖环境
            raise LearnedMatcherUnavailable(
                "缺少 onnxruntime：macOS 用 pip install onnxruntime；Linux/NVIDIA 用 onnxruntime-gpu"
            ) from exc
        p = Path(model_path)
        if not p.exists():
            raise LearnedMatcherUnavailable(f"模型不存在: {p}")
        try:
            providers = select_onnx_providers(provider, ort.get_available_providers())
        except RuntimeError as exc:
            raise LearnedMatcherUnavailable(str(exc)) from exc
        try:
            self.session = ort.InferenceSession(str(p), providers=providers)
        except Exception as exc:  # noqa: BLE001 - 把 provider 失败变成可读错误
            raise LearnedMatcherUnavailable(
                f"无法用 providers={providers} 创建 ONNX session：{exc}；"
                "CoreML EP 对动态 shape/算子支持有限，可先用 learned_provider=cpu 做正确性对照"
            ) from exc
        self.input_name = self.session.get_inputs()[0].name
        self.input_size = input_size
        self.max_disp = float(max_disp)
        if input_mode not in ("batch", "concat"):
            raise ValueError("input_mode 必须是 batch 或 concat")
        self.input_mode = input_mode
        self.mean = np.asarray(mean, np.float32).reshape(1, 1, 3)
        self.std = np.asarray(std, np.float32).reshape(1, 1, 3)

    def compute(self, left_bgr: np.ndarray, right_bgr: np.ndarray) -> DisparityResult:
        t0 = time.perf_counter()
        h, w = left_bgr.shape[:2]
        in_h, in_w = self.input_size if self.input_size else (h, w)
        left = cv2.resize(left_bgr, (in_w, in_h), interpolation=cv2.INTER_AREA)
        right = cv2.resize(right_bgr, (in_w, in_h), interpolation=cv2.INTER_AREA)
        # BGR->RGB, 归一化, NCHW
        l = (left[:, :, ::-1].astype(np.float32) / 255.0 - self.mean) / self.std
        r = (right[:, :, ::-1].astype(np.float32) / 255.0 - self.mean) / self.std
        l = l.transpose(2, 0, 1)
        r = r.transpose(2, 0, 1)
        if self.input_mode == "concat":
            batch = np.concatenate([l, r], axis=0)[None]
        else:
            batch = np.stack([l, r], axis=0)
        out = np.asarray(self.session.run(None, {self.input_name: batch})[0])
        # 兼容 (1,1,H,W)/(2,1,H,W)/(1,H,W)/(H,W)
        while out.ndim > 2:
            if out.shape[0] == 2 and out.ndim == 4:
                out = out[0]
            else:
                out = out[0]
        pred = np.asarray(out, np.float32)
        if pred.ndim != 2:
            raise LearnedMatcherUnavailable(f"模型输出维度异常: {pred.shape}")
        pred = cv2.resize(pred, (w, h), interpolation=cv2.INTER_LINEAR)
        pred = pred * (w / float(in_w))
        valid = np.isfinite(pred) & (pred > 0) & (pred < self.max_disp)
        disp = np.where(valid, pred, 0.0).astype(np.float32)
        return DisparityResult(
            disp=disp,
            valid=valid,
            source="learned",
            latency_ms=(time.perf_counter() - t0) * 1000.0,
            meta={
                "backend": "onnx",
                "input_size": [in_w, in_h],
                "input_mode": self.input_mode,
                "providers": self.session.get_providers(),
            },
        )


class TensorRTDisparityMatcher:
    """TensorRT 引擎适配器（通过 polygraphy / tensorrt Python API 加载）。

    这里只做显存与延迟的常规调用封装；具体引擎由
    docs/stage3.md 中的导出命令生成。
    """

    name = "learned_tensorrt"

    def __init__(self, engine_path: str, input_size: Tuple[int, int], max_disp: float = 192.0) -> None:
        try:
            import tensorrt as trt  # noqa: F401
            import pycuda.driver as cuda  # noqa: F401
            import pycuda.autoinit  # noqa: F401
        except ImportError as exc:  # pragma: no cover - 依赖环境
            raise LearnedMatcherUnavailable(
                "缺少 tensorrt/pycuda：阶段三需在 PC（NVIDIA GPU、TensorRT 10.x）上运行"
            ) from exc
        p = Path(engine_path)
        if not p.exists():
            raise LearnedMatcherUnavailable(f"引擎不存在: {p}")
        raise LearnedMatcherUnavailable(
            "TensorRT 推理封装需要在目标 PC 上按 docs/stage3.md 校准后启用；"
            "当前仓库默认走 ONNX 路径，避免在无 GPU 环境伪造延迟数据。"
        )


def build_learned_matcher(cfg: SgbmConfig) -> Optional[object]:
    """按配置构建学习型匹配器；未配置则返回 None。"""
    backend = (cfg.learned_backend or "none").lower()
    if backend in ("none", ""):
        return None
    if backend == "onnx":
        size = None
        if cfg.learned_input_size:
            w_s, h_s = cfg.learned_input_size.lower().split("x")
            size = (int(w_s), int(h_s))
        return OnnxDisparityMatcher(
            cfg.learned_model,
            input_size=size,
            max_disp=float(cfg.num_disparities),
            input_mode=cfg.learned_input_mode,
            provider=cfg.learned_provider,
        )
    if backend in ("tensorrt", "trt"):
        raise LearnedMatcherUnavailable(
            "TensorRT 后端需要目标 PC 的引擎文件；请先用 --learned-backend onnx 做精度对照，"
            "再按 docs/stage3.md 导出 FP16 引擎。"
        )
    raise ValueError(f"未知 learned_backend: {cfg.learned_backend}")
