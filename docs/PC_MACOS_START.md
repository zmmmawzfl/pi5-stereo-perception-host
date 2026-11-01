# PC 端（macOS）执行手册：从 Pi 搬到 Mac，再跑原生 + Docker

> 这份文档是给 **macOS 上的你 / macOS DSH** 看的，目标是从零把项目跑起来，并构建 ROS 2 Docker。
>
> **2026-11-01 验证进展**：macOS 原生（Apple M2、8 GB、macOS 27.2、Python 3.14.6、
> OpenCV 5.0.0-contrib）已跑通 **80 个 PC 测试（2 skip）**、合成 demo（20.27 fps）和 3 对回放
> （9.56 fps）。**Docker 未安装/未构建；YOLO 与 FFS 的模型推理未跑。**
> 下面每一步都给了“看到什么才算成功”，不要跳步，也不要把未跑的数字当结论。
>
> 相关深文档：[macOS/Docker 细节](macos_docker.md)、[项目状态](PROJECT_STATE.md)、
> [ROS 2/Foxglove](ros2_foxglove.md)、[阶段三](stage3.md)。

---

## 给 macOS DSH 的启动提示（先读这一节）

你是运行在 macOS 上的 DSH，工作目录假定为 `~/Desktop/stereo-depth`（建议用 ASCII 路径，
避免 Docker/ROS 对中文路径的兼容问题）。你的任务不是重写算法，而是：

1. 把 Pi 上的当前工作区完整搬到 Mac；
2. 在 **macOS 原生** 跑通无相机链路（合成/回放、SGBM、物体 3D、点云）；
3. 在 Mac 原生验证 CoreML（YOLO `.mlpackage`、FFS ONNX CoreML EP）；
4. 在 **Docker（ROS 2 Humble，纯 CPU）** 构建并验证话题/Foxglove；
5. 把每一步的版本、命令、输出、错误写进 `runs/macos/REPORT.md`。

**硬约束（不要违反）：**

- 不要先改算法/默认参数；先按文档跑通基线。要改先记录原因和对照数据。
- Docker 里**不要**安装 CUDA、TensorRT、`onnxruntime-gpu`；容器没有 GPU。
- 不要把 macOS CoreML 的延迟写成 RTX 3060/TensorRT 的延迟；所有数字标注平台和后端。
- 没有实际输出时，不要写“通过/已验收”；失败就保留完整 stderr。
- 没有相机，不要尝试 Picamera2 实机路径；只用合成/回放数据。
- 不要用 MLX 的名义报数字：当前只接入了 CoreML，MLX 未接入。

**先读：** 本文件 → [`PROJECT_STATE.md`](PROJECT_STATE.md) → [`macos_docker.md`](macos_docker.md)。
**执行顺序：** 第 2 节搬运 → 第 3 节原生 → 第 4 节 Docker → 第 6 节验收 → 第 7 节回传。

可直接粘贴给 DSH 的启动句：

```text
请按 docs/PC_MACOS_START.md 执行。先确认工作目录、Python 3.10+、arm64、Docker Desktop。
不要改算法；按“搬运 → 原生无相机 → CoreML → Docker ROS2 → 验收”顺序做。
每步把命令、版本、关键输出写入 runs/macos/REPORT.md；失败保留完整错误。
Docker 内禁止 GPU/CUDA/TensorRT；性能数字必须标注 macOS 原生还是 Docker CPU。
没有真实输出不要声称通过。
```

---

## 0. 先理解这个项目

**它是什么：** 实时双目 3D 场景感知前端。

```text
Pi 5 双 IMX219 ──ZMQ──► PC
  → 校正 → SGBM（96 视差，可 ROI/降分辨率）→ 左右一致性/纹理路由
  → 深度 + 彩色点云（体素 0.03 m）→ 物体级 3D（框/中心/来源）
  → ROS 2 PointCloud2 / 深度图 / CameraInfo / 对象 JSON → Foxglove / Nav2 / RTAB-Map
```

**它不是什么：**

- 不是 3D 建模/CAD，不输出高保真纹理网格，不导出 Blender/SolidWorks；
- 不是 SLAM，自身不提供位姿；
- 不直接给 6-DoF 抓取位姿（先用 2D 框 + 点云投影做抓取初筛）；
- 学习型匹配器只是阶段三的可回退补全，不是主线依赖。

**现在真实状态：**

| 项 | 状态 |
|---|---|
| 合成/回放全链路（标定→SGBM→点云→物体） | ✅ Pi 与 macOS M2 均跑通；80 tests（Mac 2 skip） |
| 真实相机、真实标定、真实同步 | ❌ 相机未到，未测 |
| macOS 原生基础链路 | ✅ Apple M2 已验证：demo 20.27 fps、3 对回放 9.56 fps |
| macOS CoreML | ⚠️ `CoreMLExecutionProvider` 已可见；YOLO/FFS 推理未跑 |
| Docker ROS 2 | ❌ Docker 未安装，未构建 |
| 模型权重 | ⚠️ `models/` 已预下载并校验，未接入/未实测 |
| 目标 PC 帧率 | ❌ 没有；Pi 的 3.75 fps 不是 Mac/RTX 数字 |

合成 demo 的已验证参考值（只代表合成场景 + Pi 5）：

- B=83.025 mm、f=531.125 px、RMS=0.047 px；
- 物体深度误差中位 1.2 mm；真值几何区覆盖率 65.4%；
- SGBM 150 ms/总 266 ms/3.75 fps（Pi 5，不是 Mac，也不是 RTX 3060）。

---

## 1. 推荐的运行分工

| 环节 | 在哪里跑 | 用什么 |
|---|---|---|
| 校正、SGBM、物体聚合、点云/体素 | macOS 原生 | OpenCV CPU |
| YOLO | macOS 原生 | CoreML `.mlpackage` |
| Fast-FoundationStereo | macOS 原生 | ONNX Runtime CoreML EP |
| ROS 2 节点、TF、Foxglove bridge | Docker（Humble） | CPU |
| RTAB-Map / Nav2 | Docker（Humble，可选装） | CPU |
| TensorRT / CUDA | **不要**在 Docker 里 | Linux + NVIDIA 才用 |

**性能口径：** macOS CoreML 数字 ≠ RTX 3060 TensorRT 数字；Docker 是 CPU + 虚拟化，
只用来验证接口，不用来报帧率。

**当前已知缺口：** native 算完的结果还没有桥接进 Docker 的 ROS 2。Docker 里的节点会
自己跑一遍 CPU 管线，所以它适合验证话题/字段/QoS/Foxglove，不适合代表 native 性能。
如果你要在 ROS 2 里看 native 的真实结果，下一步是加“结果 ZMQ → ROS 2”桥，或让 Mac
原生也装 ROS 2。

---

## 2. 把项目从 Pi 搬到 Mac

当前 PC 仓库：Linux 本地 `/home/chenai/桌面/深度/macos-pc`，Mac 上推荐
`~/Desktop/stereo-depth`。**只搬 PC 仓库**；Pi 采集端是独立仓库，不要再把 `pi/` 搬进 Mac。

### 2.1 先决定用哪种方式

| 方式 | 适合 | 注意 |
|---|---|---|
| **tar + scp（推荐）** | 想带上当前未提交的改动 | 需要手动排除 `.venv`/`models`，再单独传模型 |
| git bundle / GitHub | 只传已提交历史 | 当前 macOS/Docker 改动**未提交**，直接 bundle 会丢 |
| rsync | 两台机器都可 SSH，反复同步 | 加 `--exclude`，不要在 Mac 上同步 Linux 的 `.venv` |
| U 盘/网盘 | 网络不方便 | 同样排除 `.venv`、`__pycache__` |

**建议项目放在 Mac 的 ASCII 路径**，例如 `~/Desktop/stereo-depth`，避免 Docker/ROS 对中文
路径的兼容问题。

### 2.2 方式 A：tar + scp（推荐，包含当前工作区）

在 **Pi** 上执行：

```bash
cd /home/chenai/桌面/深度

# 先看一眼：当前有未提交改动，tar 会把它们一起带走
git status --short

tar \
  --exclude='.venv' \
  --exclude='.git' \
  --exclude='models' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='.pytest_cache' \
  -czf /tmp/stereo-depth-mac.tar.gz .

sha256sum /tmp/stereo-depth-mac.tar.gz
scp /tmp/stereo-depth-mac.tar.gz <你的Mac用户名>@<Mac的IP>:/Users/<你的Mac用户名>/Downloads/
```

模型有 105 MB，默认没进 tar；需要 YOLO/FFS 时单独传：

```bash
scp -r /home/chenai/桌面/深度/models \
    <你的Mac用户名>@<Mac的IP>:/Users/<你的Mac用户名>/Downloads/models
```

在 **Mac** 上解包：

```bash
mkdir -p ~/Desktop/stereo-depth
tar -xzf ~/Downloads/stereo-depth-mac.tar.gz -C ~/Desktop/stereo-depth
# 如果传了模型：
mv ~/Downloads/models ~/Desktop/stereo-depth/models

cd ~/Desktop/stereo-depth
ls
# 校验模型（macOS 用 shasum；Linux 用 sha256sum）
(cd models && shasum -a 256 -c SHA256SUMS)
# 应看到 pc/ pi/ shared/ configs/ docker/ docker-compose.yml docs/ tests/ ...
```

校验一下没有把 Linux 虚拟环境带进来：

```bash
test ! -d .venv && echo "OK: 没有 .venv"
du -sh .
```

### 2.3 方式 B：git bundle（只传已提交内容）

如果你愿意先提交当前改动，在 Pi 上：

```bash
cd /home/chenai/桌面/深度
git add -A
git commit -m "macOS native + Docker backend"
git bundle create /tmp/stereo-depth.bundle --all
scp /tmp/stereo-depth.bundle <你的Mac用户名>@<Mac的IP>:~/Downloads/
```

Mac 上：

```bash
cd ~/dev
git clone ~/Downloads/stereo-depth.bundle stereo-depth
cd stereo-depth
```

**注意：** 不先 commit 就直接 bundle，当前工作区的 macOS/Docker 改动不会进 bundle。
如果你不想提交，用方式 A。

### 2.4 方式 C：rsync

在 Mac 上执行（从 Mac 拉，或从 Pi 推都行）：

```bash
rsync -av --progress \
  --exclude='.venv' --exclude='.git' --exclude='models' \
  --exclude='__pycache__' --exclude='*.pyc' --exclude='.pytest_cache' \
  <Pi用户名>@<Pi的IP>:/home/chenai/桌面/深度/ \
  ~/Desktop/stereo-depth/
```

再按需单独 `rsync` 模型目录。不要在 Mac 上复用 Pi 的 `.venv`：里面的二进制是
Linux/aarch64 的，Mac 上必须重建。

### 2.5 到 Mac 后先检查文件

```bash
cd ~/Desktop/stereo-depth
find . -maxdepth 2 -type f | sort | head -50
python3 --version          # 建议 3.12–3.14；本机 3.14.6 已验证
uname -m                   # Apple Silicon 应为 arm64
```

如果 `data/recordings/synth6` 和 `runs/demo/calib/stereo_calib.npz` 在，后面的无相机
回放可以直接跑；不在就先跑 `tools.run_demo` 重新生成。

---

## 3. macOS 原生：先跑无相机链路

### 3.1 安装依赖

```bash
cd ~/Desktop/stereo-depth

# 先确认 Python >=3.10；macOS 自带的 python3 可能还是 3.9，不要直接用它
python3 --version
# 推荐 Homebrew Python 3.12（Apple Silicon 路径是 /opt/homebrew）
# 没有 Homebrew 先按 https://brew.sh 安装
brew install python@3.12
/opt/homebrew/bin/python3.12 -m venv .venv-macos
source .venv-macos/bin/activate
python -m pip install --upgrade pip
pip install -r requirements-macos.txt

# ultralytics 会依赖 opencv-python；必须在最后强制装回 contrib，否则会丢 ximgproc，
# SGBM 右目视差/左右一致性测试会失败（本机实测过这个坑）。
pip install --force-reinstall --no-deps opencv-contrib-python
```

如果 `coremltools` 与 numpy 版本冲突：

```bash
# 先装运行必需，coremltools 只在导出 YOLO CoreML 时需要
pip install opencv-contrib-python ultralytics onnxruntime pyzmq PyYAML numpy
pip install coremltools
```

`requirements-macos.txt` 用官方 `onnxruntime`（macOS wheel 带 CoreML EP），
**不要装 `onnxruntime-gpu`**。

### 3.2 确认 CoreML provider

```bash
python -m pc.runtime
```

期望类似：

```text
platform: Darwin-arm64
onnx providers: ['CoreMLExecutionProvider', 'CPUExecutionProvider']
auto selection: ['CoreMLExecutionProvider', 'CPUExecutionProvider']
```

如果只有 `CPUExecutionProvider`：

- 确认是 **arm64** Python：`python -c "import platform; print(platform.machine())"`；
- 确认装的是官方 `onnxruntime`，不是 `onnxruntime-gpu`；
- 换官方 arm64 wheel 后重试。**不要在没有 CoreML 的情况下声称用了 CoreML。**

### 3.3 跑测试和合成 demo

```bash
# 80 个测试；macOS 上个别依赖 ffmpeg 的用例可能 skip
python -m unittest discover -s tests -t .

# 端到端合成 demo（不启用学习型，不依赖模型）
python -m tools.run_demo --out runs/demo_macos --frames 8
```

看到 `runs/demo_macos/report.md` 和 `cloud.ply` 就算通过。重点看：

- 标定 RMS、极线 `|dy|`；
- 物体 `|ΔZ|` 中位数；
- 点云前/后点数、覆盖率；
- 报告里“学习型未启用/未实测”是否如实。

### 3.4 无相机回放 + 可视化

```bash
# 如果搬运时带了 runs/demo/calib，用这个：
python -m pc.receive.live \
  --config configs/pc_macos.yaml \
  --replay-root data/recordings/synth6 \
  --calib runs/demo/calib/stereo_calib.npz \
  --out runs/macos_replay --save-every 1 --save-cloud-every 1

# 如果刚跑的是 runs/demo_macos，则把 --calib 换成：
#   runs/demo_macos/calib/stereo_calib.npz
```

看 `runs/macos_replay/objects.jsonl`、`overlay_00001.png`、`cloud_00001.ply`。
如果 `detect` 显示 `color`，说明 `.mlpackage` 还没导出，属于预期；先跑通链路。

### 3.5 YOLO CoreML

```bash
# 需要 macOS + coremltools
yolo export model=models/yolov8n.pt format=coreml nms=True
# 产物可能落在当前目录或 models/ 下；先找出来
find . -maxdepth 2 -name '*.mlpackage' -print
# 若不在 models/ 下：
# mv yolov8n.mlpackage models/
```

`configs/pc_macos.yaml` 已指向 `models/yolov8n.mlpackage`，`device: auto` 会把它解析为
`cpu` 输入侧；模型计算由 CoreML/ANE/GPU 执行，不是“CPU 推理”。

MLX **没有接入** `pc/detect`；当前可执行路径是 CoreML。不要用 MLX 的名义报数字。

### 3.6 Fast-FoundationStereo（先正确性，再 CoreML）

```bash
# 确认模型在
ls -lh models/c_fast_foundationstereo_bp2_dynbatch_dynhw.onnx

# 建议先把 configs/pc_macos.yaml 改成：
#   learned_backend: onnx
#   learned_provider: cpu
# 跑一小段，确认输入/归一化/输出语义正确；再改回 coreml
```

现有适配器按 ImageNet mean/std + float 像素视差实现，而 FFS 模型卡写“无预处理、
16-bit 视差”；**两者没对齐之前，CoreML 的输出不能当精度结论**。CoreML EP 对动态
shape 也可能直接失败，失败时先用 `learned_provider=cpu` 对照。

---

## 4. Docker：构建 ROS 2 Humble（纯 CPU）

### 4.1 安装 Docker Desktop

装 Docker Desktop for Mac（Apple Silicon 版本），启动后确认：

```bash
docker --version
docker compose version
```

在 Docker Desktop 设置里给足内存（建议 ≥6 GB；ROS + Foxglove 会吃内存）。

### 4.2 构建

```bash
cd ~/Desktop/stereo-depth
docker compose build
```

首次构建会拉 `ros:humble-ros-base` 并安装 OpenCV、pyzmq、PyYAML、Foxglove bridge、
CycloneDDS；**没有 GPU/CUDA/TensorRT**。

如果构建失败，先看错误是不是包名/架构问题：

```bash
docker compose config
docker buildx ls
uname -m
```

Apple Silicon 应使用 arm64 镜像；如果某个 ROS 包没有 arm64 版本，可以临时用
`--platform linux/amd64` 构建（会慢很多），或把该包从 Dockerfile 去掉后重试。

### 4.3 启动并自检

```bash
docker compose up -d
docker compose exec ros2 bash

# 容器内
source /opt/ros/humble/setup.bash
python3 -c "import cv2, zmq, yaml, rclpy; print(cv2.__version__)"
ros2 --help
python3 -m unittest discover -s tests -t .   # 可选，验证 CPU 管线
```

`docker compose up -d` 后容器执行 `sleep infinity`，不会自己跑节点；用 `exec` 开 shell。

### 4.4 回放验证 ROS 2 话题

在容器内：

```bash
source /opt/ros/humble/setup.bash
python3 -m pc.ros2.pointcloud_node \
  --calib runs/demo/calib/stereo_calib.npz \
  --replay-root data/recordings/synth6 \
  --max-pairs 10 --no-images
```

另开一个终端：

```bash
docker compose exec ros2 bash -lc \
  'source /opt/ros/humble/setup.bash && ros2 topic list'
```

期望看到：

```text
/stereo/objects
/stereo/points
/stereo/depth
/stereo/left/camera_info
```

不加 `--no-images` 时还会有 `/stereo/left/image_rect`。`ros2 topic echo /stereo/objects`
能看到 JSON（2D 框、3D 中心、`depth_source`）。

### 4.5 Foxglove

```bash
docker compose exec ros2 bash -lc \
  'source /opt/ros/humble/setup.bash && ros2 run foxglove_bridge foxglove_bridge'
```

Foxglove Studio 打开：

```text
ws://localhost:8765
```

3D 面板：

- PointCloud topic `/stereo/points`，Color mode = RGB；
- 关闭 normals；
- Decay 0（或 0.5 s 以内）；
- Fixed frame `camera_left`（或补 TF 后的 `world`）。

图像面板限 5–10 Hz，不要 30 Hz 全速刷图。详细设置见
[ros2_foxglove.md](ros2_foxglove.md)。

### 4.6 在线（Pi → Docker）

如果 Pi 和 Mac 在同一网段，容器可以直接连 Pi：

```bash
docker compose exec ros2 bash -lc '
  source /opt/ros/humble/setup.bash &&
  python3 -m pc.ros2.pointcloud_node \
    --calib data/calib/stereo_calib.npz \
    --endpoint tcp://<PI_IP>:5555'
```

如果 Pi 先发到 **macOS 宿主机**上的接收端，容器内用：

```text
tcp://host.docker.internal:5555
```

容器里的 `127.0.0.1` 是容器自己，不是 Mac。

### 4.7 停止

```bash
docker compose down
```

---

## 5. 常见问题

| 现象 | 先查 |
|---|---|
| `docker compose build` 拉不到镜像 | Docker Desktop 是否启动、网络/代理、`docker login` 不需要；先 `docker pull ros:humble-ros-base` |
| 构建时某个 `ros-humble-*` 包找不到 | 换 arm64 基础镜像/去掉可选包/临时 `--platform linux/amd64`；不要硬装 CUDA |
| 容器内 `import rclpy` 失败 | 先 `source /opt/ros/humble/setup.bash`；确认在 `ros2` 服务里 exec，不是 Mac 原生 shell |
| `ros2 topic list` 为空 | 节点和 bridge 是否在同一容器；`ROS_DOMAIN_ID` 是否一致；DDS 不跨 Docker Desktop NAT |
| Foxglove 连不上 8765 | `docker compose ps` 端口映射；bridge 是否真的在容器内启动；Mac 防火墙 |
| 容器连不上 Pi | 用 Pi 的 LAN IP，不要 `127.0.0.1`；`ping <PI_IP>`；TCP 5555 放行 |
| `python -m pc.runtime` 没有 CoreML | 是否 arm64 Python/官方 onnxruntime；不要装 `onnxruntime-gpu` |
| YOLO 仍显示 `color` | `.mlpackage` 是否导出到 `models/`；`detect.backend` 是否 `auto`；看启动日志 |
| FFS CoreML 直接报错 | 先 `learned_provider=cpu` 对齐语义；CoreML 对动态 shape 支持有限 |
| `yolo export ... format=coreml` 报 `BlobWriter not loaded` | 本机在 macOS 27 + Python 3.14 + coremltools 9.0 遇到；先换稳定版 coremltools/Python 3.12，或先用 `.pt` + MPS 跑通检测，不要声称已有 CoreML |
| Docker 里帧率很低 | 预期：容器是 CPU + 虚拟化；性能数字在 native 测，不要拿容器数字报帧率 |

---

## 6. 建议的验收顺序

1. **原生无相机**：`python -m pc.runtime` 看到 CoreML；`tools.run_demo` 出报告；
2. **原生回放**：`pc.receive.live` 出 objects/overlay/PLY；
3. **YOLO CoreML**：`.mlpackage` 导出成功，`detector` 不再是 color；
4. **Docker 构建**：`docker compose build` 成功，容器内 `rclpy/cv2/zmq` 可导入；
5. **Docker 回放**：`ros2 topic list` 看到 5 个话题，`/stereo/objects` 有 JSON；
6. **Foxglove**：`ws://localhost:8765` 能看到 RGB 点云；
7. **在线**：Pi → native 或 Pi → Docker（同一网段），记录端到端延迟/CPU/内存；
8. **可选**：RTAB-Map/Nav2 只在需要时装，且只做接口验证。

每一步都记录：

- 平台（macOS arm64 / Docker CPU）、后端（CoreML/CPU/…）；
- 分辨率、`num_disparities`、`scale`、体素大小；
- P50/P90 延迟、`resources.cpu_percent/rss_mb`；
- 失败时的完整错误文本（不要只说“跑不起来”）。

---

## 7. 回传给 Pi 侧的最小信息

如果 Mac 上跑出问题，把这几样发回来即可定位：

```bash
# 1) 平台/后端
python -m pc.runtime --json

# 2) 原生 demo 的摘要（不要发整份点云）
cat runs/demo_macos/summary.json | head -80

# 3) Docker 构建/运行日志（末尾 100 行）
docker compose build 2>&1 | tail -100
docker compose logs --tail=100 ros2
docker compose exec ros2 bash -lc 'source /opt/ros/humble/setup.bash && ros2 topic list'
```

不要发 `.venv`、模型权重、完整 `runs/` 点云；这些要么可重建，要么太大。

---

## 8. 文件索引

| 内容 | 位置 |
|---|---|
| 项目交接/状态 | [`PROJECT_STATE.md`](PROJECT_STATE.md) |
| macOS/Docker 设计与边界 | [`macos_docker.md`](macos_docker.md) |
| macOS 配置 | [`../configs/pc_macos.yaml`](../configs/pc_macos.yaml) |
| macOS 依赖 | [`../requirements-macos.txt`](../requirements-macos.txt) |
| Dockerfile | [`../docker/Dockerfile.ros2`](../docker/Dockerfile.ros2) |
| Compose | [`../docker-compose.yml`](../docker-compose.yml) |
| ROS 2/Foxglove | [`ros2_foxglove.md`](ros2_foxglove.md) |
| 阶段三/学习型 | [`stage3.md`](stage3.md) |
| Pi↔PC 联调 | [`runbook.md`](runbook.md) |
