# 640×640 视频传输：raw 还是压缩？

结论先说：**640×640@30fps 传 raw BGR 在三层链路里“能跑”，但不建议作为默认**。
如果目标是去掉 JPEG 压缩伪影，优先考虑 **raw 灰度/Bayer**，而不是 raw BGR；
如果目标是低延迟/低 CPU，JPEG q90 仍是默认。

## 1. 先确认 640×640 不是 IMX219 原生模式

本机 `rpicam-hello --list-cameras` 当时列出的模式是 640×480、1640×1232、
1920×1080、3280×2464，**没有 640×640**。Picamera2 拿到 640×640 时只能
对某个 sensor mode 做裁剪/缩放；这会改变视场和像素纵横比，标定必须按
最终实际输出重做。

> 2026-11-07 复测时两块 IMX219 都已 probe 失败（`-121`），所以本轮没有
> 重新验证 640×640 的 mode selection。先修好排线/供电，再确认它是
> “1640×1232 裁剪”还是“640×480 拉伸”；后者不应作为标定输入。

## 2. 带宽（30 fps，左右两目合计）

公式：`Mbps = 每对字节 × 8 × fps / 1e6`。1 GbE 线速 1000 Mbps；
实际 TCP 可用约 110–118 MB/s（880–944 Mbps），再扣协议/拷贝/突发。

| 载荷 | 每对字节 | Mbps | 1GbE 占用 | 1GbE 序列化/对 |
|---|---:|---:|---:|---:|
| raw BGR8 640×480 | 1,843,200 | 442.4 | 44% | 14.7 ms |
| raw BGR8 640×640 | 2,457,600 | 589.8 | 59% | 19.7 ms |
| raw 灰度8 640×640 | 819,200 | 196.6 | 20% | 6.6 ms |
| Bayer10 640×640（近似） | 1,024,000 | 245.8 | 25% | 8.2 ms |
| JPEG q90 640×640（估值 150 KB/对） | 153,600 | 36.9 | 3.7% | 1.2 ms |
| JPEG q95 640×480（实测 160.1 KB/对） | 163,942 | 39.4 | 3.9% | 1.3 ms |
| JPEG q80 640×480（实测 72.8 KB/对） | 74,547 | 17.9 | 1.8% | 0.6 ms |
| H.264 2.5 Mbps（实测 21.6 KB/对） | 22,118 | 5.3 | 0.5% | 0.2 ms |

把 640×480 的 JPEG 数字直接乘 1.33 可估 640×640：q95 约 52 Mbps、
q80 约 24 Mbps。**这些是估算，不是 640×640 实测。**

复现：

```bash
python -m tools.transport_budget --width 640 --height 640 --fps 30
python -m tools.transport_budget --width 640 --height 640 --fps 15 --encodings raw_bgr8,jpeg
```

## 3. 延迟与 CPU：raw 省的是编解码，不是链路

Pi 5 上对合成 640×480 帧的实测（单帧，非流水线）：

| 操作 | 640×480 | 640×640（随机噪声最坏情况） |
|---|---:|---:|
| JPEG q90 编码 | 1.5–2.9 ms | 3.9 ms |
| raw BGR 拷贝 | 0.26 ms | 0.36 ms |
| JPEG 解码（PC 侧参考） | 1.6 ms | 5.9 ms |

raw BGR 每对至少要在 1GbE 上串行 14.7 ms（640×480）/ 19.7 ms（640×640），
而 JPEG q90 只要 1–2 ms。也就是说：

- **CPU**：raw 省掉 Pi 的 JPEG 编码（每对约 3–6 ms）和 PC 的解码（约 3–6 ms）；
- **链路**：raw 把每对突发从 ~150 KB 提高到 ~2.4 MB，序列化时间增加一个数量级；
- **端到端延迟**：如果一次只处理一对，raw 往往更差；只有流水线足够深、
  编码 CPU 真的是瓶颈时，raw 才可能持平或更好。

当前链路的瓶颈在 SGBM（Pi 5 上约 150 ms/帧，总 266 ms），不是 JPEG 编码。
在没有把 SGBM 降到 30 fps 之前，换 raw 不会让端到端更快。

## 4. ZeroMQ/TCP 的额外成本

当前 `ZmqPublisher` 是 PUB + `send_multipart([header, payload])`，TCP 传输：

- 2.4 MB 消息会被拆成约 1600 个 1500B 帧；开启 jumbo frame（MTU 9000）
  可把分片数降到约 270，但需要 Pi、PC、交换机整条路径都支持；
- PUB 的 `SNDHWM=4` 在 raw 下意味着最多缓存约 10 MB；网络抖动时会丢帧，
  丢的是整对中的一侧，`PairAssembler` 会计为 orphan；
- TCP 的 head-of-line blocking 会让一帧大包阻塞后面的小包；直连千兆
  比经过交换机/路由器稳，但仍有突发排队；
- 建议 raw 评估时把 `SNDHWM` 调到 1–2、PC 端 `RCVHWM` 调大，并记录
  `PairingStats.orphans/duplicates`，不要只看“平均 Mbps”。

## 5. 建议

| 场景 | 建议 |
|---|---|
| 默认实时感知（30 fps） | **JPEG q90**；链路占用 <5%，编码 CPU 不是瓶颈 |
| 想要无损几何、可接受灰度 | **raw 灰度 8-bit**（196 Mbps@640×640），SGBM 直接用灰度；检测另走低频彩色 |
| 想要无损彩色、30 fps | raw BGR 640×640 = 590 Mbps，**不推荐默认**；只建议直连 1GbE + jumbo frame + 充足 HWM，且先实测 orphans |
| 想要无损彩色、15 fps | raw BGR 约 295 Mbps，可行；适合标定/离线参考，不适合高帧率 |
| 只想验证压缩伪影 | 用 `FilePublisher` 落 PNG 或 `raw_bgr8` 离线对比，不要占用实时链路 |

代码里已经可以通过配置切换：

```yaml
stream:
  encoding: raw_bgr8   # 或 jpeg
```

```bash
python -m pi.service --backend picamera2 --encoding raw_bgr8 --frames 100
python -m pc.receive.record_pairs --endpoint tcp://<PI_IP>:5555 --out data/recordings/raw
```

如果后续确认“灰度足够、彩色只用于检测”，更合理的是新增 `raw_gray8`
线格式（本仓库尚未实现；`shared.messages.ENCODINGS` 目前只有
`raw_bgr8/jpeg/h264`），而不是把 BGR 三通道全传过去。

## 6. 实测记录（640×480，已有）

来自 `runs/demo/report.md` 的压缩对照：

- q95：39.36 Mbps/对，物体质心深度最大偏移 0.00%；
- q80：17.89 Mbps/对，最大偏移 0.13%；
- H.264 2.5 Mbps：5.30 Mbps/对，最大偏移 0.41%；
- 结论：在 640×480 下，压缩对流没造成超过基线 1–2% 的深度偏移；
  这也是“默认 JPEG”仍然合理的原因。

640×640 的 JPEG 数字、raw 的 orphans、jumbo frame 收益都还没有实机测；
相机恢复后应按 `docs/acceptance.md` 的“压缩伪影”与“链路”两节补测。
