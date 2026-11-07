PY ?= python3
CALIB ?= runs/demo/calib/stereo_calib.npz

.PHONY: help test demo calib budget clean

help:
	@echo "make test   运行 80 个 PC/macOS 测试"
	@echo "make demo   合成场景端到端 demo + 压缩量化"
	@echo "make calib  生成合成标定文件"
	@echo "make budget 640x640@30fps 链路预算"

test:
	$(PY) -m unittest discover -s tests -t . -v

demo:
	$(PY) -m tools.run_demo --out runs/demo --frames 8 --compression

calib:
	$(PY) -m pc.calib.cli synthetic --out $(CALIB) --views 15

budget:
	$(PY) -m tools.transport_budget --width 640 --height 640 --fps 30

clean:
	rm -rf runs/replay runs/live runs/compression
