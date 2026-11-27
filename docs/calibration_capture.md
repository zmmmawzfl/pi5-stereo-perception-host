# 标定板采集与体检（2026-11-22）

标定失败最常见的原因不是算法，是**数据**：拍了 20 对，到标定时
`pc.calib.calibrate` 只有 9 对能用 —— 因为检不出角点的帧会被**静默跳过**，
等它报错时板子已经收走了。

所以流程改成四步，每步都有硬判据，不靠感觉：

1. **Pi 端采集**：边拍边验，只存两只眼都检出完整 9x6 棋盘的帧 → `pi.tools.capture_calib`
2. **体检**：覆盖、尺寸跨度、重复、锐度，一次说清缺什么 → `tools.check_calib`
3. **标定**：在 x86 主机上跑（Pi 跑不动，见 §5 的实测）
4. **验收**：看 baseline / f / RMS 与逐视图残差

---

## 1. 器材与场地

| 项 | 要求 |
| --- | --- |
| 标定板 | `artifacts/calibration/board_9x6_30mm_a3.pdf`：9x6 内角点、30 mm 方格、A3、PVC 雪弗板裱纸 |
| 平整度 | 板面必须绝对平整、不晃；翘曲就重做（工艺与风险见 `artifacts/calibration/README.md`） |
| 光照 | 两侧均匀侧光，别让灯直射板面。正面 160 g 彩激纸有轻微光晕；反光压不住就拍反面（300 g 白卡哑光） |
| 相机 | 两路 IMX219 拧在同一块支架上，先确认没有松动；光心距应 ≈ 80 mm |
| 距离 | 0.35–0.8 m，让棋盘占画面 1/3 ~ 2/3 |
| 背景 | 板子后面别有别的棋盘/格子布/条纹，容易误检 |

拍之前把板举起来、侧着看一眼：**反光点是不是正好盖住角点**。角点被高光吃掉是最常见的"明明很平却检不出"。

## 2. 姿态清单（15 张起，20 张稳）

体检按 3x3 分格（棋盘中心落在画面的哪个格），要求至少 8/9 格有、且中心格必须有。

| 数量 | 位置 | 姿态 |
| --- | --- | --- |
| 2 | 正中 | 一张正对镜头；一张绕竖轴（yaw）转 20–30° |
| 1–2 | 其余 8 格各 | 每张都带点倾斜：yaw ±20–40°、pitch 15–25°、roll ±15° 三者混着来 |
| 2 | 其中两张 | 一张 0.35 m（占满 2/3）、一张 0.8 m（占 1/3）→ 保证尺寸跨度 >= 1.5x |

三条不要：

- 不要连拍同一个姿态（体检会判"重复姿态"）；
- 不要把棋盘贴到画面边缘（角点出画就废，体检会标"贴边"）；
- 不要只在中间一个距离拍（尺寸跨度不够，焦距和深度方向标不准）。

倾斜的作用是让标定能解出畸变与相对姿态的前后一致性 —— 全正对的 20 张，标出来
的 RMS 也可能很小，但边缘畸变其实没被约束。

## 3. Pi 端采集（当拍当验）

```bash
cd ~/桌面/深度/raspberry-pi
.venv/bin/python -m pi.tools.capture_calib \
    --out data/recordings/calib_board \
    --target 15 --attempts 120 \
    --width 1640 --height 1232
```

`--max-skew-ms` 默认 **1000**（棋盘静止，左右帧差多少都不影响标定）。实测 1640×1232 下
P 环收敛不了（时间戳差中位 23 ms），所以别把它卡回 25 —— 那是动态场景的门限。

`--min-move` 默认 **20 px**：新一帧的棋盘四角离已存**任意一帧**的平均位移小于这个值就跳过，
并打印「和已存帧几乎同一姿态」。静止场景反复按快门会存进一堆同一姿态的帧，标定时只贡献
同一个方程，等于白拍 —— 这个门限替你把它们挡掉（`0` 关闭检查；`capture.json` 里
`skipped_duplicate` 记着挡了多少帧）。

采之前先跑一次装配体检（判「视差沿 x / 装反 180°」），见
[`../../raspberry-pi/docs/rig_check.md`](../../raspberry-pi/docs/rig_check.md)：

```bash
.venv/bin/python -m pi.tools.rig_check --out runs/rig_check
```

输出长这样（只有两目都检出 54 个角点才落盘）：

```
[  1] 存   左54 右54    612px  左上   已存 1/15
[  2] 存   左54 右54    604px  中上   已存 2/15
[  3] 跳过  右目未检出
[  4] 存   左54 右54    598px  右上   已存 3/15
      ●  ·  ●
      ·  ·  ·
      ·  ·  ·
      还空着：左中、正中、右中、左下、中下、右下
```

每 3 张打一次 3x3 网格和"还空着"的方位 —— 手里举着板子的时候就知道下一步往哪挪。

参数说明：

- `--target` 合格帧对数（默认 15）；`--attempts` 最多拍多少次（默认 120，防止一直举着板子等）
- `--width/--height` 默认 1640x1232（IMX219 的 4:3 高分辨率）。分辨率太低时角点太密会检不出：**实测 410x308 就检不到 9x6 了**
- `--scale` 检测用的降采样比例（默认 0.5）。1640x1232 降到 820x616 后单帧检测约 10 ms；不降采样约 0.33 s
- `--jpeg` 默认存 PNG 无损（标定要的是像素精度，别用 JPEG）

## 4. 体检（同一台机器上就能跑，不必先拷）

```bash
cd ~/桌面/深度/macos-pc
python3 -m tools.check_calib \
    --root ../raspberry-pi/data/recordings/calib_board \
    --out runs/calib_check.json \
    --montage runs/calib_coverage.png
```

（在 Pi 上用 classic 检测器跑得动：20 对、降采样一半约 1 秒。换到 x86 主机上跑同一条命令，路径换成拷贝过去的位置。）

输出：

```
画面 1640x1232   帧对 20   检测器 classic   缩放 1.0

  #00 ok   L54 R54    612px  倾斜1.12  锐度 1180/ 1215  中心(0.51,0.49)
  #01 ok   L54 R54    705px  倾斜1.41  锐度  980/ 1002  中心(0.33,0.20)
  ...
  #17 FAIL 右目未检出

覆盖 3x3（棋盘中心落在哪格，行 0 = 上）
      ●1  ●1  ●1
      ●2  ●2  ●1
      ●1  ●1  ·
   格子 8/9   尺寸 402..705 px（跨度 1.75x）
   重复姿态 1 组: #03/#11
结论: 可以开标定（18 张可用）
```

判据（`tools/check_calib.py` 顶部常量，改那里就等于改标准）：

| 判据 | 阈值 | 不满足时 |
| --- | --- | --- |
| 可用帧对 | >= 15（提示线 20） | 不合格 |
| 覆盖 | >= 8/9 格，且中心格非空 | 不合格 |
| 尺寸跨度 | 最大/最小 >= 1.5x | 不合格 |
| 重复姿态 | <= 2 组 | 不合格 |
| 棋盘贴边 | — | 只提示（标定时可能被判废） |
| 左右尺寸差 | > 12% | 只提示（对焦/遮挡可疑） |
| 锐度 | < 中位数一半 | 只提示（运动模糊/失焦） |

退出码 0 = 可以标定，1 = 不合格，所以能直接串进脚本。`--montage` 出的是拼图：
每张缩略图上画出检出的棋盘边框（绿 = 可用、红 = 不可用），右上角是 3x3 覆盖示意。

**检测器口径**：体检默认 `classic`（`findChessboardCorners`，快）。标定时用的是
SB（`findChessboardCornersSB`，慢但更宽容）。classic 检不出而 SB 能检出的边界片，
这里会被判成废片 —— 宁可多拍一张。要用标定的口径复核就加 `--detector sb`（很慢）。

## 5. 标定：必须在 x86 主机上跑

Pi 5（aarch64）上 `findChessboardCornersSB` 的实测：820x616 的干净合成棋盘，
**超过 2 分钟没返回**；而经典检测在同一张图上只要 10 ms。标定链路第一步就是 SB，
所以在 Pi 上标定会卡死在第一张图，不是慢，是不可用。

做法：把录制目录拷到 x86 主机（之前的 `ubuntu@10.42.0.110`），在那边跑：

```bash
scp -r data/recordings/calib_board ubuntu@10.42.0.110:/home/ubuntu/pi5-stereo-perception-host/data/recordings/
ssh ubuntu@10.42.0.110
cd ~/pi5-stereo-perception-host
python3 -m tools.check_calib --root data/recordings/calib_board        # 再体检一遍，路径不同容易拷漏
python3 -m pc.calib.cli calibrate \
    --replay-root data/recordings/calib_board \
    --pattern 9x6 --square-mm 30 \
    --out data/calib/stereo_calib.npz
```

标定会打印有效视图数、跳过的帧号，以及：

```
baseline=80.03 mm  f=1400.9 px  stereo RMS=0.31 px
```

（`f` 随分辨率变：1640×1232 下 IMX219 约 **1360–1410 px**；640×480 的合成 demo 才是 531 px。）

验收看三样：

- **baseline** 应 ≈ 80 mm（支架标称）。差 > 0.5 mm 说明装配歪了或某几张姿态有问题
- **RMS** 越小越好，< 0.5 px 算好；> 1 px 说明数据里有糊片/误检，回到第 4 步看提示
- **跳过的帧号** 应该和体检判废的帧基本一致；如果体检通过但标定跳过，说明 classic 与 SB 的分歧落在了边界片上 —— 删掉那几张重标

## 5.1 硬件无关自检：合成板图 → 标定 → 对真值

在花时间举着板子拍 20 张之前，先确认「读图 → 检测角点 → 标定 → 存 npz」这条路是通的。
`tools/synth_calib_views.py` 按你的分辨率渲染 20 对棋盘（焦距按分辨率给，真值写进
`truth.json`），再用同一条标定命令标回去：

```bash
python3 -m tools.synth_calib_views --out runs/synth_calib/views --views 20
python3 -m pc.calib.cli calibrate --left runs/synth_calib/views/left \
    --right runs/synth_calib/views/right --pattern 9x6 --square-mm 30 \
    --min-views 12 --out runs/synth_calib/calib.npz
# 或者：make synth-calib
```

2026-11-28 在 Mac（cv2 5.1）上实测 1640×1232 / f=1400 px / 基线 80 mm / 20 个位姿：

| 量 | 真值 | 标定回来 | 偏差 |
| --- | --- | --- | --- |
| f | 1400.0 px | 1400.711 px | +0.05% |
| baseline | 80.0 mm | 80.007 mm | +0.01% |
| stereo RMS | — | 0.0625 px | — |

偏差在这个量级就说明链路本身没问题：真机标定如果差 5% 以上，问题在**图、板、装法**，
不在代码。

生成器会把 20 个位姿的**棋盘中心轮流铺到 3×3 分格里**（所以合成数据也能过 §4 的
「覆盖 8/9 格」体检：本机实测 `tools.check_calib` 给 9/9、19–20 张可用）。为了两只眼都看全
整块板，落点在角格时得把板拉远，距离范围是按分辨率/焦距自动算的（最短 ≈ 板宽的
3.4×焦距/短边，最长 ≈ 1.9 倍），不用手调 `--dist`。

两点注意：

- 这一步会跑 `findChessboardCornersSB`，**只在 x86 / Mac 上跑**（Pi 上会卡住，见 §5）；
  测试 `tests/test_synth_calib_views.py` 因此在 aarch64 上默认 skip（`RUN_CALIB_IT=1` 可强制）。
- 2026-11-28 第一版生成器写错过两处（棋盘纹理画成 11×9 格却按 10×7 的高宽贴、右目平移
  方向取反），结果标定回来 f 偏 +5%、baseline 偏 +11%、RMS 0.87 px —— **看起来和真实的
  标定问题一模一样**。教训：合成器要给真值、并且自己先被验证过，才能拿来当"链路没问题"
  的证据。

## 6. 失败对照表

| 现象 | 先查什么 |
| --- | --- |
| 右目反复未检出 | 右目对焦、曝光（`rpicam-still` 单独出图看）、排线是否被压 |
| 左右目棋盘尺寸差 > 12% | 两目没同步/装配歪/一个镜头没调焦 |
| 棋盘贴边 | 板子出画了，往中间挪一点重拍 |
| 尺寸跨度 < 1.5x | 只在同一个距离拍了，补一张近的、一张远的 |
| 重复姿态多 | 每次挪动幅度太小，换方位+换角度 |
| 全部未检出 | 板面反光/过曝/糊/图案不是 9x6（数一下黑白格：9x6 内角点 = 10x7 格） |
| 标定 RMS 大但体检通过 | 有轻微糊片，看体检的"锐度"一列挑出来删掉重标 |

## 7. 最短路径

```bash
# x86 / Mac：先自检链路（合成板图 -> 标定 -> 对真值，10 秒）
cd macos-pc && make synth-calib

# Pi：只存合格帧（板要真的动起来）
cd ~/桌面/深度/raspberry-pi
.venv/bin/python -m pi.tools.capture_calib --out data/recordings/calib_board --target 15

# Pi：体检（看得懂就往下走）
cd ~/桌面/深度/macos-pc
python3 -m tools.check_calib --root ../raspberry-pi/data/recordings/calib_board --montage runs/calib_coverage.png

# x86：拷过去再体检一次，然后标定
# 产物落到 data/calib/stereo_calib.npz，被 pc.receive.live / ros2 节点用 --calib 指过去
```
