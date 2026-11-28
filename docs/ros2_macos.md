# 在 Mac 上跑 ROS 2（2026-11-28 实测）

> 结论先说：**Mac 上能跑 ROS 2**，但不是官方二进制 —— 用 **robostack 的 conda 包（osx-arm64, humble）**。
> 本机（Mac mini M2，macOS 27.2）已装好并验证：`ros2 pkg list` 189 个包、`rclpy` 可用、
> `pc.ros2.pointcloud_node` 能发布 `/stereo/points`（6565 点，point_step 16）与 `/stereo/objects`，
> `foxglove_bridge` 在 8765 端口接受 WebSocket 握手。**rviz2 没有**（macOS 无官方构建），可视化用 **Foxglove（浏览器）**。

## 1. 为什么不是 `apt install ros-humble-...`

| 路线 | macOS 上可行？ | 说明 |
|---|---|---|
| 官方二进制 | ❌ | ROS 2 只给 Ubuntu/Debian 和 Windows 出二进制；macOS 是 Tier 3（源码编译） |
| **robostack conda** | ✅ **采用** | `robostack-humble` + `conda-forge` 频道，osx-arm64 有完整 humble 包 |
| arm64 Docker | ✅ 备选 | 官方 `ros:humble` 有 linux/arm64 镜像；需要先装 Docker/colima |
| UTM / lima 虚拟机 | ✅ 备选 | 跑 Ubuntu 最"正统"，但多一层 VM，USB/网络都变复杂 |
| 源码编译 | ⚠️ | 官方 Tier 3，几小时起步、坑多，本次没走 |

rviz2 在 macOS 上**没有**官方构建（Qt/OGRE 依赖），所以可视化统一走 Foxglove：它是个网页，
连 `ws://<PC>:8765` 就能看 3D 点云/图像，正好和本项目的 `/stereo/points`（RGB 上色、point_step=16）对得上。

## 2. 安装（本机已装，下面是完整复现步骤）

装在仓库外面，不污染 `~/stereo/.venv`（那个是 Python 视觉链路用的）：

```bash
# 1) micromamba（不需要 root，也不依赖 brew）
mkdir -p ~/stereo/bin
cd /tmp
curl -Ls https://micro.mamba.pm/api/micromamba/osx-arm64/latest | tar -xvj bin/micromamba
mv bin/micromamba ~/stereo/bin/bin/micromamba

export MAMBA_ROOT_PREFIX=~/stereo/mamba
eval "$(~/stereo/bin/bin/micromamba shell hook -s bash)"

# 2) ROS 2 humble（robostack）
micromamba create -y -n ros -c robostack-humble -c conda-forge ros-humble-ros-base
#    下载 252 MB，装完 env 约 900 MB —— 2026-11-28 实测
# 3) 视觉链路要用的库（ros-base 不带 cv2/zmq/yaml）
micromamba install -y -n ros -c conda-forge opencv pyzmq pyyaml
# 4) 浏览器可视化（可选，但要用 Foxglove 看 3D 就得装）
micromamba install -y -n ros -c robostack-humble -c conda-forge ros-humble-foxglove-bridge
```

> 装完是 **Python 3.14**（robostack 的 np2py314 构建），和 `~/stereo/.venv`（3.11）**互不干扰**：
> 在 ROS 环境里跑 `pc.ros2.pointcloud_node`，在 venv 里跑 `pc.receive.live`（浏览器预览那条线）。
> 两边用同一份仓库代码、同一份 `stereo_calib_640.npz`，只是解释器不同。

## 3. 验证（本机实测输出）

```bash
export MAMBA_ROOT_PREFIX=~/stereo/mamba
eval "$(~/stereo/bin/bin/micromamba shell hook -s bash)"
micromamba activate ros

ros2 pkg list | wc -l                     # 189
python -c "import rclpy, sensor_msgs.msg; print(rclpy.__file__)"
python -c "import cv2, zmq; print(cv2.__version__, hasattr(cv2,'ximgproc'), zmq.__version__)"
# 5.1.0 True 27.3.0
```

## 4. 跑 `pointcloud_node`（离线回放，不需要相机）

```bash
cd ~/stereo/macos-pc
python -m pc.ros2.pointcloud_node \
    --calib data/calib/stereo_calib_640.npz \
    --replay-root ../raspberry-pi/data/recordings/calib_board2_640 \
    --publish-hz 4 --idle-timeout 60
```

有相机时把 `--replay-root` 换成 `--endpoint tcp://10.42.0.1:5555`（Pi 的 `pi.service` 推流）。

实测（640 档、20 对回放、CycloneDDS 默认）：

| 话题 | 类型 | 实测 |
|---|---|---|
| `/stereo/points` | sensor_msgs/PointCloud2 | 6565 点、point_step **16**、frame `camera_left` |
| `/stereo/objects` | std_msgs/String | JSON：pair_id / objects / coverage / timings_ms |
| `/stereo/left/image_rect` | sensor_msgs/Image | bgr8 640×480（step 1440） |
| `/stereo/depth` | sensor_msgs/Image | 16UC1 640×480（step 1280），mm，0=无效 |
| `/stereo/left/camera_info` | sensor_msgs/CameraInfo | 来自 npz 的 P1 |

`ros2 topic list --no-daemon` 能看到全部 7 个话题；用 rclpy 订阅 8 秒计到 **20/20/20/20**（点云/对象/左图/深度各 20 条）。

## 5. Foxglove 看 3D（不需要 rviz）

```bash
ros2 run foxglove_bridge foxglove_bridge --ros-args -p port:=8765
```

然后浏览器打开 <https://app.foxglove.dev/>（或 Foxglove Studio 桌面版），
"Open connection → Foxglove WebSocket → `ws://10.42.0.190:8765`"。

- 3D 面板加 `/stereo/points`，Color mode 选 **RGBA/RGB**（点云自带颜色，point_step=16）。
- **对着真实相机实测（2026-11-28 22:1x）**：Pi `pi.service --config /tmp/pi640s.yaml`（1640 采集、推流缩 640、49.5 KB/帧、1200 对 0 丢）→
  Mac `pointcloud_node --endpoint tcp://10.42.0.1:5555 --publish-hz 5` → 60 对、**约 2500 点/帧、5 个物体**，
  rclpy 订阅 8 s 计到 **点云 57 / 深度 56 / 左图 57 / 对象 56**；同时 @@foxglove_bridge@@ 8765 正常 advertising。
- 图像面板加 `/stereo/left/image_rect` / `/stereo/depth`。
- 只想要"看画面"、不想装 Foxglove：直接用 `pc.receive.live --preview-port 8080`（见 runbook §3.4），
  它不依赖 ROS，浏览器打开就有「校正图+框 | 深度伪彩 | 状态条」。

握手细节：bridge 3.6.3 只接受子协议 **`foxglove.sdk.v1`**（只给 `foxglove.websocket.v1` 会返回
"Missing expected sec-websocket-protocol header"）。浏览器/Studio 会自动带对，手写客户端别漏。

## 6. 踩过的坑（都是 macOS + conda 特有）

| 现象 | 原因 | 处理 |
|---|---|---|
| `ros2 topic list` 卡住不返回 | robostack 的 **ros2 daemon 在 macOS 上起不来** | 加 `--no-daemon`（`topic list` / `node list` / `topic echo` 都支持） |
| 图像话题收不到，点云/对象正常 | 我手动切到 `RMW_IMPLEMENTATION=rmw_fastrtps_cpp` 后，**~1 MB 的 Image 消息大量丢**（20 条只到 0–2 条） | **别切**，用默认的 **`rmw_cyclonedds_cpp`**：同一场景 20/20 全到 |
| `import cv2` 失败 | ros-base 不带 OpenCV | `micromamba install -n ros -c conda-forge opencv`（装到 5.1.0，`ximgproc` 在） |
| `import zmq` 失败 | 同上 | 装 `pyzmq` |
| Foxglove 连不上 | 子协议不对 | 见 §5，用 `foxglove.sdk.v1` |
| 端口 8765 被占用 | 上一次 bridge 没退 | `pkill -f foxglove_bridge` |

## 7. 一句话状态

- ✅ **ROS 2 humble 在 Mac 上可用**（robostack conda），`pointcloud_node` 发布点云/深度/图像/对象已验证
- ✅ **Foxglove 可视化链路可用**（bridge 8765）
- ⚠️ `rviz2` 不可用（macOS 无构建），要看 3D 用 Foxglove
- ⚠️ `ros2` CLI 要带 `--no-daemon`
- ⚠️ DDS 用默认的 CycloneDDS，别切 fastrtps（大图像会丢）

## 8. 要不要换 ARM Linux（2026-11-28 结论：暂不换）

用户问过「ARM Linux 会不会比 Mac 更好，打算装 Asahi Linux」，最后决定**继续 macOS**。结论和依据留在这里，免得下次重新论证。

### 8.1 一句话
同一颗 M2、瓶颈是 **CPU 上的 SGBM**（640 档 41–57 ms/对），换系统**不会更快**；好处只在工具链：
原生 ROS 2、rviz2、apt OpenCV、原生 Docker、以及少掉下面 §6 那串 conda/macOS 特有的坑。

### 8.2 对照
| 现在（macOS + robostack） | Ubuntu Asahi |
|---|---|
| `ros2 topic list/echo` 必须加 `--no-daemon`（daemon 起不来） | 原生 ROS 2，daemon 正常 |
| robostack env 900 MB、Python 3.14，和 3.11 venv 的 numpy 冲突 | `apt install python3-opencv python3-zmq`，系统 Python 一套 |
| 没有 rviz2，3D 用 Foxglove 浏览器 | rviz2 直接可用 |
| `ros2 topic hz` 不支持 `--no-daemon` | 原生命令全能 |
| DDS 切 `rmw_fastrtps_cpp` 会丢大 Image（20 条到 0–2 条） | Linux 上两种 RMW 都正常 |

### 8.3 为什么暂不换
- **性能不变**：GPU 不参与（SGBM 走 CPU）；Asahi 也**没有 NPU 开源驱动**，将来上学习型匹配，macOS 还能走 CoreML。
- **风险**：这是唯一的 PC；Asahi 仍标 experimental，M1/M2 图形驱动的核心开发者在 2025 年陆续离开项目，长期维护有不确定性。
- **标定不用重做**：npz 是纯 numpy，换系统照用。

### 8.4 将来真要换的正确路线
1. **发行版必须选 Ubuntu Asahi**，不要 Fedora Asahi Remix —— Fedora 没有 ROS 2 官方 deb，换了还是得 robostack，等于只换了皮肤。
2. 先在 UTM/lima 里起 Ubuntu 24.04 arm64 验证（装 apt ROS 2 + 跑仓库 152 项测试 + pointcloud_node），零风险。
3. 再双系统（安装器缩 APFS 分区，保留 macOS），跑一个月再考虑抹盘。
