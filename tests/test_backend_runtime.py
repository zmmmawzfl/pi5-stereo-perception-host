"""平台后端选择与 macOS CoreML 设备解析的纯逻辑测试。"""
from __future__ import annotations

import unittest
from unittest import mock

from pc.detect.yolo import resolve_device
from pc.runtime import select_onnx_providers
from shared.config import SgbmConfig


class OnnxProviderSelectionTest(unittest.TestCase):
    def test_cpu_explicit(self) -> None:
        self.assertEqual(
            select_onnx_providers("cpu", ["CPUExecutionProvider"]),
            ["CPUExecutionProvider"],
        )

    def test_coreml_includes_cpu_fallback(self) -> None:
        self.assertEqual(
            select_onnx_providers(
                "coreml",
                ["CoreMLExecutionProvider", "CPUExecutionProvider"],
            ),
            ["CoreMLExecutionProvider", "CPUExecutionProvider"],
        )

    def test_explicit_coreml_unavailable_raises(self) -> None:
        with self.assertRaises(RuntimeError):
            select_onnx_providers("coreml", ["CPUExecutionProvider"])

    def test_auto_darwin_prefers_coreml(self) -> None:
        with mock.patch("pc.runtime.platform.system", return_value="Darwin"):
            got = select_onnx_providers(
                "auto", ["CPUExecutionProvider", "CoreMLExecutionProvider"]
            )
        self.assertEqual(got[0], "CoreMLExecutionProvider")

    def test_auto_linux_prefers_cuda(self) -> None:
        with mock.patch("pc.runtime.platform.system", return_value="Linux"):
            got = select_onnx_providers(
                "auto", ["CPUExecutionProvider", "CUDAExecutionProvider"]
            )
        self.assertEqual(got[0], "CUDAExecutionProvider")

    def test_tensorrt_includes_cuda_fallback(self) -> None:
        self.assertEqual(
            select_onnx_providers(
                "tensorrt",
                [
                    "TensorrtExecutionProvider",
                    "CUDAExecutionProvider",
                    "CPUExecutionProvider",
                ],
            ),
            [
                "TensorrtExecutionProvider",
                "CUDAExecutionProvider",
                "CPUExecutionProvider",
            ],
        )

    def test_unknown_preference_raises(self) -> None:
        with self.assertRaises(ValueError):
            select_onnx_providers("openvino", ["CPUExecutionProvider"])


class MacDeviceResolutionTest(unittest.TestCase):
    def test_coreml_package_uses_cpu_input_device(self) -> None:
        self.assertEqual(resolve_device("auto", "models/yolov8n.mlpackage"), "cpu")

    def test_explicit_device_is_preserved_for_pt(self) -> None:
        self.assertEqual(resolve_device("mps", "models/yolov8n.pt"), "mps")

    def test_explicit_cuda_device_is_preserved(self) -> None:
        self.assertEqual(resolve_device("0", "models/yolov8n.engine"), "0")


class LearnedProviderConfigTest(unittest.TestCase):
    def test_rejects_unknown_provider(self) -> None:
        with self.assertRaises(ValueError):
            SgbmConfig(learned_provider="openvino")

    def test_accepts_macos_provider(self) -> None:
        self.assertEqual(SgbmConfig(learned_provider="coreml").learned_provider, "coreml")


if __name__ == "__main__":
    unittest.main()
