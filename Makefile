PY ?= python3
CALIB ?= runs/demo/calib/stereo_calib.npz

.PHONY: help test deps demo calib budget clean

help:
	@echo "make deps   按平台选 requirements 并安装（Linux CPU / NVIDIA / macOS）"
	@echo "make test   运行 80 个 PC/macOS 测试"
	@echo "make demo   合成场景端到端 demo + 压缩量化"
	@echo "make calib  生成合成标定文件"
	@echo "make budget 640x640@30fps 链路预算"

deps:
	@case "$$(uname -s)" in \
	  Darwin) req=requirements-macos.txt ;; \
	  Linux)  if command -v nvidia-smi >/dev/null 2>&1; then req=requirements-pc.txt; else req=requirements-linux-cpu.txt; fi ;; \
	  *)      echo "unsupported platform"; exit 1 ;; \
	esac; \
	echo "[deps] pip install -r $$req"; \
	$(PY) -m pip install -r $$req; \
	$(PY) -c "import cv2; assert hasattr(cv2,'ximgproc'), 'need opencv-contrib'; print('[deps] cv2', cv2.__version__)" || \
	  echo "[deps] 警告: OpenCV 缺 ximgproc，请覆盖安装 opencv-contrib-python（见 docs/portability.md）"

test:
	$(PY) -m unittest discover -s tests -t . -v
