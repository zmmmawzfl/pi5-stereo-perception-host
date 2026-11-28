# Pi ↔ PC 联调运行手册

按顺序做四件事：**最短链路 → 标定 → 在线处理 → 离线复算**。
每一步都有明确的"看到什么才算通"。

> 本手册的命令已在同一台机器上用 `pi.service`（synthetic）→ ZMQ → `pc.receive.record_pairs`
> / `pc.receive.live` 跑通；跨两台机器的真实网线/交换机环境尚未实测。

## 0. 一次性准备

**Pi：**

```bash
hostname -I                     # 记下 IP，例如 192.168.1.10
ip -4 addr | grep inet
sudo apt install -y python3-opencv python3-picamera2 python3-zmq
python -c "import cv2, picamera2, zmq; print(cv2.__version__)"
```

**PC：**

```bash
ping <PI_IP>                    # 先确认二层可达
python -c "import cv2, zmq; print(cv2.__version__)"
```

**Linux PC（任意发行版/架构）：** 视觉链路原生 `pip install -r requirements-linux-cpu.txt`（NVIDIA 机器用
`requirements-pc.txt`），**不需要编译**；ROS 2 优先用发行版 apt，只有没有原生 ROS 2 时才用容器。
选型矩阵与镜像复用（tar / buildx）见 [`docs/portability.md`](portability.md)。

**macOS PC：** 视觉链路用 `requirements-macos.txt` 在原生跑；ROS 2/Foxglove 用
`docker compose`（Humble，纯 CPU），不需要在 Mac 上装 ROS 2。详见
[`docs/macos_docker.md`](macos_docker.md)。先在原生确认
`python -m pc.runtime` 能看到 `CoreMLExecutionProvider`。

**端口：** Pi 绑定 `5555`（视频帧）和 `5556`（时间戳旁路）。两端防火墙都要放行 TCP 5555/5556。
PC 是客户端（SUB connect），不需要开放入站。Docker 里访问宿主机用
`host.docker.internal:5555`，不要写 `127.0.0.1`。

## 1. 最短链路：先不要标定

目的：确认"Pi 能发、PC 能按 pair_id 收"。

**Pi：**

```bash
cd /path/to/raspberry-pi
python -m pi.service --config configs/pi.yaml \
    --record-root data/recordings/smoke --frames 300
```

**PC（建议先启动它，SUB 连接后 Pi 再发，避免 PUB/SUB 慢加入丢首帧）：**

```bash
cd /path/to/macos-pc
python -m pc.receive.record_pairs --endpoint tcp://<PI_IP>:5555 \
    --out data/recordings/smoke_pc --max-pairs 100 --idle-timeout 30
```

看到什么才算通：

- PC 端 `summary.json` 里 `emitted > 0`，`orphans ≈ 0`，`duplicates ≈ 0`；
- `ts_diff_ms.median` 有具体数字；
- `data/recordings/smoke_pc/pairs.jsonl` 每行左右文件名都存在，打开看是同一时刻的左右图。

如果 `emitted=0`：先查 Pi 是否真的 bind 成功（`ss -ltnp | grep 5555`）、IP/端口、防火墙。
如果 `orphans` 很多：左右 `pair_id` 没对齐，或 PC 启动太晚错过了同一对的另一侧。

## 2. 标定

### 2a. 采棋盘格（Pi 端当拍当验）

标定板：**A3、9×6 内角点、30 mm 方格**，实物已做好（2026-11-21），文件在 `../artifacts/calibration/`，
做法、翘曲风险与采图前检查清单见 `../artifacts/calibration/README.md`。

用 Pi 端的采集工具，它只在两只眼都检出完整棋盘时落盘，并提示 3x3 网格里还缺哪个方位
（裸录 150 对、标定时才发现只有 9 对能用，是最常见的返工）：

```bash
python -m pi.tools.capture_calib --out data/recordings/calib_board \\
    --target 15 --attempts 120 --width 1640 --height 1232
```

采完先体检（`make check-calib CALIB_ROOT=...`），判据与失败对照表见
[`calibration_capture.md`](calibration_capture.md)。

### 2b. 在 PC 上直接标定（无需手工分左右目录）

```bash
python -m pc.calib.cli calibrate --replay-root data/recordings/calib_board \
    --pattern 9x6 --square-mm 30.0 --out data/calib/stereo_calib.npz
python -m pc.calib.cli inspect --calib data/calib/stereo_calib.npz
```

没有棋盘格的帧会被自动跳过（日志会列出下标），所以多录一些没关系。
方格尺寸用卡尺实测，**不要用标称值**。

通过标准：

- 单目 RMS < 0.3 px，双目 RMS < 0.5 px；
- 极线 `|dy|` 中位数接近 0；
- `B` 与卷尺量到的基线接近（差 1% 以内）；
- 不达标先重采，不要改算法。

> 也可以用 `--left data/calib/left --right data/calib/right` 读两个目录（文件名排序一一对应）。

## 3. 在线联动

**Pi**（保持运行，默认推流 + 同步环）：

```bash
python -m pi.service --config configs/pi.yaml
```

**PC**（另开终端）：

```bash
python -m pc.receive.live --endpoint tcp://<PI_IP>:5555 \
    --calib data/calib/stereo_calib.npz \
    --out runs/live --save-every 30
```

每帧输出一行 `[live] pair=... objects=... total=...ms`，并在 `runs/live/` 写：

- `objects.jsonl`：每帧物体列表（`center_3d`、`bbox_2d`、`depth_source`、`valid_ratio`）；
- `overlay_00030.png`：检测框 + 质心 + 深度；
- `source_00030.png`：每像素来源（绿=SGBM，蓝=学习型）；
- `summary.json`：帧数、fps、各阶段耗时。

先看 overlay 里的框和深度是否合理，再看 `objects.jsonl` 的数字。
**第一次联调不要求精度**，先要求"稳定出物体、不漏帧、深度不跳飞"。


### 3.1 完整链路实测（2026-11-28，v4 支架 + `--rotate 90`）

Pi 推流 → Mac 校正 / SGBM / 深度 / 点云 / 物体，全程真实相机，跑通。
两台机器的配置文件要点：`camera` 必须写 1640×1232 / 15 fps（`pi.service` 的 CLI 不暴露这几项）；
Mac 没有 ultralytics 时用 `detect.backend: color`；`max_skew_ms: 1000` 只是不让 1640×1232 下的
同步环抖动直接中止采集（这个分辨率下 P 环不收敛，标定/静态场景不需要帧同步）。

```bash
# Pi
python -m pi.service --config /tmp/pi_e2e.yaml --backend picamera2 \
    --frames 600 --encoding jpeg --out /tmp/pi_e2e_stats.json
# Mac
python -m pc.receive.live --endpoint tcp://10.42.0.1:5555 \
    --calib /tmp/stereo_calib.npz --config /tmp/pc_e2e.yaml \
    --out runs/e2e_live --max-pairs 6 --save-every 1 --save-cloud-every 1
```

| 指标 | 默认 `texture_threshold=6` | 调到 `2` + `num_disparities=128` |
|---|---|---|
| Pi 采集 | 600 对 / 40 s（15.0 fps）/ dropped 0 | 同左 |
| Pi 推流 | 1200 帧 0 丢、213 MB、173.8 KB/帧、`throttled=0x0` | 同左 |
| Mac 端到端 | 2.32 fps，每对 median 318 ms（SGBM 142–163 ms） | 2.19 fps，median 344 ms |
| 视差有效覆盖 | 3.3% | **12.8%** |
| 点云点数（voxel 0.03） | ≈2000 | **≈4700** |
| 输出物体 | 0 | **2**（`objects.jsonl` 有 `center_3d`） |

- 点云实测（`cloud_00001.ply`，binary little endian，x/y/z + rgb）：z 的 p1/p50/p99 = **0.78 / 1.90 / 2.50 m**，
  x ±0.95 m、y ±1.24 m，与室内场景量级一致。
- `texture_threshold` 是 SGBM 覆盖的真正瓶颈：同一对帧上 SGBM 原始有效 57.6%，融合后只剩 3.4%；
  单独把阈值降到 4 → 5.3%、降到 2 → 12.1%，而 `lr_max_diff` 1→4 几乎无变化（3.41%→3.49%）。
  默认值 6 是「只信强纹理」的保守选择；暗光/白墙场景要更密的深度，显式在 config 里降到 2–3。
- 链路里还没有 ROS 2：Mac 是 arm64、没装 ROS 2，x86 主机 10.42.0.110 当前离线，
  所以 `pointcloud_node` 这一跳仍未验证（见 §7）。

### 3.2 调参旋钮：近距视差范围、中值、WLS（2026-11-28 实测）

默认 `num_disparities=96` 在 f≈1231 px、B≈80 mm 时最近只能测到 `f*B/96 ≈ 1.03 m`；
桌面/机械臂近距会直接测不到（或错配到别的周期）。按 stereo_image_proc 的通用做法
（视差范围取 16 的倍数、`min_disparity` 保持 0），加了一个几何开关：

```yaml
sgbm:
  min_depth_m: 0.5      # 0 = 关闭自动；>0 时按 f*B/Z 自动定 num_disparities
  max_disparities: 256  # 上限，越大越慢
```

| `min_depth_m` | 需要的视差 `f*B/Z` | 实际（向上取 16） | Mac 上 SGBM 耗时（8 对 median） |
|---|---|---|---|
| 关闭（96） | — | 96 | 155 ms |
| 1.0 m | 98.5 | 112 | — |
| 0.5 m | 197.0 | 208 | 246 ms |
| 0.3 m | 328.2 | 256（被 max 截断） | — |

- 代价近似线性：96→208 时 Mac 上 155→246 ms（+58%）；Pi 上更明显（2.07→4.45 s）。实时档位见 §3.3（半分辨率 + tex3）。
- 同一批 8 对（远景 ≈1.9 m）上，128/208 的原始覆盖反而从 53.4% 掉到 49.5%/44.4%——
  远处不需要那么大范围，多出来的范围只是变慢、还更容易被别的周期骗到。**按场景开，不要常开**。

后处理两个可选开关（默认 `none`，同一批 12 对静态帧实测）：

```yaml
sgbm:
  postprocess: median   # none | median | wls
  median_ksize: 3
  wls_lambda: 8000.0
  wls_sigma_color: 1.5
```

| 方案 | 原始 SGBM 覆盖 | 融合后覆盖 | 孤立尖刺占比 | Mac SGBM 耗时 |
|---|---|---|---|---|
| baseline | 53.7% | 3.6% | 0.20% | 138 ms |
| `median`（3x3） | 54.1% | 3.6% | **0.10%** | 165 ms |
| `wls` | **92.2%** | 2.0% | — | 160 ms |

- `median` 只当点云降噪用：尖刺减半、覆盖不变、+20% 耗时。
- `wls` 会把低纹理大面积抹平成连续视差（92% 覆盖），但这些值与右目原始视差不一致，
  过不了左右一致性检查，融合后反而比 baseline 少（2.0% < 3.6%）。**只适合看密度图，不要进度量链路**。
- 也按 OpenCV/RealSense 案例试过匹配前 CLAHE：本场景把原始覆盖从 53.7% 压到 43.9%
  （把画面压暗到 10%/6% 再试仍然更低），原因推测是放大噪声、改变了代价分布。
  **这个开关没有保留**（试完就删），别把 CLAHE 当低纹理的万灵药。

结论：真正有用的是 ①`texture_threshold`（覆盖第一旋钮）②`min_depth_m`（近距几何硬约束）。
median/WLS 默认关，按需开。
#### 3.2.1 近距实测：桌面上的打印机/鼠标/键盘（2026-11-28）

把相机对着书桌支架（打印机、鼠标、键盘、2 m 外的墙），抓一对 1640×1232、v4 + `--rotate 90`，
两种配置各跑一次（产物：`artifacts/near_range_2026-11-28/near_range_compare.png`，左=校正图、中=96、右=208）：

| 指标 | 默认 96 | `min_depth_m: 0.5`（208） |
|---|---|---|
| 融合后覆盖 | 3.58% | **5.19%** |
| 深度 < 1 m 的像素占比 | **0.00%** | **2.81%** |
| 最近可测 Z（= max 视差） | 1.034 m | **0.480 m** |
| 深度 p1 / p10 / p50 | 1.034 / 1.073 / 1.166 m | 0.480 / 0.705 / 0.913 m |
| Mac SGBM / 单对总耗时 | 166 / 408 ms | 244 / 446 ms |
| Pi 上 SGBM 耗时（只看量级） | 2.07 s | 4.45 s |

- 画面里键盘前沿、鼠标、打印机底部这些 <1 m 的面，**96 全部是洞**；208 才出来（橙色=近）。
- 代价在目标机 Mac 上只有 **+78 ms SGBM / +38 ms 整对**；Pi 上翻倍，但 Pi 本来不跑 SGBM。
- 键盘约 0.5 m，正好压在 208 的 0.48 m 边界上——**再近就要 `min_depth_m: 0.4`（256）**。
- 还没有卷尺真值：这一条只证明「近距面能测出来」，度量精度仍以标定验收（§5.3）为准。

### 3.3 实时档：半分辨率 + tex3（2026-11-28 实测）

目标机 Mac 上 SGBM 是唯一的大头（整对 376 ms 里 225 ms 是它）。把匹配放到 0.5 倍图、
同时把纹理门限降到 3，覆盖反而更高、速度快 5 倍：

```yaml
sgbm:
  min_depth_m: 0.5      # 全分辨率等效 208；scale 0.5 时自动落成工作分辨率 112
  scale: 0.5
  texture_threshold: 3.0
```

| 配置 | 融合覆盖 | 近处中位深度（真值 0.745 m） | 孤立像素 | Mac SGBM | 整对中位 |
|---|---|---|---|---|---|
| 全分辨率 208、tex 6 | 5.19% | 0.754 m（+9 mm） | 0.62% | 217 ms | 391 ms |
| 全分辨率 208、tex 2 | 10.5% | 0.752 m（+7 mm） | 0.40% | 229 ms | — |
| **半分辨率 112、tex 3** | **9.58%** | 0.752 m（+7 mm） | **0.34%** | **41 ms** | **185 ms** |
| 半分辨率 112、tex 2 | 11.8% | 0.753 m（+8 mm） | 0.32% | 42 ms | — |

- 同一场景搬上链路实测（Pi 推流 15 fps）：**2.25 fps → 4.61 fps**，单对 376 → 185 ms；
  物体每对 4–5 个（近处键盘/鼠标/打印机把物体数也带起来了）。
- `scale<1` 时视差范围会按同一比例缩（208 → 112），这是 `auto_num_disparities(..., scale=)`
  的责任，别手写全分辨率的 208 塞进 0.5 倍图（会白跑一倍范围）。
- 代价：深度精度略降（0.75 m 处 +7 mm，约 1%），远处细节变少；精细测量仍用全分辨率。
- `tex 2` 还能再多 2 个百分点覆盖，但门限越低越容易把噪声当纹理，先用 3。

## 4. 离线复算（同一份数据反复调参）

```bash
python -m pc.receive.live --replay-root data/recordings/smoke_pc \
    --calib data/calib/stereo_calib.npz --out runs/replay

python -m tools.compression_report --replay-root data/recordings/smoke_pc \
    --calib data/calib/stereo_calib.npz --out runs/compression --max-frames 200

python -m tools.motion_jitter --replay-root data/recordings/static \
    --calib data/calib/stereo_calib.npz --out runs/jitter_static.json
```

## 5. 端口与常见故障

| 现象 | 检查 |
|---|---|
| PC 一帧都收不到 | Pi `ss -ltnp \| grep 5555`；`ping <PI_IP>`；防火墙；endpoint 是否写成 Pi 的 IP 而不是 127.0.0.1 |
| 只有 orphan | PC 启动晚于 Pi；`--stale` 太小；左右 pair_id 生成逻辑 |
| 有 pair 但解码失败 | JPEG 载荷损坏；检查 MTU/网络；先降 `jpeg_quality` |
| 物体框对但深度离谱 | 是否用了 `pc.calib.cli synthetic` 的假内参；`--calib` 是否指向实机标定 |
| 框的位置整体偏移 | 标定分辨率与运行分辨率不一致；看 pipeline 启动告警 |
| 只有右目/左目 | Pi 侧 `rpicam-hello --list-cameras`；CSI 排线 |
| 在线 fps 低 | PC 端 SGBM 参数、检测器；先 `--detect` 换成 color 排除 YOLO |

## 6. 第一次联调的验收清单

- [ ] 两个相机都能单独出图；
- [ ] PC 收到成对帧，`orphans=0`；
- [ ] 标定 RMS 达标，极线 `|dy|` 接近 0；
- [ ] 在线跑 100 对不崩，overlay 正常；
- [ ] 静止目标深度抖动 < 基线 1–2%；
- [ ] 记录 Pi 全开时的 CPU/温度/是否降频；
- [ ] 记录一段已知距离（0.5 m / 1 m）做绝对误差表。

## 7. ROS 2 / Foxglove 联调（在装了 ROS 2 的 PC 上）

先确认纯 Python 管线能出点云（不需要 ROS 2）：

```bash
python -m pc.receive.live --replay-root data/recordings/day1 \
    --calib data/calib/stereo_calib.npz --out runs/live --save-cloud-every 10
```

再起 ROS 2 节点：

```bash
source /opt/ros/humble/setup.bash
python -m pc.ros2.pointcloud_node --calib data/calib/stereo_calib.npz \
    --endpoint tcp://<PI_IP>:5555 --publish-hz 10
ros2 run foxglove_bridge foxglove_bridge
```

检查顺序：

1. `ros2 topic hz /stereo/points` 应接近 `--publish-hz`，而不是 Pi 的采集帧率；
2. `ros2 topic echo /stereo/left/camera_info --once` 的 `width/height` 与深度图一致；
3. Foxglove 里点云位置不飘、颜色正常（RGB 模式）、没有 normals；
4. 静态 TF：`ros2 run tf2_ros static_transform_publisher 0 0 0 0 0 0 world camera_left`；
5. 端到端延迟与 CPU/内存记进验收表：`live` 的 `summary.json.resources` 是同一套进程级指标。

常见故障：

| 现象 | 检查 |
|---|---|
| Foxglove 点云全白/全黑 | 颜色字段是否为 `r/g/b`；面板 Color mode 选 RGB |
| 点云不动/重叠 | TF 是否重复发布；decay 是否过大 |
| RTAB-Map 不同步 | 图像、深度、camera_info 是否同一 `stamp`、同一尺寸 |
| 代价地图 CPU 高 | 是否绕过体素直接喂了稠密点云；`/stereo/points` 应已降采样 |
