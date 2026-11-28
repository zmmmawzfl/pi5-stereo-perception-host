# 可移植性：为什么大多数情况不用“编译”，以及怎么一次构建到处复用

> 面向问题：“PC 端能不能适用性更广一点，不要老是编译/重建？”
> 结论先给：**视觉链路（接收 → 校正 → SGBM → 点云 → 物体 3D）是纯 Python，不需要编译**；
> 真正需要“构建”的只有 **ROS 2 容器镜像**，而且它只影响 ROS 2 发布这条路。

---

## 1. 三层东西，别混在一起

| 层 | 需要什么 | 是否需要编译/构建 | 说明 |
|---|---|---|---|
| 视觉链路 `pc.receive.live` / demo / 标定 | Python 3.10+、numpy、PyYAML、pyzmq、带 ximgproc 的 OpenCV | **不需要** | pip wheel 直接装；x86_64 / aarch64 / macOS 都有预编译轮子 |
| ROS 2 节点 (`pc/ros2/`) | ROS 2 Humble/Jazzy + rclpy + foxglove_bridge | 仅在“本机没有 ROS 2”时才要装/构建 | Ubuntu 22.04/24.04 用 apt；**macOS 用 robostack conda**（已在 Mac 跑通，见 [`ros2_macos.md`](ros2_macos.md)）；也可以用容器 |
| 容器镜像 `stereo-depth-ros2:humble` | 基础镜像 `ros:humble-ros-base` + 少量 apt 包 | 每架构一次 | 架构绑定，但可以导出/推送后复用，不必每次重建 |

关键点：**镜像里没有项目代码**。`docker-compose.yml` 把仓库挂到容器的 `/workspace`，改代码不用重建镜像，重启容器即可。

---

## 2. 首选路径：原生 pip（无编译）

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-linux-cpu.txt      # 通用 Linux，x86_64 / aarch64
# 有 NVIDIA GPU 且要跑 ONNX-GPU/TensorRT 时改用 requirements-pc.txt
python -m unittest discover -s tests -t .
python -m tools.run_demo --out runs/demo --frames 8 --compression
```

- 已实测：Pi 5 aarch64 与 Mac（Apple M2，cv2 5.1）同一套源码都能跑全量测试。**2026-11-28 复跑：PC 152 项 OK（Mac 2 skip）、Pi 81 项 OK**。旧的 x86 主机（2026-11-14 上 80 项）已移除，见 [`pc_linux_x86_setup.md`](pc_linux_x86_setup.md) 顶部的历史说明。
- 版本边界：只需要 OpenCV ≥ 4.8 且**带 contrib**（`cv2.ximgproc`，SGBM 右目匹配 + WLS）。检查：
  ```bash
  python -c "import cv2; print(cv2.__version__, hasattr(cv2,'ximgproc'))"
  ```
- 用 apt 装 `python3-opencv` 也可以（Ubuntu 22.04+ / Raspberry Pi OS 都带 contrib），只是版本随发行版走。

> 只有树莓派端**必须**用 apt 版 OpenCV：Picamera2 与系统 libcamera 绑定，不能换成 pip 轮子。

---

## 2.5 只跑实时链路：纯 CPU 容器（任意 Docker 主机）

不需要 ROS 2、不需要 ultralytics，镜像里只有 numpy / PyYAML / pyzmq / opencv-contrib-headless：

```bash
make docker-cpu                     # 构建 stereo-depth-cpu:latest
docker run --rm --network host \
    -v "$PWD/data:/data:ro" -v "$PWD/runs:/out" \
    stereo-depth-cpu:latest \
    --endpoint tcp://10.42.0.1:5555 --calib /data/calib/stereo_calib.npz --out /out/live
```

- Linux 用 `--network host`；Docker Desktop（macOS / Windows）没有 host 网络，但 ZMQ 是
  **容器主动连 Pi**，所以直接 `--endpoint tcp://<Pi 的 IP>:5555` 就行，不用端口映射；
- 想一次产多架构：`docker buildx build --platform linux/amd64,linux/arm64 -f docker/Dockerfile.cpu .`；
- 镜像里没有 `models/yolov8n.pt`：检测先用 `--detect-backend color`；要跑 YOLO 就把
  `models/` 挂到 `/app/models` 并换 `requirements-linux-cpu.txt` 重建；
- 这是“跑通链路”的最低门槛；ROS 2 话题、TF 仍然走 `docker/Dockerfile.ros2`。
- **国内网络拉不动 docker.io 时**（`failed to fetch anonymous token` / `connect: connection refused`）：
  ```bash
  docker pull docker.m.daocloud.io/library/python:3.12-slim
  docker tag  docker.m.daocloud.io/library/python:3.12-slim python:3.12-slim   # 或 --build-arg BASE_IMAGE=...
  make docker-cpu PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
  ```
  `BASE_IMAGE` 与 `PIP_INDEX_URL` 都是 Dockerfile 的 build-arg，不改文件就能换源。
- **实测**（2026-11-15，Ubuntu 26.04 x86_64 / Pentium G4560 / Docker 29）：镜像 620 MB 级，
  容器内 `import cv2` = 4.14.0（Dockerfile 已把 opencv 钉在 `>=4.8,<5`，5.0 未验证），
  对 Pi 的 jpeg 流跑 40 对：**3.66 fps**、单对耗时 median 259 ms、CPU 164%、峰值 RSS 111 MB ——
  与裸机跑（3.37 fps / 359 ms 旧数据）同量级，容器几乎不带来额外开销。

---
## 3. 需要 ROS 2 时：三条路，按机器选

1. **Ubuntu 22.04 / 24.04 原生（最省事，零容器）**
   ```bash
   sudo apt install ros-humble-ros-base ros-humble-foxglove-bridge ros-humble-rmw-cyclonedds-cpp
   ```
   然后直接用系统 Python 跑 `python -m pc.ros2.pointcloud_node`，不需要 Docker。
2. **macOS / 其它发行版：容器**（本仓库现有方案）
   ```bash
   docker compose up -d ros2
   docker compose exec ros2 bash
   ```
3. **离线/无外网：导入导出的 tar**（见第 4 节）

---

## 4. 别重复构建：镜像一次构建、到处复用

镜像与架构绑定：**amd64 镜像不能在 Apple Silicon / 树莓派上跑**，反之亦然。

### 4.1 同架构搬运（最快）

```bash
# 源机器（已构建过的 x86_64）
docker save stereo-depth-ros2:humble | gzip -1 > stereo-depth-ros2-humble-amd64.tar.gz
# 目标机器
docker load < stereo-depth-ros2-humble-amd64.tar.gz
```

已导出的样例：`artifacts/pc-env-2026-11-14/stereo-depth-ros2-humble-amd64.tar.gz`（478 MB，sha256 `78ca0e35…c783`，在工作区内，不进 git）。

### 4.2 多架构一次构建（有 registry 时最省事）

在任意 x86_64 机器上装一次 buildx + qemu，之后一条命令产出 amd64+arm64：

```bash
docker run --privileged --rm tonistiigi/binfmt --install arm64
docker buildx create --name multi --use
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -f docker/Dockerfile.ros2 \
  -t <registry>/stereo-depth-ros2:humble --push .
```

之后所有目标机只需要 `docker pull`，**永远不用再构建**。

### 4.3 尽量减少“需要重建”的触发

- 基础镜像固定 tag（`ros:humble-ros-base`），只在 `Dockerfile.ros2` 改动时才 `docker compose build`；
- `.dockerignore` 已排除 `models/ data/ runs/ docs/ tests/`，构建上下文很小（模型 105 MB、数据 11 MB 不进上下文）；
- 换代码不用重建：compose 挂载 `.:/workspace`。

---

## 5. 踩过的坑（重复构建多半是这些）

| 现象 | 原因 | 处理 |
|---|---|---|
| 拉基础镜像超时 | Docker Hub 被墙 | 用镜像站（如 `docker.m.daocloud.io/ros:humble-ros-base`）拉下来再 `docker tag` |
| 经代理拉镜像层卡死（几百 MB 的层几十分钟不动） | 镜像站域名被塞进代理 | 把镜像站域名加入 daemon 的 `NO_PROXY`，然后重启 docker |
| `failed to resolve source metadata … EOF` | BuildKit 走代理解析 | `DOCKER_BUILDKIT=0 docker build --network=host …` |
| `exec format error` | 架构不匹配 | 换对应架构镜像，或用 buildx 多架构构建 |

---

## 6. 验证清单（任何新机器）

```bash
python -c "import cv2; print(cv2.__version__, hasattr(cv2,'ximgproc'))"   # OpenCV 带 contrib
python -m unittest discover -s tests -t .                                  # 152 项（Mac 2 skip）
python -m tools.run_demo --out runs/demo --frames 8 --compression           # 端到端
docker compose run --rm --no-deps ros2 ros2 pkg list | wc -l                # 容器（可选）≈189
```
