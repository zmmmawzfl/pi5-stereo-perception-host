"""运行后端选择：macOS 原生（CoreML）与 Linux（CUDA/CPU）共用一套逻辑。

目标不是把 macOS 数字伪装成 RTX 数字，而是让上层代码明确“现在用的是哪个
ONNX Runtime provider”，并在 provider 不可用时**立即报错**，避免静默退回 CPU
却对外宣称用了 GPU。

- macOS 原生：``CoreMLExecutionProvider``（FFS ONNX、CoreML EP）
- Linux/NVIDIA：``CUDAExecutionProvider``（或显式 ``TensorrtExecutionProvider``）
- Docker：只有 CPU，``CPUExecutionProvider``；容器内不要尝试 CUDA/TensorRT
"""
from __future__ import annotations

import argparse
import json
import platform
from typing import Iterable, List, Optional

PROVIDER_ALIASES = {
    "coreml": "CoreMLExecutionProvider",
    "cuda": "CUDAExecutionProvider",
    "tensorrt": "TensorrtExecutionProvider",
    "trt": "TensorrtExecutionProvider",
    "cpu": "CPUExecutionProvider",
}


def platform_tag() -> str:
    return f"{platform.system()}-{platform.machine()}"


def available_onnx_providers() -> List[str]:
    """返回当前 onnxruntime 构建可用的 provider；没装 onnxruntime 时返回空列表。"""
    try:
        import onnxruntime as ort
    except ImportError:
        return []
    try:
        return list(ort.get_available_providers())
    except Exception:  # pragma: no cover - 损坏的 onnxruntime
        return []


def select_onnx_providers(
    prefer: str = "auto", available: Optional[Iterable[str]] = None
) -> List[str]:
    """按平台/显式偏好选择 provider，并保留 CPU 作为最后回退。

    ``prefer``：
      - ``auto``：Darwin 先 CoreML，其他平台先 CUDA；都没有则 CPU；
      - ``coreml`` / ``cuda`` / ``tensorrt`` / ``cpu``：显式要求。

    显式请求的首选 provider 不可用时抛 ``RuntimeError``，不静默降级。
    ``available`` 可注入，便于测试和无 onnxruntime 环境下做纯逻辑校验。
    """
    avail = list(available) if available is not None else available_onnx_providers()
    avail = [str(p) for p in avail]
    tag = (prefer or "auto").strip().lower()

    if tag == "auto":
        if platform.system() == "Darwin":
            candidates = ["CoreMLExecutionProvider", "CPUExecutionProvider"]
        else:
            candidates = ["CUDAExecutionProvider", "CPUExecutionProvider"]
        selected = [p for p in candidates if p in avail]
        if not selected:
            raise RuntimeError(
                f"没有可用的 onnxruntime provider（平台 {platform_tag()}，"
                f"available={avail or 'none'}）；先安装 onnxruntime"
            )
        return selected

    primary = PROVIDER_ALIASES.get(tag)
    if primary is None:
        raise ValueError(
            f"未知 ONNX provider 偏好: {prefer!r}；可选 auto/coreml/cuda/tensorrt/cpu"
        )
    if primary not in avail:
        raise RuntimeError(
            f"请求 {primary} 但当前 onnxruntime 不可用；"
            f"平台 {platform_tag()}，available={avail or 'none'}。"
            "macOS 用 onnxruntime 的 CoreML EP；Linux 用 onnxruntime-gpu；"
            "Docker 里只能用 cpu。"
        )
    candidates = [primary]
    if primary == "TensorrtExecutionProvider" and "CUDAExecutionProvider" in avail:
        candidates.append("CUDAExecutionProvider")
    if primary != "CPUExecutionProvider" and "CPUExecutionProvider" in avail:
        candidates.append("CPUExecutionProvider")
    return [p for p in candidates if p in avail]


def describe() -> dict:
    return {
        "platform": platform_tag(),
        "python": platform.python_version(),
        "available_onnx_providers": available_onnx_providers(),
        "auto_selection": _safe_auto(),
    }


def _safe_auto() -> Optional[List[str]]:
    try:
        return select_onnx_providers("auto")
    except Exception as exc:  # noqa: BLE001 - 诊断命令要能报告而不是崩溃
        return [f"<unavailable: {exc}>"]


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="显示当前平台与 ONNX Runtime provider 选择")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args(argv)
    info = describe()
    if args.json:
        print(json.dumps(info, indent=2, ensure_ascii=False))
    else:
        print(f"platform: {info['platform']}")
        print(f"python:   {info['python']}")
        print(f"onnx providers: {info['available_onnx_providers'] or 'none'}")
        print(f"auto selection: {info['auto_selection']}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
