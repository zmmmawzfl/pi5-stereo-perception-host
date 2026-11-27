PY ?= python3
CALIB ?= runs/demo/calib/stereo_calib.npz
CALIB_ROOT ?= data/recordings/calib_board
BASE_IMAGE ?= python:3.12-slim
PIP_INDEX_URL ?=

.PHONY: help test deps demo calib check-calib check-docs budget checker clean docker-cpu docker-ros2

help:
	@echo "make deps   按平台选 requirements 并安装（Linux CPU / NVIDIA / macOS）"
	@echo "make test   运行全部 PC 测试（当前 111 个）"
	@echo "make demo   合成场景端到端 demo + 压缩量化"
	@echo "make calib  生成合成标定文件"
	@echo "make check-calib 体检标定采集（CALIB_ROOT=... 指目录）"
	@echo "make check-docs 体检文档：md 里引用的文件/python -m 模块/make 目标是否存在"
	@echo "make budget 640x640@30fps 链路预算"
	@echo "make checker 生成 A3 棋盘格 PDF 到 artifacts/calibration"
	@echo "make docker-cpu  构建纯 CPU 容器（任意 Docker 主机，跨架构一次构建）"
	@echo "make docker-ros2 构建 ROS 2 Humble 容器（x86 上已验证）"

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

demo:
	$(PY) -m tools.run_demo --out runs/demo --frames 8 --compression

calib:
	$(PY) -m pc.calib.cli synthetic --out $(CALIB) --views 15

check-calib:
	$(PY) -m tools.check_calib --root $(CALIB_ROOT) \
	  --out runs/calib_check.json --montage runs/calib_coverage.png

budget:
	$(PY) -m tools.transport_budget --width 640 --height 640 --fps 30

check-docs:
	$(PY) -m tools.check_docs

checker:
	$(PY) -m tools.make_checkerboard --pattern 9x6 --square-mm 30 --page a3 --out ../artifacts/calibration/board_9x6_30mm_a3

docker-cpu:
	docker build -f docker/Dockerfile.cpu -t stereo-depth-cpu:latest \
	  --build-arg BASE_IMAGE=$(BASE_IMAGE) --build-arg PIP_INDEX_URL=$(PIP_INDEX_URL) .
	@echo "运行示例：docker run --rm --network host -v \"\$$PWD/data:/data:ro\" -v \"\$$PWD/runs:/out\" stereo-depth-cpu:latest"

docker-ros2:
	DOCKER_BUILDKIT=0 docker build --network=host -f docker/Dockerfile.ros2 -t stereo-depth-ros2:humble .

clean:
	rm -rf runs/replay runs/live runs/compression
