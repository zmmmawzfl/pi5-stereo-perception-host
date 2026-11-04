# stereo-depth — macOS / PC 端

**实时双目 3D 场景感知（PC 端仓库）。**

本仓库只包含 PC/macOS 端：接收 Pi 的双目帧、校正、SGBM、路由、检测/聚合、点云、ROS 2。
树莓派采集端是**另一个仓库**：[pi5-stereo-perception-edge](https://github.com/zmmmawzfl/pi5-stereo-perception-edge)
两端通过 ZMQ + `SDP1` 协议通信，详见 [docs/interfaces.md](docs/interfaces.md)。

> **当前状态（诚实版）**
> - 本仓库 **80 个单元测试**通过（Mac 上 80 tests OK）。
> - macOS 原生基础链路已在 **Apple M2 / 8 GB / macOS 27.2 / Python 3.14.6 / OpenCV 5.0.0-contrib**
>   验证：合成 demo **20.27 fps**，3 对回放 **9.56 fps**；这些是 Mac 数字，不是 RTX 3060。
> - Docker（ROS 2 Humble，纯 CPU）脚本已写，**Docker 未安装/未构建**。
> - YOLO CoreML 导出在本机因 `coremltools` 报 `BlobWriter not loaded` 未完成；FFS 适配器语义未对齐。
> - Pi 端两块 IMX219 已能枚举/出图/双实例采集（2026-11-04），未控制时间戳差约 52.8 ms，
>   P 环可压到约 0.56 ms；但**尚未刚性固定**，计划 **2026-11-05 做支架 + cam1 调焦**，
>   固定前不做标定；真实尺度/运动同步仍未验收。

## 项目定位 / 非目标

- 目标：实时双目 3D 场景重建、深度估计、彩色点云、对象级 3D 中心、ROS 2 感知输出。
- 用途：机器人避障、抓取初筛、场景理解、Foxglove 可视化。
- 非目标：完整 3D 建模、可编辑 CAD、高保真纹理网格、Blender/SolidWorks 导出、SLAM、6-DoF 抓取。
- SGBM 是度量锚点；学习型匹配器只是阶段三的可回退补全，必须过“误差下降/覆盖率/帧率”三项。

## 快速开始（无需相机）

```bash
cd macos-pc        # 或 Mac 上的 ~/Desktop/stereo-depth

# macOS 原生
python3 -m venv .venv-macos
source .venv-macos/bin/activate
pip install -r requirements-macos.txt
# ultralytics 会拉 opencv-python；必须再覆盖 contrib，否则 SGBM 右目/ximgproc 缺失
pip install --force-reinstall --no-deps opencv-contrib-python

python -m pc.runtime        # 应看到 CoreMLExecutionProvider
python -m unittest discover -s tests -t .
python -m tools.run_demo --out runs/demo --frames 8

# 无相机回放
python -m pc.receive.live --config configs/pc_macos.yaml \
  --replay-root data/recordings/synth6 \
  --calib runs/demo/calib/stereo_calib.npz --out runs/macos_replay
```

Linux/NVIDIA 用 `requirements-pc.txt`，不要用 macOS 的 CoreML 配置；目标 PC 帧率只能在
NVIDIA + TensorRT 上确认。

## Docker（ROS 2 Humble，纯 CPU）

```bash
docker compose build
docker compose up -d
docker compose exec ros2 bash -lc '
  source /opt/ros/humble/setup.bash &&
  python3 -m pc.ros2.pointcloud_node \
    --calib runs/demo/calib/stereo_calib.npz \
    --replay-root data/recordings/synth6 --max-pairs 10'
```

容器内**没有 GPU**，不要装 CUDA/TensorRT/onnxruntime-gpu；它只验证话题/字段/QoS/Foxglove。
当前还没有 native → Docker 的 ROS 2 结果桥，容器节点会自己跑 CPU 管线。

## 目录（本仓库）

```text
macos-pc/
├── pc/                 # 接收、标定、校正、SGBM、融合、检测、聚合、点云、ROS2、验证
├── shared/             # SDP1 消息、配置、统计、合成场景、录制格式
├── tools/              # run_demo / compression_report / motion_jitter
├── configs/            # default / pc / pc_macos
├── tests/              # 80 个 unittest（Pi-only 测试不在这里）
├── docker/             # ROS 2 Humble CPU 容器
├── docker-compose.yml
├── ros2/               # RTAB-Map / Nav2 参数与 launch
├── data/recordings/    # 回放数据（默认 gitignore）
├── models/             # 预下载权重（默认 gitignore）
├── runs/               # demo/回放产物（默认 gitignore）
└── docs/               # 接口、阶段三、验收、macOS/Docker、交接
```

## 数据契约

- Pi→PC：ZMQ `5555` 左右 JPEG + `SDP1` header；按 `pair_id` 重组，绝不靠到达顺序。
- 标定：`K/D/R/T/P1/P2/Q`，`B=‖T‖`、`f=P1(0,0)`，不写死 83 mm。
- 输出：`objects.jsonl`（2D 框、3D 中心、`depth_source`）、`.ply`、ROS 2 `PointCloud2`/深度/CameraInfo/对象 JSON。
- 详细契约：[docs/interfaces.md](docs/interfaces.md)

## 文档

| 内容 | 位置 |
|---|---|
| 项目状态/决策/踩坑 | [docs/PROJECT_STATE.md](docs/PROJECT_STATE.md) |
| Mac 首次搬运与执行 | [docs/PC_MACOS_START.md](docs/PC_MACOS_START.md) |
| macOS/Docker 设计 | [docs/macos_docker.md](docs/macos_docker.md) |
| ROS 2 / Foxglove / RTAB-Map / Nav2 | [docs/ros2_foxglove.md](docs/ros2_foxglove.md) |
| 阶段三学习型对照 | [docs/stage3.md](docs/stage3.md) |
| 验收映射 | [docs/acceptance.md](docs/acceptance.md) |
| Pi↔PC 联调 | [docs/runbook.md](docs/runbook.md) |
