# macOS 原生 + Docker 混合运行（PC 端）

> 状态：macOS 原生基础链路已在 Apple M2（8 GB、macOS 27.2、Python 3.14.6、OpenCV 5.0.0-contrib）
> 验证：80 tests（2 skip）、demo 20.27 fps、3 对回放 9.56 fps；`CoreMLExecutionProvider` 可见。
> **Docker 尚未安装/构建；YOLO、FFS 模型推理未跑。** 下面的 Docker 命令仍是待执行清单。

## 0. 一句话

**延迟敏感环节在 macOS 原生跑，ROS 2 运行期在 Docker（纯 CPU）里跑；Docker 里不要碰
TensorRT/CUDA，也不要把容器里的 CPU 数字当成 Mac CoreML 或 RTX 3060 的数字。**

| 环节 | 运行位置 | 后端 | 目的 |
|---|---|---|---|
| SGBM、校正、物体聚合、点云/体素 | macOS 原生 | OpenCV CPU | 真实延迟/可视化/报告 |
| YOLO 检测 | macOS 原生 | CoreML（`.mlpackage`） | 检测延迟与精度 |
| Fast-FoundationStereo | macOS 原生 | ONNX Runtime CoreML EP | 学习型补全实验 |
| ROS 2 节点、TF、Foxglove bridge | Docker（Humble） | CPU | 话题/字段/QoS/可视化联调 |
| RTAB-Map / Nav2 | Docker（Humble，可选安装） | CPU | 接口验证，不追帧率 |
| TensorRT / CUDA | **不要**在 Docker 里 | — | 只在 Linux + NVIDIA 上 |

## 1. 数据流

```text
Pi 5 ──ZMQ 5555/5556──► macOS 原生
                          ├─ 校正 → SGBM → 融合 → 检测/聚合 → 点云/深度/JSON
                          └─ 指标、PLY、overlay、报告

Docker (ROS 2 Humble, CPU)
  └─ 同一份 ZMQ/回放 → 校正 → SGBM → 点云 → PointCloud2/Image/CameraInfo/JSON
       └─ Foxglove bridge :8765 → Foxglove Studio
```

**当前限制（重要）：** 还没有实现“macOS 原生算完 → ZMQ 结果 → Docker ROS 2 转发”的桥。
所以现在 Docker 里的 ROS 2 节点会自己跑一遍 CPU 管线：

- 用来验证**接口/字段/QoS/可视化**是够的；
- 用来做 macOS 的**性能演示**不够（容器是 CPU，且 Docker Desktop 有虚拟化开销）；
- 若要在 ROS 2 里看 native 的真实帧率结果，下一步应加一个“结果桥”（点云/深度/对象
  的 ZMQ 发布 + 容器内只做 ROS 消息转换），或直接在 macOS 原生装 ROS 2。

## 2. macOS 原生

### 2.1 安装

```bash
cd ~/Desktop/stereo-depth        # 本 PC 仓库根目录
python3 -m venv .venv-macos
source .venv-macos/bin/activate
pip install -r requirements-macos.txt
```

`requirements-macos.txt` 用官方 `onnxruntime`（macOS wheel 带 CoreML EP），**不要**装
`onnxruntime-gpu`；`coremltools` 只在导出 YOLO CoreML 时需要。

先确认 provider：

```bash
python -m pc.runtime
# 期望看到 CoreMLExecutionProvider（Apple Silicon）
# auto selection 应为 ["CoreMLExecutionProvider", "CPUExecutionProvider"]
```

如果只有 `CPUExecutionProvider`，说明当前 Python 架构或 onnxruntime 轮子没有 CoreML EP；
先不要声称用了 CoreML，换 arm64 Python/官方 wheel 再查。

### 2.2 无相机基线

```bash
python -m pc.receive.live \
    --config configs/pc_macos.yaml \
    --replay-root data/recordings/synth6 \
    --calib runs/demo/calib/stereo_calib.npz \
    --out runs/macos_replay --save-every 1 --save-cloud-every 1
```

`configs/pc_macos.yaml` 默认 `detect.model_path=models/yolov8n.mlpackage`；文件不存在时
`detect.backend=auto` 会退到彩色连通域占位，这是预期行为，报告里会写 `detector=color`。

### 2.3 YOLO CoreML

```bash
# 需 macOS + coremltools
yolo export model=models/yolov8n.pt format=coreml nms=True
# 产物通常是 models/yolov8n.mlpackage
```

`configs/pc_macos.yaml` 已指向该路径，`device: auto` 对 `.mlpackage` 解析为 `cpu`——这里的
`cpu` 只是 ultralytics 输入侧设备，模型计算由 CoreML/ANE/GPU 执行，不是“CPU 推理”。

MLX 还没有接进 `pc/detect`；当前可执行路径是 CoreML。不要用 MLX 的名义报数字，除非真的
跑过并记录了后端。

### 2.4 Fast-FoundationStereo（FFS）CoreML EP

先做**正确性**，再做性能：

```bash
# 1) CPU 参考：确认输入/归一化/输出语义与适配器一致
python -m pc.runtime --json
# 2) 再把 configs/pc_macos.yaml 的 learned_backend 改成 onnx、
#    learned_provider 改成 coreml，跑一小段回放
```

适配器在 `pc/stereo/learned.py`，provider 选择在 `pc/runtime.py`。注意：

- FFS 模型卡写“无预处理、16-bit 视差”，而当前适配器按 ImageNet mean/std + float 像素
  视差实现；**这两者没有对齐之前，CoreML 跑出来的图不能当作精度结论**；
- CoreML EP 对动态 shape/部分算子支持有限，`InferenceSession` 可能直接失败；失败信息会
  带 provider 列表，先用 `learned_provider=cpu` 做对照，再回 CoreML；
- CoreML 首次加载/编译有 warm-up，不要把第一帧当稳态延迟；
- 记录 `latency_ms`、`providers`（已写进 `DisparityResult.meta`）和实际分辨率。

### 2.5 性能口径

在 macOS 上至少记录：

- SGBM/校正/聚合的每阶段耗时（`pc.receive.live` 的 summary）；
- YOLO CoreML 的 warm-up 后 P50/P90；
- FFS CoreML EP 的 warm-up 后 P50/P90、峰值内存；
- 进程 `resources.cpu_percent/rss_mb`；
- 分辨率、`scale`、`num_disparities`、体素大小。

这些数字只代表这台 Mac。**不能**写成 RTX 3060/TensorRT 的指标；目标 PC 极限帧率仍要在
NVIDIA 机器上复测。

## 3. Docker（ROS 2 Humble，纯 CPU）

### 3.1 构建与进入

```bash
cd ~/Desktop/stereo-depth
docker compose build
docker compose up -d
docker compose exec ros2 bash
```

容器内自检：

```bash
source /opt/ros/humble/setup.bash
python3 -c "import cv2, zmq, yaml, rclpy; print(cv2.__version__)"
ros2 --help
```

`docker-compose.yml` 使用端口映射而不是 `network_mode: host`（Docker Desktop for Mac
不支持 host 网络）；`host.docker.internal` 指向 macOS 宿主机。

### 3.2 回放验证（不需要 Pi）

```bash
docker compose exec ros2 bash -lc '
  source /opt/ros/humble/setup.bash &&
  python3 -m pc.ros2.pointcloud_node \
    --calib runs/demo/calib/stereo_calib.npz \
    --replay-root data/recordings/synth6 \
    --max-pairs 10 --no-images'
```

另开一个 exec 看话题：

```bash
docker compose exec ros2 bash -lc '
  source /opt/ros/humble/setup.bash && ros2 topic list'
```

期望看到 `/stereo/points`、`/stereo/depth`、`/stereo/left/camera_info`、`/stereo/objects`
（`--no-images` 时没有 image_rect；不加该参数就有）。

### 3.3 在线（Pi → 容器）

```bash
# Pi 的 IP 直接可达时
docker compose exec ros2 bash -lc '
  source /opt/ros/humble/setup.bash &&
  python3 -m pc.ros2.pointcloud_node \
    --calib data/calib/stereo_calib.npz \
    --endpoint tcp://<PI_IP>:5555'
```

如果 Pi 先把流发到 **macOS 宿主机**上的接收端，容器用
`tcp://host.docker.internal:5555`；容器里的 `127.0.0.1` 是容器自己，不是 Mac。

### 3.4 Foxglove

```bash
docker compose exec ros2 bash -lc '
  source /opt/ros/humble/setup.bash &&
  ros2 run foxglove_bridge foxglove_bridge'
# Foxglove Studio 打开 ws://localhost:8765
```

3D 面板：PointCloud `/stereo/points`、Color=RGB、关 normals、decay 0；图像面板限 5–10 Hz。
完整话题/面板设置见 [`docs/ros2_foxglove.md`](ros2_foxglove.md)。

### 3.5 RTAB-Map / Nav2

基础镜像只装了 `foxglove_bridge` 和 CycloneDDS，**没有**装 `rtabmap_ros`/Nav2，避免镜像
过大。需要时在 `docker/Dockerfile.ros2` 里追加：

```dockerfile
ros-humble-rtabmap-ros \
ros-humble-nav2-bringup \
```

然后重新 `docker compose build`。RTAB-Map/Nav2 在 macOS Docker 里只做**接口验证**，不要
用它的耗时代表机器人平台。

### 3.6 Docker 网络注意

- DDS 发现不保证跨 Docker Desktop NAT；把 ROS 2 节点和 Foxglove bridge 放在**同一个
  容器**里（本仓库的 compose 就是单容器），用 `docker compose exec` 开多个 shell。
- 不要把 `--network host` 当成 macOS 的解决方案；它不生效或行为不同。
- 容器内没有 GPU；`onnxruntime-gpu`、TensorRT、CUDA 都不要装。

## 4. 换到 Linux + NVIDIA

上层业务代码不变，只换后端：

| 项 | macOS 原生 | Linux + RTX |
|---|---|---|
| 依赖 | `requirements-macos.txt` | `requirements-pc.txt` |
| YOLO | `.mlpackage` + CoreML | `.engine` + TensorRT |
| `detect.device` | `auto`（CoreML 包为 cpu 输入侧） | `0` |
| FFS | `learned_provider: coreml` | `learned_provider: cuda` 或 `tensorrt` |
| ROS 2 | Docker CPU | 可 Docker，也可原生；Linux 可用 host 网络 |

迁移后必须重新记录延迟/显存，不能沿用 macOS 数字。

## 5. 验证清单（按顺序）

- [x] `python -m pc.runtime` 看到 CoreMLExecutionProvider（Apple M2，2026-11-01）；
- [x] 无相机回放能出点云/物体，`detector` 字段与实际后端一致；
- [ ] YOLO `.mlpackage` 推理成功，记录 warm-up 后延迟；
- [ ] FFS ONNX 先 CPU、再 CoreML，输入/输出语义对齐，记录 `providers`；
- [ ] `docker compose build` 成功，容器内 `rclpy/cv2/zmq` 可导入；
- [ ] Docker 回放能 `ros2 topic list` 看到 5 个话题；
- [ ] Foxglove `ws://localhost:8765` 能看 RGB 点云与 5–10 Hz 图像；
- [ ] RTAB-Map/Nav2 仅在需要时安装并验证接口；
- [ ] 所有性能数字标注平台/后端，不把 Mac 数字写成 RTX 数字。

## 6. 当前状态

- `pc/runtime.py`、`learned_provider`、YOLO `.mlpackage`/`device=auto`、`configs/pc_macos.yaml`、
  `requirements-macos.txt`、`docker/`、`docker-compose.yml` 已加入；
- macOS 原生基础链路已在 Apple M2 上验证（见上），但 **Docker、YOLO、FFS 仍未跑**；
- 没有 macOS CoreML 模型推理或 Docker 的性能数字；
- 没有实现 native→Docker 的 ROS 2 结果桥，Docker 节点当前自己跑 CPU 管线。
