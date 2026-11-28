# 交付物与验收映射

本项目的交付物是用于“实时双目 3D 重建 / 机器人视觉感知”的链路，不是完整 3D 建模软件。重点是：实时深度、彩色点云、对象级 3D 中心、导航/避障/抓取初筛所需的感知输出；不追求 CAD 级建模、纹理高保真网格或 Blender/SolidWorks 导出。

## 1. 交付物清单

| # | 交付物 | 代码/文件 | 当前状态（2026-11-28） |
|---|---|---|---|
| 1 | Pi 5 采集 + 同步 + 推流服务 | `pi/service.py`、`pi/capture/`、`pi/sync/`、`pi/stream/` | ✅ 实机：1640×1232@15 采集、推流前缩 640×480（jpeg 48.7 KB/帧）；1200 对 0 丢。1640 下 P 环不收敛（skew 中位约 −25 ms） |
| 2 | 时间戳配对分布 + 压缩伪影量化报告 | `pi/tools/timestamp_baseline.py`、`tools/compression_report.py` | 时间戳 ✅（640 档 52.8 ms → 0.56 ms；1640 档 −25 ms 不收敛）；压缩伪影合成 ✅（H.264 2.5 Mbps 0.41%），jpeg 对深度的实机量化未做 |
| 3 | 双目标定参数 + 校正映射 + 极线检查 | `pc/calib/`、`pc/rectify/`、`data/calib/*.npz` | ✅ 真机 20 对：1640 **RMS 0.634 px / B 80.196 mm**；640 **RMS 0.241 px / 80.195 mm**；极线残差 0.50 / 0.194 px |
| 4 | PC 完整处理管线 | `pc/pipeline.py`、`pc/receive/live.py` | ✅ 真实相机端到端（640 档 13–15 fps、单对 57 ms） |
| 5 | 物体级 3D JSON + 体素点云 PLY | `pc/objects/`、`pc/depth/`、`runs/*/objects.jsonl`、`cloud_*.ply` | ✅ 真实相机 640 档每对约 4 个物体、≈2900 点（0.03 m 体素） |
| 5b | ROS 2 点云/深度/对象发布 | `pc/ros2/pointcloud_node.py` | ✅ Mac 上 robostack humble 实测：约 2500 点/帧、5 物体、5 Hz |
| 5c | Foxglove / RTAB-Map / Nav2 接入 | `docs/ros2_foxglove.md`、`ros2/config/` | Foxglove bridge 8765 已连通（子协议握手成功）；RTAB-Map / Nav2 / rviz2 未跑 |
| 5d | macOS 原生 + Docker 混合形态 | `pc/runtime.py`、`configs/pc_macos.yaml`、`docker/`、`docs/macos_docker.md` | macOS 原生 ✅；Docker 的 amd64 镜像是历史基线、tar 已删，arm64 未重建 |
| 6 | 已知距离验证报告 | `tools/run_demo.py` 报告；实机用 `pc.receive.live` + 真值 | ✅ 打印机正面 0.745 m → 640 档近簇中位 0.752–0.762 m（+1%） |
| 7 | 运动边界报告 | `tools/motion_jitter.py` | 工具完成；实机未测 |
| 8 | 可选扩展：TSDF 网格 | `pc/tsdf/fuse_offline.py` | 完成（需 Open3D + 显式 poses；不作为主线依赖） |

## 2. 验收指标与当前证据

| 指标 | 目标 | 当前（2026-11-28 真实相机，除注明） | 结论 |
|---|---|---|---|
| 端到端帧率 | 25–30 fps（Pi 推流） | 640 档 **13–15 fps**（单对 57 ms）；1640 档 2.2 fps | 640 档可用，未到 25 |
| 时间戳差 | 中位数/IQR 记录在案 | 640×480：52.765 ms → 0.56 ms（200 帧 P 环）；1640×1232：中位 **−25 ms**、period 恒 40000 µs（不收敛） | 640 通过；1640 静态可用、动态有时刻差 |
| 运动纹理板深度抖动 | 记录在案 | 工具就绪 | 待测 |
| Pi CPU | 无核心持续满载 | 采集 + 缩 640 + jpeg 稳定 15 fps、1200 对 0 丢；未单独做核心级采样 | 基本通过 |
| 压缩伪影 | 深度偏移 < 1–2% | H.264 2.5 Mbps：0.41%；800 kbps：0.31%（合成）；jpeg q90 未量化 | 合成通过 |
| 单目 RMS | < 0.3 px | 真机 1640 每视图 0.42–0.63 px（640 仅记录立体 RMS 0.241 px） | 1640 略超；640 通过 |
| 双目 RMS | < 0.5 px | 1640 **0.634 px**；640 **0.241 px** | 640 通过；1640 略超 |
| 极线 \|dy\| | 接近 0 | 1640 **0.50 px**；640 **0.194 px** | 通过 |
| 已知距离质心误差 | 记录中位数 | 0.745 m 真值：全分辨率 +1.15%、640 档 +1.7% | 通过（<2%） |
| 地面约束 | 记录前后两组 | 合成：\|ΔY\| 7.1→0.7 mm | 合成有效 |
| 真值几何区覆盖率 | 记录 | 640 档 **32%**（≈2900 点）；1640 默认 3.3%（阈值 2 → 12.8%）；合成 65.4% | 真实场景取决于纹理 |
| 点云体素降采样 | 0.03 m 起 | 合成 20,620 → 3,518 点；真机 640 档约 2900 点/帧 | 完成 |
| ROS 2 发布 | PointCloud2/深度/对象 | ✅ Mac robostack：约 2500 点/帧、5 Hz、point_step 16 | 通过 |
| macOS 原生基础链路 | 测试/demo/回放可跑 | Apple M2：**152 tests OK（2 skip）**、640 档 13–15 fps | 已验证（模型推理除外） |
| macOS CoreML provider | `pc.runtime` 能选到 CoreML | `CoreMLExecutionProvider` 可见；YOLO/FFS 推理未跑（Mac 无 ultralytics/onnxruntime） | provider 通过，精度未验 |
| Docker ROS 2（Humble） | 话题/字段/Foxglove 可通 | amd64 历史镜像（189 包、3.66 fps）tar 已删；arm64 未重建 | 历史，不做验收 |
| Foxglove 可视化 | 可看 | 8765 端口握手成功；真实 3D 面板未逐项验 | 连通 |
| 学习型三项 | 有明确结论 | 未执行 | 待目标机 |

## 3. 风险 → 代码落点

| 风险 | 触发信号 | 代码里的应对 |
|---|---|---|
| Pi CPU 余量不足 | 编码/推流时核心满载 | `pi/service.py` 统计 fps/period；`build_publisher` 可关流；`stream.transport=none` 先测采集；`stream.width/height` 降推流分辨率 |
| 时间戳差 ≠ 曝光同步 | 时间戳小但运动抖动大 | `tools/motion_jitter.py` 以深度抖动为判据 |
| 压缩污染弱纹理 | SGBM 有效像素减少 | `pc/validate/compression.py` 量化；`stream.raw_max_pairs` 预留无损旁路 |
| 学习型超延迟/显存 | >30 ms 或挤占检测 | `learned.py` 显式不可用；`fuse/router.py` 可一键回退纯 SGBM |
| 标定不达标 | RMS >0.3/0.5 px、极线差大 | `calibrate.py` 超 `MAX_EPIPOLAR_DY_PX=5` 直接抛错不落盘；`epipolar_dy` 进 meta |
| 基线竖直导致校正失效 | 校正后同名点 \|dy\| 上百 px | v4 支架必须 `--rotate 90`；`stereoRectify` 只认画面水平基线 |
| 位姿来源未定 | 进入 TSDF 才发现 | `pc/tsdf/fuse_offline.py` 强制要求显式 poses；没有位姿不融合 |
| ROS 2 字段/坐标系不匹配 | Foxglove 点云无色/位置飘 | 节点固定 x/y/z float32 + r/g/b uint8、point_step=16、frame_id=camera_left；`docs/ros2_foxglove.md` 给静态 TF |
| 导航吃不动稠密点云 | 代价地图 CPU 高 | 主线已体素 0.03 m；Nav2 只订阅 `/stereo/points`，不再叠高密度滤波 |

## 4. 尚未完成（必须在目标机/实机完成）

1. 运动场景（纹理板）的深度抖动与准静态速度上界；
2. 阶段三学习型匹配器三项（延迟/显存/精度）——Mac 还没有 onnxruntime，模型语义也未对齐；
3. 真实相机 jpeg q90 对深度/覆盖的量化（目前压缩只测过合成 H.264）；
4. 1640×1232 的帧同步（P 环不收敛，需要硬件触发或换曝光策略）；
5. YOLO 真实检测（Mac 未装 ultralytics，当前 `detect.backend=color` 只验链路）；
6. RTAB-Map / Nav2 / rviz2（Mac 上只有 Foxglove）；
7. TSDF 的位姿来源确认与网格质量评估；
8. PETG 的 `fpc_down` 新件装好后重标（不再需要 `--rotate 90`）。

**没有这些数据之前，任何“已达验收”的结论都不成立。**
