# 项目状态交接（给下一个 AI / 工程师）

> **2026-11-28 现状（先看这里）**：权威交接是 [`docs/HANDOFF.md`](../../docs/HANDOFF.md)，逐次实测是
> [`docs/SESSION_CONTEXT.md`](../../docs/SESSION_CONTEXT.md)（§0 现状、§60 最新）。本文件保留下方的架构与决策记录，
> **凡是“当时数字”都以 HANDOFF/README 为准**。当前要点：PC **152 项测试 OK（Mac 2 skip）**、Pi **81 项**；
> 两路 IMX219 已装 v4 支架（标称 80 mm）并完成真机标定（1640 RMS 0.634 px / 640 RMS 0.241 px，两份 npz）；
> 640 实时档 13–15 fps、浏览器预览可用；ROS 2 已在 Mac 上用 robostack 跑通；**x86 主机与 amd64 镜像 tar 已移除**；
> 下一步是 PETG `fpc_down` 新件装板后重标、真实检测器、运动场景。
>
> 历史基线：2026-11-04（双 IMX219 实机检查后；**不是当前提交**）。仓库根目录（本仓库）：Linux 本地
> `/home/chenai/桌面/深度/macos-pc`；Mac `~/stereo/macos-pc`。本仓库只含 **PC/macOS 端**；Pi 采集端是独立仓库
> （本地 `../raspberry-pi`）。当时提交 `e35346e`；根 `.git` 已删除，两个子目录各自建仓。
> 当时工作区：**80 个 PC 测试 / 20 个 Pi 测试**（现 152 / 81）；双 IMX219 已能采集但未刚性固定。

---

## 0. 先读这个（TL;DR）

**这是什么：**
一套面向机器人的**实时双目 3D 场景感知**原型。输入左右双目视频，输出：

1. 校正后的左右图；
2. 视差图 / 深度图（米）；
3. 彩色点云（PLY + ROS 2 `PointCloud2`，默认 0.03 m 体素降采样）；
4. 物体级 JSON（2D 框、3D 中心、深度、`depth_source`、覆盖率）；
5. 一套可复现的质量报告（标定、同步、压缩、误差、帧率）。

**它不是什么：**
不是 3D 建模/CAD 软件，不做高保真纹理网格，不导出 Blender/SolidWorks，不直接给 6-DoF 抓取位姿，也不是 SLAM（自身不提供位姿）。

**现在能做什么：**
没有相机也能用**合成场景 + 录制回放**跑完整链路：标定、SGBM、融合、物体 3D、点云、压缩量化、报告。Pi↔PC 的 ZMQ 链路已在本机回环验证。`models/` 已预下载 YOLOv8n 与 Fast-FoundationStereo ONNX，但默认路径不加载，见 3.4。
**双 IMX219 实机采集已验证**：枚举、单相机出图、Picamera2 双实例 640×480 都通过；未加控制的初始时间戳差约 52.8 ms，P 环 200 帧可到约 0.56 ms。

**现在不能做什么：**
实机**刚性固定、真实镜头标定、真实尺度/精度、运动同步、目标 PC 帧率、ROS 2/Foxglove/RTAB-Map/Nav2 运行期**，都还没有验证。相机虽然已能采集，但未固定，标定结果不可复现；`cam1` 还需要调焦。学习型匹配器（阶段三）只有预下载的 ONNX，没有任何实测；本机也没有 onnxruntime/GPU。

**下一步最重要的事：**
相机已到，**2026-11-05 先做刚性支架并给 `cam1` 调焦**；固定后再做第一天基线（时间戳差 + 运动抖动）、真实标定和已知距离验收。固定前不要采标定板。ROS 2 相关只在装了 ROS 2 的机器上验证；模型权重只在目标 PC 上验证，不要在本机拿 `.pt`/ONNX 冒充延迟数字。

---

## 1. 项目目标与边界

### 1.1 最终目标

从双相机实时视频中恢复**度量正确**的深度与 3D 结构，面向：

- 机器人导航避障（点云进代价地图/占据栅格）；
- 抓取初筛（2D 检测框 + 框内点云 + 物体 3D 中心）；
- 场景理解（物体级 3D 位置与深度来源）；
- 双目硬件/算法评估（同步、标定、压缩、SGBM、ROI/降分辨率、学习型对照）。

### 1.2 主线技术路线

```text
双目校正
  → SGBM（96 视差，可选 ROI / 半分辨率）
  → 左右一致性 + 纹理门限
  → SGBM 优先融合（学习型只补失效像素，不平均）
  → 检测
  → 物体级聚合（框内中位视差 + 地面约束）
  → 深度图 / 体素彩色点云
  → PLY / ROS 2 PointCloud2 / objects JSON
```

SGBM 是**度量锚点**。学习型匹配器只是阶段三的候选补全路径，必须同时满足“误差下降、覆盖率显著扩大、帧率达标”三项才保留，否则退回纯 SGBM。TSDF 只是可选扩展，不能成为主线依赖。

### 1.3 非目标（明确不做）

- 完整 3D 建模、可编辑 CAD、逆向工程级重建；
- 高保真纹理网格、Blender/SolidWorks 导出；
- 6-DoF 抓取位姿、手眼标定（当前只到“抓取初筛”）；
- SLAM / 视觉里程计（TSDF 需要外部给位姿）；
- 依赖学习型单目深度先验（相对尺度会污染绝对测距）。

### 1.4 目标演变（重要，避免下一个会话按旧目标改）

最初（2026-10-31 上午）的目标是“Pi 5 传感器前端 + PC 端立体匹配与物体级 3D 输出”，带完整 8 周阶段计划、学习型匹配器阶段三、TSDF 阶段四。

当日下午用户重新定位为：**“实时双目 3D 场景感知 / 机器人视觉感知”**，明确：

- 主线必须传统 SGBM + 体素降采样 + ROI/降分辨率，`numDisparities=64 或 96`；
- 不依赖学习型深度模型；
- ROS 2 `PointCloud2` + Foxglove 可视化；
- 对象级 2D 框 / 3D 中心 / 深度来源；
- 导航用占据栅格/OctoMap，不追高保真网格；
- 抓取先用 2D 分割 + 点云投影，6-DoF 后续；
- 非目标见 1.3。

**后续工作以“机器人感知”这版目标为准，不要按 CAD 建模方向改。**

---

## 2. 状态总表（2026-11-28 更新；2.1 是当时快照）

| 层 | 状态 | 证据 / 说明 |
|---|---|---|
| PC 核心管线（校正→SGBM→融合→物体→点云） | ✅ 合成 + 真机端到端 | 152 测试；真实相机 640 档 13–15 fps、单对 57 ms |
| Pi 采集/同步/推流代码 | ✅ 双 IMX219 已装 v4 支架、完成真机标定 | 1640 RMS 0.634 px / 640 0.241 px；见 `docs/calibration_capture.md` |
| Pi↔PC ZMQ 链路 | ✅ 千兆实测 | 943 Mbps；640 推流 48.7 KB/帧；1200 对 0 丢 |
| 双目标定 | ✅ 真机 20 对 + 合成回归 | 1640 0.634 px / 640 0.241 px；合成 0.047 px 仅作回归 |
| 物体级 3D + 地面约束 | ✅ 合成验证 | `\|ΔZ\|` 中位 1.2 mm；`\|ΔY\|` 近目标 7.1→0.7 mm |
| 点云 + 体素降采样 | ✅ 合成/回放验证 | 20,620 → 3,518 点（0.03 m） |
| ROS 2 发布节点 | ✅ Mac 实测 | robostack humble；约 2500 点/帧、5 Hz；见 `docs/ros2_macos.md` |
| Foxglove / RTAB-Map / Nav2 配置 | Foxglove ✅ 连通；RTAB/Nav2 ❌ 未跑 | bridge 8765 握手成功；`docs/ros2_foxglove.md` |
| YOLO 检测 | ⚠️ 权重已预下载；❌ 未跑 | `models/yolov8n.pt`；Mac 无 ultralytics，仍退彩色连通域 |
| 学习型匹配器（阶段三） | ⚠️ ONNX 已预下载；❌ 未实测 | `models/c_fast_foundationstereo_bp2_dynbatch_dynhw.onnx`；无 GPU/onnxruntime、无数字 |
| TSDF（阶段四） | ⚠️ 代码完成；❌ 未跑 | 缺 Open3D、缺位姿；不作为主线 |
| 实机真实精度/同步/CPU/延迟 | ✅ 静态精度已验证；运动/温度漂移未测 | 0.745 m +1%；640 档 13–15 fps；1640 P 环不收敛 |
| 运动边界 / 准静态速度上界 | ⚠️ 工具就绪；❌ 未测 | `tools/motion_jitter.py` |

### 2.1 2026-11-04 双 IMX219 实机检查（当时快照）

| 项 | 结果 |
|---|---|
| 枚举 | `rpicam-hello --list-cameras` 看到 2 个 `imx219`：`i2c@88000`（cam0）与 `i2c@80000`（cam1） |
| 单相机 | `rpicam-still --camera 0/1` 均 exit 0；cam0 正常，cam1 失焦（镜头未固定） |
| 双实例 | `Picamera2Backend(640x480, fps=30, buffer_count=3)` 连续采集成功 |
| 未控制时间戳差 | `median 52.7650 ms`，`P25 52.7632`，`P75 52.7657`，`IQR 0.0025`，`std 0.0043` |
| P 环 200 帧 | 最后 10 帧 `1.07 → 0.56 ms`；最后 50 帧 `median 3.622`，`min 0.558`，`max 10.793` |
| 启动收敛 | 约 150–200 帧（5–7 s @30fps）；服务前 ~131 对被判坏对丢弃，之后稳定 |
| 旋转 | 两个相机都报 `Rotation=180`；如画面方向不合适，采集/校正前统一转 180° |
| 测试 | Pi 仓库 **81** tests OK；PC 仓库 **152** tests OK（2026-11-28；当时是 20/80） |

**边界：** 当前只能证明“相机与 CSI 链路正常、能出图、能配对”；不能证明基线长度、镜头畸变、曝光同步或运动场景精度。详见 Pi 仓库 `docs/camera_check.md`。

---

## 3. 环境与硬件

### 3.1 当前机器（开发/验证环境）

| 项 | 值 |
|---|---|
| 主机 | Raspberry Pi 5，4 核，约 3.9 GiB 内存（`free -h` 显示 3.9Gi） |
| OS | Linux 6.18.50+rpt-rpi-v8，aarch64 |
| Python | 3.13.5 |
| OpenCV | 4.10.0（含 `ximgproc`、SGBM、`findChessboardCornersSB`） |
| NumPy | 2.2.4 |
| pyzmq | 27.2.0 |
| PyYAML | 6.0.2 |
| ffmpeg | 7.1.5（H.264 压缩量化） |
| pytest | 9.2.0 |
| 缺失 | ROS 2、Torch、ultralytics、onnxruntime、Open3D、SciPy |
| 模型资产 | `models/`（预下载，未加载；见 3.4） |
| 虚拟环境 | `.venv/`，`--system-site-packages`，复用系统 OpenCV |

**注意：** 本机是 Pi 5，不是目标 PC。`runs/demo` 的 3.75 fps 是 **Pi 上跑 PC 端 SGBM 的耗时**，不是 RTX 3060 的性能。

### 3.2 目标硬件（用户计划）

Pi 5 端：

- Pi 5 8GB 优先；官方 27W USB-C PD；
- 双 IMX219，刚性固定基线 **80 mm**（v4 支架实测标定 `T` 模长 80.196 mm，以标定为终值）；
- 2 根 CSI 排线，注意 cam0/cam1 方向；
- 主动散热；≥32GB microSD 或 NVMe；
- 千兆网线直连 PC 或交换机，避免 WiFi。

PC 端（2026-11-01 更新为两种形态）：

- **macOS 原生（当前开发/演示）**：Apple Silicon，Metal/CoreML；SGBM/校正/聚合在本机
  CPU 跑，YOLO 用 CoreML `.mlpackage`，FFS ONNX 用 CoreML EP；
- **Docker（ROS 2 运行期）**：Docker Desktop for Mac + `ros:humble-ros-base`，纯 CPU，
  只做话题/QoS/Foxglove/接口联调，容器内没有 GPU；
- **Linux + NVIDIA（最终性能确认）**：≥6GB 显存，RTX 3060 级别起；Ubuntu 22.04；
  YOLO TensorRT、FFS CUDA/TensorRT；macOS 数字不能代替这一档；
- 千兆网口（Pi 直连或交换机）。

标定与验证：

- 棋盘格或 Charuco，A3 硬质基板；方格尺寸实测后用 mm；
- 卷尺、纹理板、可选小转台。

### 3.3 依赖文件

- `requirements.txt`：numpy / PyYAML / pyzmq；
- `requirements-macos.txt`：OpenCV、ultralytics、`onnxruntime`（CoreML EP）、coremltools；
- `requirements-pc.txt`：Linux/NVIDIA 的 OpenCV、ultralytics、onnxruntime-gpu、open3d；
- `requirements-linux-cpu.txt`：通用 Linux（x86_64/aarch64，无 GPU）CPU-only 依赖，`opencv-contrib` 轮子自带 ximgproc，不需要编译；
- `requirements-dev.txt`：pytest；
- `docs/portability.md`：跨平台/跨架构选型与镜像复用（tar 搬运、buildx 多架构），何时需要构建、何时不需要；
- `docker/Dockerfile.ros2` + `docker-compose.yml`：ROS 2 Humble、OpenCV、pyzmq、
  Foxglove bridge、CycloneDDS；无 GPU。

### 3.4 预下载模型资产（2026-11-01）

`models/` 被 `.gitignore` 忽略，权重不进仓库；当前只是预下载，默认路径**不加载**。

| 文件 | 大小 | 来源 | 许可 | 状态 |
|---|---:|---|---|---|
| `models/yolov8n.pt` | 6,549,796 B | Ultralytics assets v8.3.0 | AGPL-3.0 | 检测权重；需目标 PC 的 ultralytics；默认配置仍指向 `.engine` |
| `models/c_fast_foundationstereo_bp2_dynbatch_dynhw.onnx` | 103,228,473 B | Hugging Face `nvidia/c-fast-foundationstereo` | NVIDIA Open Model Agreement（研究/评估） | 动态 H/W ONNX；适配器输入/归一化/输出语义未对齐，未运行 |

- 两个文件已用 `sha256sum -c models/SHA256SUMS` 校验通过；下载来源与复现命令见 `models/README.md`。
- 本机没有 ultralytics、onnxruntime、GPU，**没有模型推理数字**；不要用 `.pt`/ONNX 冒充 TensorRT 延迟。
- FFS 模型卡写的是“无预处理、输出 16-bit 视差”，但仓库适配器按 ImageNet mean/std + float 像素视差实现，二者必须在目标 PC 上核对后再启用。

### 3.5 PC 端运行形态：macOS 原生 + Docker（2026-11-01 用户确认）

| 环节 | 位置 | 后端 | 说明 |
|---|---|---|---|
| 校正、SGBM、聚合、体素点云 | macOS 原生 | OpenCV CPU | 真实延迟/可视化以这里为准 |
| YOLO | macOS 原生 | CoreML `.mlpackage` | `detect.device=auto`；`.mlpackage` 输入侧走 cpu |
| FFS | macOS 原生 | ONNX Runtime CoreML EP | `learned_provider=coreml`；失败先用 cpu 对照 |
| ROS 2 节点、TF、Foxglove | Docker Humble | CPU | 只验证接口；容器无 GPU |
| RTAB-Map / Nav2 | Docker Humble（可选装） | CPU | 接口验证，不追帧率 |
| TensorRT / CUDA | Linux + NVIDIA | CUDA/TensorRT | 最终性能数字只能在这里产生 |

- 代码入口：`pc/runtime.py`（provider 选择）、`configs/pc_macos.yaml`、
  `requirements-macos.txt`、`docker/Dockerfile.ros2`、`docker-compose.yml`；
- 详细命令与边界：`docs/macos_docker.md`；跨平台安装与镜像复用：`docs/portability.md`；`make deps` 按平台自动选依赖；
- **验证进展（2026-11-01）**：macOS 原生基础链路已在 Apple M2（8 GB、macOS 27.2、
  Python 3.14.6、OpenCV 5.0.0-contrib）跑通：80 PC tests（2 skip）、demo 20.27 fps、
  3 对回放 9.56 fps；`CoreMLExecutionProvider` 已可见。**YOLO/FFS 模型推理、Docker 构建
  仍未跑**；
- **已知缺口**：native 算完的结果还没有桥接进 Docker 的 ROS 2；当前 Docker 节点会自己跑
  一遍 CPU 管线，因此它适合接口联调，不适合代表 native 性能。

---

## 4. 架构与数据流

### 4.1 端到端

```text
Pi 5
┌─────────────────────────────────────────────────────────────┐
│ Picamera2(left)  ─┐                                        │
│ Picamera2(right) ─┴→ CapturedPair(left,right,tsL,tsR,seq)  │
│                              │                             │
│                    PhaseLockController                     │
│                    update(tsL,tsR) -> period_us            │
│                    set_slave_frame_duration(period)        │
│                              │                             │
│                    ZmqPublisher (JPEG + header)            │
└──────────────────────────────┼─────────────────────────────┘
                               │ tcp://PI:5555
PC                             ▼
┌─────────────────────────────────────────────────────────────┐
│ ZmqPairSource → PairAssembler（只按 pair_id 配对）          │
│       │                                                     │
│       ▼                                                     │
│ Rectifier（remap，去畸变 + 极线校正）                       │
│       │                                                     │
│       ▼                                                     │
│ SGBMMatcher（96 视差，可选 ROI/scale，右目视差做 LR）      │
│       │                    ┌── 学习型（可选，默认关闭）──┐  │
│       ▼                    │ 只补 SGBM 失效像素，不平均  │  │
│ FuseRouter ←───────────────┘                              │  │
│       │                                                     │
│       ├─→ 亚像素精化（SAD 抛物线）                          │
│       ├─→ 检测（YOLO 或彩色连通域占位）                     │
│       ├─→ ObjectAggregator（框内中位视差 + 地面 RANSAC）    │
│       ├─→ depth_from_fused（Z=fB/d）                        │
│       └─→ colored_pointcloud → voxel_downsample → PLY       │
│                                                             │
│ 输出：objects.json / overlay.png / depth.png / cloud.ply     │
│       ROS 2：/stereo/points、/stereo/depth、...              │
└─────────────────────────────────────────────────────────────┘
```

### 4.2 关键时序

1. Pi 每读一对帧：`tsL, tsR` 来自同一 request 的 `SensorTimestamp`；
2. `PhaseLockController.update(tsL, tsR)` 算从动相机新周期；
3. 下一帧前把周期写入从动相机 `FrameDurationLimits`；
4. `ZmqPublisher` 分别发左/右 JPEG，header 带 `pair_id/side/sensor_ts_ns`；
5. PC `PairAssembler` 只按 `pair_id` 重组，**绝不靠到达顺序**；
6. PC 管线按固定顺序处理，耗时写进 `timings_ms`；
7. 输出 JSON/PLY/ROS 话题。

---

## 5. 目录与模块职责

```text
macos-pc/
├── pc/
│   ├── receive/
│   │   ├── subscriber.py       # ZMQ SUB + PairAssembler
│   │   ├── sources.py          # PairFeed：统一 ZMQ/回放帧对来源
│   │   ├── record_pairs.py      # PC 端最小接收器（落盘）
│   │   └── live.py              # 在线/回放管线，写 JSON/overlay/PLY
│   ├── calib/
│   │   ├── calibrate.py        # 角点、单目→双目、stereoRectify、极线
│   │   └── cli.py              # calibrate/synthetic/inspect
│   ├── rectify/rectifier.py    # initUndistortRectifyMap + remap
│   ├── stereo/
│   │   ├── sgbm.py             # SGBM + ROI/scale + 右目视差 + 可选 WLS
│   │   ├── subpixel.py         # SAD 抛物线亚像素
│   │   ├── learned.py          # ONNX(CoreML/CUDA)/TensorRT 适配器
│   │   └── base.py             # DisparityResult
│   ├── runtime.py              # 平台/ONNX provider 选择（CoreML/CUDA/CPU）
│   ├── fuse/router.py          # LR + 纹理门限 + SGBM 优先路由
│   ├── detect/
│   │   ├── yolo.py             # ultralytics YOLO
│   │   ├── color.py            # HSV 彩色连通域占位
│   │   └── factory.py          # auto/yolo/color/none
│   ├── objects/aggregator.py   # 框内中位视差、地面平面、3D 中心
│   ├── depth/pointcloud.py     # Z=fB/d、彩色点云、体素、框内点云、PLY
│   ├── ros2/pointcloud_node.py # ROS 2 发布节点（独立）
│   ├── tsdf/fuse_offline.py    # 可选 TSDF 融合（需 Open3D + 位姿）
│   ├── validate/
│   │   ├── metrics.py          # 误差、覆盖率、物体匹配
│   │   ├── compression.py      # JPEG/H.264 往返量化
│   │   ├── report.py           # Markdown 报告
│   │   ├── resources.py        # 进程级 CPU/内存
│   │   └── visualize.py        # overlay/source map
│   └── pipeline.py             # 端到端编排
├── shared/
│   ├── messages.py             # SDP1 协议、PairAssembler
│   ├── config.py               # dataclass 配置 + 校验
│   ├── stats.py                # 统计与表格
│   ├── synthetic.py            # 合成场景（地面 + 3 个盒子）
│   └── recording.py            # 录制目录 / PairReplay
├── tools/
│   ├── run_demo.py             # 无相机端到端 demo + 报告
│   ├── compression_report.py   # 压缩伪影量化
│   └── motion_jitter.py        # 深度抖动 / 运动边界
├── ros2/
│   ├── config/rtabmap.yaml     # RTAB-Map（MemoryThr=300）
│   ├── config/nav2_obstacle.yaml
│   └── launch/stereo_perception.launch.py
├── configs/                    # default / pc / pc_macos
├── docker/                     # ROS 2 Humble CPU 容器
├── docker-compose.yml          # macOS Docker Desktop 服务定义
├── requirements-macos.txt      # macOS 原生依赖（CoreML EP）
├── tests/                      # 152 个 PC unittest（2026-11-28）
├── docs/                       # 接口、第一天、阶段三、验收、runbook、macOS/Docker、本文件
├── data/recordings/            # 测试录制（gitignore）
└── runs/                       # 报告与产物（gitignore）
```

---

## 6. 数据契约

### 6.1 帧消息（Pi → PC）

线格式：

```text
MAGIC(4)="SDP1" | header_len(uint32 LE) | header(JSON utf-8) | payload
```

ZMQ 优先用多段 `[header_json, payload]`。header：

```json
{"v":1,"pair_id":42,"seq":42,"side":"left","sensor_ts_ns":1000000000,
 "capture_wall_ns":1690000000000000000,"w":640,"h":480,"enc":"jpeg","bytes":12345}
```

- `pair_id` 由 Pi 端 `CapturedPair.pair_id = seq` 递增；
- `enc`：`jpeg` / `raw_bgr8` / `h264`；
- `side`：`left` / `right`；
- 解码后必须是 BGR `uint8`，形状 `(h,w,3)`。

### 6.2 配对状态机

`PairAssembler`：

| 情形 | 行为 | 计数 |
|---|---|---|
| 左右都到（任意顺序） | 输出 `StereoPair` | `emitted` |
| 同一侧重复 | 丢弃，不覆盖 | `duplicates` |
| 只有一侧超时 | 淘汰 | `orphans` |
| `pair_id` 小于已输出最大 id | 丢弃 | `out_of_order` |
| 打开 pair 超过 `max_open` | 淘汰最老 | `orphans` |

**绝不跨 `pair_id` 拼接。**

### 6.3 核心 Python 结构

```python
CapturedPair(left, right, ts_left_ns, ts_right_ns, seq)
  .pair_id == seq
  .ts_diff_ns == ts_right_ns - ts_left_ns

StereoPair(pair_id, seq, left: FramePacket, right: FramePacket)
  .ts_diff_ns == right.sensor_ts_ns - left.sensor_ts_ns

DisparityResult(disp, valid, source, latency_ms, right_disp, right_valid, meta)
FusedDisparity(disp, valid, source, stats)
  source: 0=无效, 1=SGBM, 2=学习型

FrameResult(frame_id, timings_ms, detections, aggregate, fused, depth,
            rectified_left, sgbm, learned, meta)
  .objects -> aggregate.objects
  .object_json()

ObjectEstimate(class_name, confidence, bbox_2d, center_3d,
               unconstrained_center_3d, depth_m, depth_source,
               valid_ratio, n_pixels, height_m, ground_applied,
               contact_3d, extra)
```

### 6.4 输出文件

`runs/demo/`：

- `report.md`：完整报告；
- `summary.json`：原始指标；
- `calib/stereo_calib.npz`：标定参数；
- `objects.json` / `objects_all.json`：物体级 JSON；
- `cloud.ply`：二进制小端 `x y z red green blue`；
- `overlay.png` / `depth.png` / `disparity.png` / `disparity_source.png`。

录制目录：

```text
<root>/pairs.jsonl
<root>/frames/000000_left.png
<root>/frames/000000_right.png
```

`pairs.jsonl` 每行含 `pair_id/seq/ts_left_ns/ts_right_ns/ts_diff_ms/left/right`。

### 6.5 ROS 2 话题

| 话题 | 类型 | 说明 |
|---|---|---|
| `/stereo/points` | `sensor_msgs/PointCloud2` | `x,y,z` float32 + `r,g,b` uint8，`point_step=16`，已体素降采样 |
| `/stereo/depth` | `sensor_msgs/Image` | `16UC1`，mm，0=无效 |
| `/stereo/left/image_rect` | `sensor_msgs/Image` | `bgr8` |
| `/stereo/left/camera_info` | `sensor_msgs/CameraInfo` | 校正内参：`D=0`，`K=P1[:3,:3]`，`P=P1` |
| `/stereo/objects` | `std_msgs/String` | JSON：对象、覆盖率、耗时、skew |

坐标系：`camera_left`，X 右、Y 下、Z 前，米。

---

## 7. 关键算法与设计决策

### 7.1 坐标、单位、符号

| 项 | 约定 |
|---|---|
| 相机坐标系 | X 右、Y 下、Z 前（OpenCV 右手系） |
| 左相机 | 光心原点 |
| 右相机 | 光心在 `(+B,0,0)` |
| 基线 B | `||T||` 米，从标定文件读，**不假设 83 mm** |
| 校正焦距 f | `P1[0,0]` |
| 视差 | `d = u_L - u_R > 0` |
| 深度 | `Z = f*B/d`，`d<=0` 无效 |
| 反投影 | `X=(u-cx)Z/f`，`Y=(v-cy)Z/f`，`cx=P1[0,2]`，`cy=P1[1,2]` |
| 时间戳 | 整数纳秒；`SensorTimestamp` 同一 Pi 单调时钟域 |

### 7.2 同步 P 环

`pi/sync/p_controller.py` 的公式（当前实现）：

```python
error_us = ((ts_slave_ns - ts_master_ns) - target_offset_ns) / 1000.0
step = clip(-kp * error_us, -max_step_us, max_step_us)
period = clip(base_period_us + step, min_frame_us, max_frame_us)
```

- `kp` 从 0.05 起；
- 从动相机晚到（error>0）→ 缩短周期；
- **不要改成累加周期**：早期实现 `p += kp*error` 会发散；现在每步从 `base_period_us` 重新算；
- `simulate_sync()` 只统计 `control_from` 之后、最后 `settle_frac` 的稳态窗口。

### 7.3 标定

`pc/calib/calibrate.py`：

- 默认 9×6 内角点，方格 30 mm；
- `detect_corners` 优先 `findChessboardCornersSB`，失败回退经典法；
- `calibrate_stereo(fix_k3=True, zero_tangent=True)`：
  - `cv2.CALIB_FIX_K3`：棋盘格数据对 k3 约束不足，放开会拟合出伪畸变，污染 `alpha=0` 的校正视场和 `fB`；
  - `cv2.CALIB_ZERO_TANGENT_DIST`：装配良好的模组切向畸变接近 0；
- `stereoRectify(..., flags=cv2.CALIB_ZERO_DISPARITY, alpha=0)`；
- `B=||T||`，`f=P1[0,0]`，`cx/cy=P1[0,2]/P1[1,2]`；
- 保存 `K1,D1,K2,D2,R,T,E,F,Q,R1,R2,P1,P2,image_size,rms,meta`；
- 极线检查 `epipolar_dy` 写进 `meta`。

### 7.4 SGBM

`pc/stereo/sgbm.py`：

- 默认 `num_disparities=96`（16 的倍数），`block_size=5`，`mode=sgbm_3way`；
- SGBM 原始输出是 `int16 * 16`，**必须除以 16** 再进深度公式；
- 右目参考视差来自 `cv2.ximgproc.createRightMatcher`；右目视差为负，取 `-raw/16` 后再比较；
- `valid = raw < 0 & rd > 0 & rd < num_disparities`（右目）；
- ROI：`roi_xywh` 归一化 `"x,y,w,h"`，只算关注区域，ROI 外无效；
- 降分辨率：`scale` 对 ROI 做 `INTER_AREA`，视差上采样后 **除以 `scale`** 还原到原图；`scale<1` 时强制 `min_disparity=0`；
- 可选 WLS（`use_wls`），默认关闭，Pi 上代价明显；
- `meta` 记录 `scale/roi/work_size/num_disparities`；
- **自适应 ROI 已撤销（2026-11-01）**：曾用上一帧 `valid` 包围盒生成 ROI，连续帧实测左侧有效像素逐帧被侵蚀（valid 最小 x 96→138→184，两帧少约 17%），根因是 SGBM 左侧 `num_disparities` 无效边界 + ROI 继续从有效区收缩。补丁留在 `runs/adaptive_roi_reviewed.patch`（已忽略），不要按原方式重引入；算力旋钮继续用 `scale`/静态 `roi_xywh`。

### 7.5 融合路由

`pc/fuse/router.py`：

```text
sgbm_ok = sgbm.valid
          & LR(|dL - dR(x - dL)| <= 1)
          & texture(local std 5x5) >= 6.0
learned 只填 (~sgbm_ok) & learned.valid
不做加权平均
```

- `stats["policy"] == "sgbm_priority_no_average"`，每帧可审计；
- 学习型输出只允许落在 SGBM 失效/低置信像素；
- 若学习型大面积覆盖物体主体，先查 LR 符号/纹理阈值，不是模型更好。

### 7.6 亚像素

`pc/stereo/subpixel.py`：SAD 代价抛物线拟合，在 `±0.5 px` 内精化；只在 `fused.valid` 上做。

### 7.7 物体级聚合与地面约束

`pc/objects/aggregator.py`：

1. 检测框收缩 `bbox_shrink=0.15`，避开边界混合像素；
2. 框内有效视差取中位数 `d_med`；
3. `Z=fB/d_med`，`(X,Y)` 用框中心反投影，得到 `unconstrained_center_3d`；
4. 地面：图像底部 `ground_band=0.45` 的像素反投影，RANSAC 拟合 `Y=pX+qZ+r`；
5. 平面可信条件：`|p|<0.5`、`|q|<0.5`、`r>0.02`、内点率≥0.25、RMS<0.08 m；
6. 对 `ground_classes`（默认 `object,box,cup,bottle,chair`）：
   - 底部条带（框底 15%）取接触点视差 → `z_b`，地面 `y=plane.y_at(x_b,z_b)`；
   - 顶部条带（框顶 15%）逐像素反投影 Y，取 P25 得顶高；
   - `height = ground_y - y_top`，合理性检查（0.02–2 m、不超过 0.8×深度、与框像素高度一致性）；
   - `center = (x_center, ground_y - height/2, z_hint)`，`contact_3d=(x_b, ground_y, z_b)`；
7. 有效像素 < `min_pixels` 或比例 < `min_valid_ratio` 的检测跳过，写 `diagnostics.skipped`；
8. 始终保留无约束结果，报告同时给有/无约束两组数字。

### 7.8 点云与体素

`pc/depth/pointcloud.py`：

- `depth_from_fused`：`Z=fB/d`，无效 `nan`；
- `colored_pointcloud`：默认 `stride=2`、`max_depth_m=6`，反投影后可选体素；
- `voxel_downsample`：整数体素索引 + `lexsort + reduceat`，同体素取质心/平均色；30k 点约 17 ms；
- `points_in_bbox`：取 2D 框内点云并补回主点偏移 `X += x1*Z/f`、`Y += y1*Z/f`，供抓取初筛；
- `write_ply`：二进制小端，`x y z red green blue`（BGR→RGB）。

### 7.9 检测

- `YoloDetector`：ultralytics，缺权重/包抛 `DetectorUnavailable`；
- `ColorBlobDetector`：HSV 8 色相桶 + 连通域 + NMS，仅链路占位；
- `auto`：有 `model_path` 文件才用 YOLO，否则彩色占位并打印告警；
- 显式 `backend: yolo` 但权重缺失 → 直接报错，不静默降级；
- **已预下载** `models/yolov8n.pt`（6.55 MB，Ultralytics AGPL-3.0），但默认配置仍指向 `models/yolov8n.engine`，本机没有 ultralytics，所以现有 demo 不受影响；
- macOS：`yolo export model=models/yolov8n.pt format=coreml nms=True`，`detect.model_path` 指向 `.mlpackage`，`device: auto`（CoreML 包解析为 cpu 输入侧，不是 CPU 推理）；
- Linux/NVIDIA：导出 `.engine`，`device: 0`；两边的延迟数字不可互换。

### 7.10 学习型与 TSDF（可选，不阻塞主线）

- `pc/stereo/learned.py`：ONNX 适配器 + TensorRT 显式报错入口；缺模型抛 `LearnedMatcherUnavailable`；
- **已预下载** `models/c_fast_foundationstereo_bp2_dynbatch_dynhw.onnx`（约 98.4 MiB，NVIDIA Open Model Agreement，仅研究/评估），但本机没有 onnxruntime/GPU，适配器的 mean/std 与输出语义尚未对齐，**不能声称已接入**；
- `pc/runtime.py` 按平台选 provider：macOS `CoreMLExecutionProvider`、Linux `CUDAExecutionProvider`、Docker 只能 `CPUExecutionProvider`；显式请求不可用时抛错，不静默降级；
- CoreML EP 对动态 shape/算子支持有限；session 创建失败会带 provider 列表，先用 `learned_provider=cpu` 做正确性对照；
- 阶段三顺序：Fast-FoundationStereo → HITNet → 降匹配分辨率 → 纯 SGBM；
- 单目深度先验不进入本阶段（相对尺度污染绝对测距）；
- `pc/tsdf/fuse_offline.py`：需要 Open3D + 显式 `poses_c2w`；没有位姿不融合。

---

## 8. 怎么运行

以下命令均在本仓库根目录 `/home/chenai/桌面/深度/macos-pc`（Mac 上为 `~/Desktop/stereo-depth`）执行。

### 8.1 测试

```bash
.venv/bin/python -m unittest discover -s tests -t .
# Ran 152 tests ... OK (skipped=2)   # 2026-11-28 在 Mac 上实测约 10.5 s
```

- macOS 上跳过的 2 项是平台相关断言；Pi 仓库另有 **81 项**。全套数字以根 `docs/HANDOFF.md` 为准。
- 首次在新机器上跑之前先做一次 `python -m tools.run_demo --out runs/demo --frames 8 --compression`，否则部分测试找不到 `runs/demo/calib/stereo_calib.npz`。

### 8.2 无相机端到端 demo

```bash
.venv/bin/python -m tools.run_demo --out runs/demo --frames 8 --compression
```

产物：`runs/demo/report.md`、`summary.json`、`objects.json`、`cloud.ply`、`overlay.png` 等。

### 8.3 合成标定

```bash
.venv/bin/python -m pc.calib.cli synthetic \
    --out runs/demo/calib/stereo_calib.npz --views 15
```

### 8.4 真实标定（刚性固定后）

```bash
# 目录模式：左右目录文件名排序一一对应
.venv/bin/python -m pc.calib.cli calibrate \
    --left data/calib/left --right data/calib/right \
    --pattern 9x6 --square-mm 30 --out data/calib/stereo_calib.npz

# 录制目录模式（自动跳过没有棋盘的帧；--rotate 从 capture.json 自动读）
.venv/bin/python -m pc.calib.cli calibrate \
    --replay-root ../raspberry-pi/data/recordings/calib_board2 \
    --pattern 9x6 --square-mm 30 --out data/calib/stereo_calib.npz
```

> **2026-11-28 现状**：真实数据在 Pi 的 `data/recordings/calib_board2`（20 对、1640×1232、`rotation_deg=90`），
> 640 档用 `python -m tools.rescale_record --root ... --out ..._640 --width 640 --height 480` 生成 `calib_board2_640`。
> v4 支架的传感器相对基线滚转 90°，**标定与流水线都要先把画面转 90°**（`--rotate 90`，或让 `capture.json` 的
> `rotation_deg` 自动生效）；换成 `fpc_down` 新件后不要再带 `--rotate`。两份产物：
> `data/calib/stereo_calib.npz`（1640，RMS 0.634 px / 基线 80.196 mm）与 `data/calib/stereo_calib_640.npz`
> （640，RMS 0.241 px）。标定器自带护栏：校正后极线残差中位 > 5 px 直接报错、不写 npz。

### 8.5 回放 / 在线处理

```bash
# 回放
.venv/bin/python -m pc.receive.live \
    --replay-root data/recordings/synth6 \
    --calib runs/demo/calib/stereo_calib.npz \
    --out runs/live --save-cloud-every 10

# 在线（先启动 Pi 推流）
.venv/bin/python -m pc.receive.live \
    --endpoint tcp://<PI_IP>:5555 \
    --calib data/calib/stereo_calib.npz \
    --out runs/live --save-every 30
```

输出 `objects.jsonl`、`overlay_*.png`、`source_*.png`、`cloud_*.ply`、`summary.json`。

### 8.6 Pi 服务与 ZMQ

> 下面的 `pi.*` 命令在独立仓库 `raspberry-pi/` 执行；本仓库只有 `pc.*`。

```bash
# Pi 仓库：采集 + 同步 + 推流（刚性固定后）
python -m pi.service --config configs/pi.yaml --record-root data/recordings/run1

# PC：最小接收器（先启动它，再让 Pi 发）
python -m pc.receive.record_pairs \
    --endpoint tcp://<PI_IP>:5555 --out data/recordings/day1 --max-pairs 100

# 本机回环（无相机）
.venv/bin/python -m pi.service --backend synthetic --frames 20
.venv/bin/python -m pc.receive.record_pairs \
    --endpoint tcp://127.0.0.1:5555 --out runs/link --max-pairs 6
```

端口：帧 `5555`，时间戳旁路 meta `5556`。

### 8.7 ROS 2

```bash
# macOS：ROS 2 走 robostack（没有官方二进制），见 docs/ros2_macos.md
micromamba activate ros          # 本机 ~/stereo/bin/bin/micromamba，MAMBA_ROOT_PREFIX=~/stereo/mamba
python -m pc.ros2.pointcloud_node \
    --calib data/calib/stereo_calib_640.npz \
    --endpoint tcp://<PI_IP>:5555 \
    --publish-hz 5

# 无相机回放
python -m pc.ros2.pointcloud_node \
    --calib data/calib/stereo_calib_640.npz \
    --replay-root ../raspberry-pi/data/recordings/calib_board2_640 --max-pairs 300

# Foxglove（浏览器 http://<MAC_IP>:8765）
ros2 run foxglove_bridge foxglove_bridge
```

- macOS 上 `ros2` 子命令要加 `--no-daemon`（daemon 在 macOS 起不来）；RMW 用默认 **CycloneDDS**，别切 `rmw_fastrtps_cpp`（Image 消息几乎全丢）。
- **不要**在 `micromamba activate ros` 的 shell 里跑仓库 venv 的 python（会撞 conda 的 numpy，报 `No module named 'numpy._core._multiarray_umath'`）。

RTAB-Map：

```bash
ros2 launch rtabmap_ros rtabmap.launch.py \
    rgb_topic:=/stereo/left/image_rect \
    depth_topic:=/stereo/depth \
    camera_info_topic:=/stereo/left/camera_info \
    frame_id:=camera_left approx_sync:=true \
    params_file:=ros2/config/rtabmap.yaml
```

Nav2 obstacle layer 见 `ros2/config/nav2_obstacle.yaml`；完整说明见 `docs/ros2_foxglove.md`。

### 8.8 压缩与运动抖动

```bash
.venv/bin/python -m tools.compression_report \
    --replay-root data/recordings/run1 \
    --calib data/calib/stereo_calib.npz \
    --out runs/compression --max-frames 200

.venv/bin/python -m tools.motion_jitter \
    --replay-root data/recordings/static \
    --calib data/calib/stereo_calib.npz \
    --out runs/jitter_static.json
```

---

## 9. 已验证数字（合成场景，Pi 5 本机）

| 指标 | 结果 | 备注 |
|---|---|---|
| 标定基线 B | 83.025 mm（真值 83.0） | 15 视图，合成针孔 |
| 校正焦距 f | 531.125 px | `P1(0,0)` |
| 双目 RMS | 0.0471 px | 验收 <0.5 px |
| 单目 RMS | 左 0.0465 / 右 0.0461 px | 验收 <0.3 px |
| 极线 `\|dy\|` 中位 | 0.0102 px | P90 0.0277 px |
| 物体 `\|ΔZ\|` 中位 | 1.2 mm（0.10%） | 24 样本；单物体最差 6.3 mm |
| 物体 3D 误差中位 | 4.5 mm | 地面约束后 |
| 地面约束 `\|ΔY\|` | 近 7.1→0.7 mm；远 5.5→0.5 mm | 有/无约束都保留 |
| 真值几何区覆盖率 | 65.4% | 纯 SGBM + LR + 纹理门限 |
| 物体像素覆盖率 | 49.0% | |
| 点云 | 20,620 → 3,518 点（17.1%） | 0.03 m 体素 |
| 处理耗时 | SGBM 150 ms；总 266 ms；3.75 fps | **Pi 5**，不是 PC |
| 压缩 H.264 2.5 Mbps | 最大物体深度偏移 0.41% | 800 kbps 0.41%；阈值 2% |
| 同步仿真 | 12.8 ms → 0.0012 ms | 只验证控制方向/收敛 |

**必须强调：** 合成场景没有真实镜头畸变、卷帘快门、运动模糊、真实检测器。这些数字证明坐标/符号/单位/误差量级自洽，**不是实机验收**。

---

## 10. 未验证 / 已知风险（2026-11-28 更新）

| 风险/缺口 | 现状 | 影响 | 下一步 |
|---|---|---|---|
| v4 传感器滚转 90° | 标定与流水线靠 `--rotate 90`（`capture.json` 自动带） | 忘转 → 基线在画面里竖直、`stereoRectify` 等于没做；护栏会在残差 > 5 px 时报错 | 换 PETG `fpc_down` 新件后去掉 `--rotate` 重标 |
| 新件未实装 | `fpc_down` 只做过 STL 几何核验（射线法 21 点、0 非流形边） | 首件可能有 FDM 收缩/孔位偏差 | 打完先用 2.5 mm 钻头通 8 个 Ø2.4 孔、逐个试塞 M2 螺母，再装板量光心距 |
| 运动场景 / 帧同步 | 1640×1232 下 P 环不收敛（skew 中位约 −25 ms）；640 档静态可用 | 动态物体左右不同时刻 → 深度错 | 用运动纹理板跑 `tools/motion_jitter.py` 量化速度边界；必要时降低曝光时间或改用硬件触发 |
| 640 档远处精度 | 640 焦距约 483 px，1 px 视差 ≈ 10 cm | 2 m 外深度粒度粗 | 精细测量切回 1640 档（`stereo_calib.npz`） |
| 检测器 | `detect.backend: color` 占位；Mac 没装 ultralytics | 框/类别不可信 | 装 ultralytics 用真实数据评估；同时看 YOLO 对物体 3D 的影响 |
| RTAB-Map / Nav2 | 未跑（Foxglove 已通） | 建图/导航链路未验证 | robostack 里补 `rtabmap_ros`、Nav2，用 `ros2/config/` 的参数 |
| 学习型匹配器 | ONNX 已下载，适配未对齐、Mac 无 onnxruntime | 无法判断是否值得保留 | 先核对输入/归一化/输出，再按 `docs/stage3.md` 三项验收 |
| TSDF | 缺位姿来源 | 不能作为主线 | 先确定转台/机械臂位姿，再离线试 |
| Pi CPU/散热 | 640 档 13–15 fps 时正常（`throttled 0x0`）；1640 SGBM 约 1.7 s/帧 | 全分辨率不能实时 | 实时主线用 640；1640 只做精细测量 |
| 标定只覆盖静态 | 20 对、RMS 0.634/0.241 px；温度漂移未测 | 长时间运行可能漂 | 隔一段时间复跑 `rig_check`（反推基线）做漂移监控 |

---

## 11. 下一步优先级（2026-11-28 重排）

### P0：换 PETG 新件 + 真实检测

1. **PETG `fpc_down` 件到货** → 装两路模组（排线座朝同侧、朝下）→ 量光心距（应 80 mm）→
   `python -m pi.tools.rig_check --out runs/rig_check` 要判 `baseline_along_x`；
2. 用**不带 `--rotate`** 的方式重采 20 对棋盘（`--min-cells 8 --min-fill 0.35`）→ `check_calib` → 重标，
   丢掉现在依赖 `--rotate 90` 的隐含状态；
3. 装 `ultralytics`（Mac 上 `~/stereo/.venv/bin/pip install ultralytics`）把 `detect.backend` 从 `color` 换成 `yolo`，
   用真实场景看框和物体 3D；
4. 运动边界：慢速移动纹理板，量化 640 档能容忍的速度（`tools/motion_jitter.py`）。

### P1：机器人接入

1. robostack 里补 `ros-humble-rtabmap-ros`、Nav2（`micromamba install -n ros -c robostack-humble ...`）；
2. `pc.ros2.pointcloud_node` 已实测（约 2500 点/帧、5 Hz）→ 接 RTAB-Map：`MemoryThr=300`、`DetectionRate=1.0`，
   确认图像/深度/camera_info 同步；
3. Nav2：ObstacleLayer 订阅 `/stereo/points`，确认代价地图可跑；
4. Foxglove：RGB 上色、关 normals、图像 5–10 Hz、点云 decay 0（bridge 已在 8765 验证）。

### P2：精度与扩展

1. 需要毫米级时切 1640 档（`stereo_calib.npz`，SGBM 约 1.7 s/帧），或只对 ROI 跑全分辨率；
2. 阶段三学习型 A/B（先核对 FFS ONNX 的输入/输出，再严格三项验收，不达标就回退）；
3. TSDF 仅在位姿来源确定后做离线扩展；
4. 温度/长时间漂移监控：定期跑 `rig_check` 反推基线，偏离 80 mm 超过 1.5% 就重标。

---

## 12. 关键文件索引

| 想改什么 | 先看 |
|---|---|
| 配置/参数校验 | `shared/config.py`、`configs/default.yaml`、`configs/pc.yaml` |
| 帧协议/配对 | `shared/messages.py` |
| 合成场景 | `shared/synthetic.py` |
| Pi 采集/同步/推流 | `pi/service.py`、`pi/capture/`、`pi/sync/`、`pi/stream/` |
| 标定 | `pc/calib/calibrate.py`、`pc/calib/cli.py` |
| 校正 | `pc/rectify/rectifier.py` |
| SGBM/ROI/scale | `pc/stereo/sgbm.py` |
| 融合规则 | `pc/fuse/router.py` |
| 物体/地面 | `pc/objects/aggregator.py` |
| 点云/体素 | `pc/depth/pointcloud.py` |
| 检测 | `pc/detect/` |
| 管线编排 | `pc/pipeline.py` |
| 在线/回放 | `pc/receive/live.py`、`pc/receive/subscriber.py` |
| ROS 2 | `pc/ros2/pointcloud_node.py`、`ros2/` |
| 报告/指标 | `pc/validate/`、`tools/run_demo.py` |
| 验收/风险 | `docs/acceptance.md` |
| 接口契约 | `docs/interfaces.md` |
| 联调排障 | `docs/runbook.md` |
| 传输链路预算 | `docs/transport.md`（640×640 是历史假设）、`tools/transport_budget.py` |
| ROS/Foxglove | `docs/ros2_foxglove.md` |
| 阶段三 | `docs/stage3.md` |
| macOS/Docker | `docs/macos_docker.md`、`docker-compose.yml`、`docker/Dockerfile.ros2` |
| Mac 首次执行/搬运 | `docs/PC_MACOS_START.md` |
| macOS 配置/依赖 | `configs/pc_macos.yaml`、`requirements-macos.txt` |
| 预下载模型 | `models/README.md`、`models/SHA256SUMS` |
| 相机实机检查 | Pi 仓库 `docs/camera_check.md` |

---

## 13. 踩过的坑（不要重复）

1. **同步 P 环不能累加周期**：早期 `p += kp*error` 会发散；现在每步从 `base_period_us` + `step` 重算，`step=-kp*error`。
2. **棋盘格多画边**：最初 `border_squares=1` 导致角点多出来，检测全失败；现在 `(cols+1)×(rows+1)` 方格 + 半格白边 quiet zone，SB/经典都能检出 54 角点。
3. **k3 过拟合**：合成无畸变数据上放开 k3 会拟合出 `k3≈0.24`，让 `alpha=0` 裁掉视场并污染 `fB`；默认 `CALIB_FIX_K3` + `CALIB_ZERO_TANGENT_DIST`。
4. **`record_pairs` 属性名**：曾用 `rp.pair.ts_left_ns`，正确是 `rp.pair.left.sensor_ts_ns`；已在回环中修复。
5. **`scale<1` 与 `min_disparity!=0`**：SGBM 内部 minDisparity 不自动缩放，配置层直接禁止。
6. **体素性能**：`np.unique(axis=0)` 在 30k 点约 43 ms；改 `lexsort+reduceat` 后约 17 ms。
7. **PUB/SUB 慢加入**：ZMQ PUB 不保留历史，PC 订阅端必须**先启动**再让 Pi 发；否则前几帧丢失是正常的。
8. **路径已上移**：项目从 `深度/stereo-depth/` 上移到 `深度/`；旧会话里的 `stereo-depth/...` 路径已失效，除非是历史文档。
9. **不要把合成数字当实机验收**：`runs/demo` 报告已显式标注“合成、Pi 5、非目标 PC”。
10. **不要用学习型模型冒充 SGBM**：缺模型时 `LearnedMatcherUnavailable` 显式报错，不静默降级。

---

## 14. 给下一个会话的建议开场

> 先读 `docs/PROJECT_STATE.md`（本文件）和 `README.md`。
> 当前主线是机器人双目感知，不是 3D 建模；默认纯 SGBM（近距自动视差），0.03 m 体素；ROS 2 已在 Mac 上跑通（robostack）。
> 相机已装 v4 支架并完成真机标定（1640 0.634 px / 640 0.241 px，两份 npz）；下一步是 PETG `fpc_down` 新件装板后重标。
> 先在 Pi 仓库看 `docs/camera_check.md`，再跑 152 个 PC 测试和 `tools.run_demo`，
> 最后按 `docs/acceptance.md` 的“尚未完成”清单继续。
> 改代码前先看 `docs/interfaces.md` 的数据契约；改融合规则前先看 `docs/stage3.md` 的“SGBM 优先、不平均”。

---

## 15. 一句话交接

**这是一个能跑、能测、能出报告的实时双目感知基线：合成/回放链路完整，真实相机端到端在 Mac 上 13–15 fps（640 档），真机标定与 ROS 2/Foxglove 均已跑通；尚未验收的是运动/温度漂移、真实检测器、RTAB-Map/Nav2 与学习型匹配器。**
