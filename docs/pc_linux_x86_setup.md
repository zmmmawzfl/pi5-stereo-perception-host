# x86 Ubuntu 主机上的 PC 环境（2026-11-14 实测）

> 状态：这台 x86 主机（Ubuntu 26.04）已能跑通 PC 端全部 Python 测试、合成 demo、
> 与 Pi 的 ZMQ 实时链路，以及 ROS 2 Humble 容器（`stereo-depth-ros2:humble`）。
> **阻塞项**：Pi 的 CSI 相机在 2026-11-14 19:10 那次开机没有被识别，真实 Pi→PC 链路未跑。

## 0. 主机与网络

| 项 | 值 |
|---|---|
| 主机 | x86_64，Intel Pentium G4560 @ 3.5 GHz（2C/4T），7 GB RAM（桌面占约 5 GB），Intel HD 610 |
| 系统 | Ubuntu 26.04.1 LTS（resolute），kernel 7.0.0-43-generic，Python 3.14.4，git 2.53 |
| 地址 | `10.42.0.110/24`（enp1s0），默认路由经 `10.42.0.1`（Pi 的 NAT）；链路 1000 Mb/s |
| 账号 | `ubuntu` / `admin`（已加入 docker 组，重新登录后免 sudo） |
| 与 Pi 的连接 | 网线直插 Pi 的 eth0；Pi 侧 `10.42.0.1/24`，`ethtool eth0` = 1000 Mb/s Full |

登录（在 Pi 上执行，Pi 已装 `sshpass`）：

```bash
sshpass -p admin ssh -o StrictHostKeyChecking=no ubuntu@10.42.0.110
```

## 1. 原生依赖（apt，系统 Python 3.14）

```bash
sudo apt-get install -y python3-opencv python3-numpy python3-yaml python3-zmq \
                        python3-pip python3-venv ffmpeg
```

实测版本：OpenCV 4.10.0（`cv2.ximgproc` 可用）、numpy 2.3.5、pyzmq 27.1.0、ffmpeg 8.0.1。
**不需要** `bpy`/`trimesh`——PC 端代码只把它们当可选依赖。

## 2. 代码同步

GitHub 直连在这台机器上不可用（`RPC failed; curl 28 GnuTLS recv error (-110)`）。
当前做法：在 Pi 上打包 `macos-pc` 后 scp 过来，解压到 `/home/ubuntu/pi5-stereo-perception-host`：

```bash
# Pi 上
cd /home/chenai/桌面/深度 && tar --exclude=.git --exclude=runs/_test -czf /tmp/pc.tar.gz macos-pc
# x86 上
scp <pi>:/tmp/pc.tar.gz /tmp/ && tar -xzf /tmp/pc.tar.gz && mv macos-pc pi5-stereo-perception-host
```

更新时重复第 2 步并用 `rsync -a --delete --exclude .git` 覆盖即可（先确认 `runs/` 里没有想留的输出）。

## 3. 测试与 demo（实测）

```bash
cd ~/pi5-stereo-perception-host
OPENCV_OPENCL_RUNTIME=disabled python3 -m unittest discover -s tests -t .
# Ran 80 tests in 11.135s — OK
```

合成 demo（8 帧，`runs/demo` 合成标定 B=83.025 mm、f=531.125 px、RMS=0.0471）：

```bash
python3 -m pc.demo --frames 8 --out runs/demo_x86
```

| 指标 | 值 |
|---|---|
| 帧率 | 2.78 fps |
| rectify / SGBM / fuse / subpixel（中位） | 4.97 / **173.69** / 39.29 / 36.64 ms |
| detect / aggregate / depth（中位） | 24.31 / 83.63 / 1.63 ms |
| 单帧总计（中位） | 359.8 ms |
| 覆盖率 fused / on_gt | 0.2688 / 0.654 |

**SGBM 是绝对瓶颈**（约一半时间）；这台 CPU 上不要再指望 CPU 端提到 10 fps。

## 4. 与 Pi 的实时链路（已验证）

先起订阅端，再发；`SyntheticBackend` 没有节流（约 290 fps），发得太快会因 slow joiner 丢光：

```bash
# Pi
.venv/bin/python -m pi.service --backend synthetic --encoding jpeg \
        --frames 100000 --no-sync --duration 120
# x86
python3 -m pc.receive.live --endpoint tcp://10.42.0.1:5555 \
        --calib runs/demo/calib/stereo_calib.npz \
        --max-pairs 20 --idle-timeout 15 --out /tmp/live_e2e --save-every 0
```

实测：`received 40 / emitted 20`、3.07 fps、skew 3244 ms（合成时钟跑飞，属伪影）。
3.07 fps 与本地 2.78 fps 吻合 → **网络不是瓶颈**。

## 5. Docker 与 ROS 2 Humble

安装：`docker.io` 29.1.3、`docker-buildx` 0.30.1、`docker-compose-v2` 2.40.3。

Docker Hub 直连不可用，宿主机上有 xray（`127.0.0.1:10808`，HTTP/SOCKS 混合）。
daemon 级代理写在 `/etc/systemd/system/docker.service.d/proxy.conf`：

```ini
[Service]
Environment="HTTP_PROXY=http://127.0.0.1:10808"
Environment="HTTPS_PROXY=http://127.0.0.1:10808"
Environment="NO_PROXY=localhost,127.0.0.1,10.42.0.0/24,docker.m.daocloud.io,docker.1ms.run,hub.rat.dev"
```

**镜像站必须列进 `NO_PROXY`**：国内镜像走 xray 大文件会卡住不动，直连才快。

拉基础镜像 + 构建（BuildKit 的 resolver 经代理会 `EOF`，用经典 builder + 本地 tag）：

```bash
docker pull docker.m.daocloud.io/library/ros:humble-ros-base
docker tag  docker.m.daocloud.io/library/ros:humble-ros-base ros:humble-ros-base
cd ~/pi5-stereo-perception-host
DOCKER_BUILDKIT=0 docker build --network=host -f docker/Dockerfile.ros2 -t stereo-depth-ros2:humble .
```

验证：

```bash
docker run --rm --network=host stereo-depth-ros2:humble bash -lc \
  'source /opt/ros/humble/setup.bash; ros2 pkg list | wc -l; ros2 pkg list | grep -E "foxglove_bridge|rmw_cyclonedds"'
# 189 / foxglove_bridge / rmw_cyclonedds_cpp
```

容器内 Python 侧：`cv2 4.5.4`（ximgproc 可用）、`numpy 1.21.5`、`zmq 22.3.0`。
`docker compose config -q` 与 `docker compose run --rm --no-deps ros2` 均已通过。

> 容器是 **CPU-only**：不要在里面跑 YOLO/FFS 的性能数字，也不要装 TensorRT/CUDA。

## 6. 当前阻塞与下一步

- **Pi 相机没识别**：2026-11-14 19:10 开机，dmesg 报
  `imx219 11-0010: Error reading reg 0x0000: -121`、
  `probe with driver imx219 failed with error -121`（10-0010 同样），
  `rpicam-hello --list-cameras` 返回 `No cameras available!`。
  → 需要重新插紧两路 CSI 排线，再跑 `rpicam-hello --list-cameras` 确认。
- 相机恢复后：真实 Pi→x86 E2E（`--encoding jpeg`，再用 `raw_bgr8` 对比 442.4 Mbps 预算）。
- ~~标定板未准备~~ → **2026-11-21 已完工**：A3 / 9×6 内角点 / 30 mm 方格，贴在 3 mm 雪弗板上（做法与采图前检查见 `artifacts/calibration/README.md`）。
  `data/calib/` 仍为空（还没采图），所以端到端验证仍只能用 `runs/demo` 的合成标定。
- ROS 2 节点联调需要真实点云/相机标定，暂缓。
