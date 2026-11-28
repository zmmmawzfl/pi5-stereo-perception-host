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
    --target 20 --attempts 600 \
    --width 1640 --height 1232
```

**开始之后就要一直挪板**（平移换格子、前后换大小、倾斜换角度）。工具每 `--interval` 秒
（默认 0.4）才取一帧，就是为了留出这个时间；板立着不动时会被判成重复姿态跳过，并在连续
5 次跳过时打印「下一步把板挪到画面哪一格、更近还是更远」。

`--max-skew-ms` 默认 **1000**（棋盘静止，左右帧差多少都不影响标定）。实测 1640×1232 下
P 环收敛不了（时间戳差中位 23 ms），所以别把它卡回 25 —— 那是动态场景的门限。

判重有两道门（`capture.json` 里 `skipped_duplicate` 记着挡了多少帧）：

1. `--min-move` 默认 **20 px**：新一帧的棋盘四角离已存**任意一帧**的平均位移小于这个值就跳过
   （`0` 关闭）。
2. **同一格 + 同一距离 + 同一倾角**（尺寸差 <12%、朝向差 <8°）也跳过 —— 2026-11-28 加的：
   实测过一次「板没动、手却在晃」的采集，20 对里有 14 对是重复姿态、全挤在正中一格，
   体检直接判「先别标定」。只比四角位移挡不住这种，因为轻微晃动就能超过 20 px。

另外 **`--min-cells` 默认 8**（和本仓库 `tools/check_calib.py` 的 `COVERAGE_MIN_CELLS = 8`
对齐，否则工具收在 6/9、体检又判「先别标定」，白跑一趟）：收够 `--target` 对但覆盖的格子
还不够时会继续拍，全部满足才停，不会多存一张。

覆盖不够**不要删**，直接补拍缺的格子（已存的帧不动，编号接着往下排）：

```bash
.venv/bin/python -m pi.tools.capture_calib \
    --out data/recordings/calib_board --resume
```

`--resume` 会先从 `capture.json` 的 `quads` 恢复上次的姿态（没有就逐张重检已存的左图），
再打印「已有 N 对、覆盖 M/9 格」和下一步往哪挪。2026-11-28 实测：21 对、覆盖 4/9，
补拍时只需盯着右边那一列 + 下排。

采之前先跑一次装配体检（判「视差沿 x / 装反 180°」），见
[`../../raspberry-pi/docs/rig_check.md`](../../raspberry-pi/docs/rig_check.md)：

```bash
.venv/bin/python -m pi.tools.rig_check --out runs/rig_check
```

输出长这样（只有两目都检出 54 个角点才落盘）：

```
[  1] 存   左54 右54    612px  左上   已存 1/20
[  2] 存   左54 右54    604px  中上   已存 2/20
[  3] 跳过  右目未检出
[  4] 存   左54 右54    598px  右上   已存 3/20
      ●  ·  ●
      ·  ·  ·
      ·  ·  ·
      还空着：左中、正中、右中、左下、中下、右下
```

每 3 张打一次 3x3 网格和"还空着"的方位 —— 手里举着板子的时候就知道下一步往哪挪。

参数说明：

- `--target` 合格帧对数（默认 15；建议 20）；`--attempts` 最多拍多少次（默认 600，配合 0.4 s 间隔约 4 分钟）
- `--interval` 每次尝试之间的最小间隔（默认 0.4 s；`0` 不限速 —— 不限速时人手根本来不及挪板，会拍到一堆重复姿态）
- `--min-cells` 至少覆盖几格才收工（默认 8，对齐体检的 8/9 要求；9 = 全铺满）
- `--resume` 接着已有目录补拍（不动已存帧，恢复姿态后只补缺的格子）
- `--width/--height` 默认 1640x1232（IMX219 的 4:3 高分辨率）。分辨率太低时角点太密会检不出：**实测 410x308 就检不到 9x6 了**
- `--detector` **默认 `auto`**，与 `pc.calib` 完全同口径：先 SB，再经典算法，最后「降到 0.5 / 0.35 倍粗检 + 回原图亚像素精化」。
  边界帧（板占半幅 + 略糊）只有在降采样图上才检得出，用 `classic` 会把它误判成废片，所以别再拿 `--detector classic` 当默认。
- `--scale` 检测用的降采样比例（默认 1.0；只对 `classic` 这条路有意义）。1640x1232 降到 820x616 后单帧检测约 10 ms；不降采样约 0.33 s
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
| 覆盖 | **检测到的角点**落在 >= 8/9 格，且中心格非空 | 不合格 |
| 板占画幅宽 | 棋盘长边 / 画面宽 >= 35%（>= 45% 为佳） | 不合格 / 只提示 |
| 尺寸跨度 | 最大/最小 >= 1.5x | 不合格 |
| 重复姿态 | <= 2 组 | 不合格 |
| 棋盘贴边 | — | 只提示（标定时可能被判废） |
| 左右尺寸差 | > 12% | 只提示（对焦/遮挡可疑） |
| 锐度 | < 中位数一半 | 只提示（运动模糊/失焦） |

**为什么覆盖按角点算、不按棋盘中心算**：A3 板在 1640 宽、f≈1400 下要占一半画宽，
距离就得 ~0.5 m，此时板中心最多只能偏轴半画幅的 49% —— 中心永远落不进四角格。
而畸变恰恰是靠画面边缘的角点约束的，所以看的是「角点伸到了哪些格」。

**板占画幅宽是最容易被忽略的一条**：实测那批旧数据里板只有 229..454 px 宽（14%–28%），
哪怕 9 格铺满，焦距/距离/畸变也会互相抵消，标出来的参数不可信。文献的经验值是
正视时目标占画面 **50%** 像素。A3 板在 1640×1232、f≈1400 下对应 **0.6–0.8 m**。

退出码 0 = 可以标定，1 = 不合格，所以能直接串进脚本。`--montage` 出的是拼图：
每张缩略图上画出检出的棋盘角点与边框（绿 = 可用、红 = 不可用），右上角是 3x3 覆盖示意。

**检测器口径**：体检默认 `classic`（`findChessboardCorners`，快）。标定时用的是
SB（`findChessboardCornersSB`，慢但更宽容）。classic 检不出而 SB 能检出的边界片，
这里会被判成废片 —— 宁可多拍一张。

### 4.1 竖直基线：`stereoRectify` 不认

v4 支架的孔位是 13.25 × 21 的对称矩形，模组装上去时**传感器相对基线转了 90°**（排线座所在的 25 mm 长边是竖着的）。
于是：**不管整机是立着放还是侧躺，画面里的基线都是竖直的**（立着 = 滚转 90°，侧躺 = 再转 90°，合并 180°，
两种都让基线竖着）—— 这不是摆放问题，是装法问题，**把支架立起来并不能修好**。

`cv2.stereoRectify` 是按水平基线设计的：基线竖直时它算出的 `R1/R2` 接近单位阵、视差仍留在 y 方向，
标定会「成功」但校正后同名点竖直残差两三百像素（实测 219 px），npz 是废的。

两条正解：

1. **换 fpc_down 版支架**（`stl/camera_mount_b80_fpc_down.stl`，见 `raspberry-pi/hardware/README.md` §10）：
   板转 90°、21 mm 孔距横放，画面里的基线就是水平的，标定/pipeline 都不用改。
2. **保留 v4，软件里把输入帧转 90°**（已实现，见 §5.2）：标定、体检、pipeline 都认一个 `rotation_deg`，
   从 `capture.json` 自动读。

护栏：`pc.calib` 校正后竖直残差中位 > `MAX_EPIPOLAR_DY_PX`（5 px）直接报错、不写 npz，退出码 2。
实测对照（2026-11-28，同一批 20 对）：原始方向 RMS 3.00 px、残差 219 px；同批图按 90° 标定后
RMS 2.63 px、残差中位 2.11 px、f 1339 px、基线 82.79 mm → 几何没问题，纯粹是朝向。

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

标定内部还有一层自动剔除：先全量标一次，逐视图算极线残差
（`|y_left - y_right|` 的中位数），超过 `max(1.0 px, 3×中位数)` 的视图会被丢掉重标，
日志里会打印 `[calib] 剔除 N 个极线残差异常视图（原序号 [...]），重算`。
只在「个别视图坏了」时生效；如果三成以上的视图都超阈，它就一个都不剔并保留警告 ——
那说明是系统性口径问题（板太平、镜头脏、同步错），不是个别外点。
被剔的帧号记在 meta 的 `views_dropped`，剔除前后的残差也都在 meta 里。

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
| f | 1400.0 px | 1400.093 px | +0.007% |
| baseline | 80.0 mm | 80.001 mm | +0.001% |
| stereo RMS | — | 0.0352 px | — |
| 体检 `tools.check_calib` | — | 20/20 可用、9/9 格、跨度 1.72x、最大占宽 53% | PASS |

偏差在这个量级就说明链路本身没问题：真机标定如果差 5% 以上，问题在**图、板、装法**，
不在代码。

生成器要让板**占画幅宽 35%–68%**（源头的远近范围），倾斜还会再缩 ~10%，
实测投射后是 389..672 px（跨度 1.72x、最大占宽 53%），所以合成数据也能过 §4 的
体检（实测 9/9 格、20/20 可用）。位姿中心轮流铺到**画面中间 50% 的 3×3 带**里 ——
板已经占半个画幅，中心再往边上挪板就出框了；边格的覆盖靠「板角伸出去」而不是中心落在边格。
距离范围按分辨率/焦距自动算，不用手调。

两点注意：

- 这一步会跑 `findChessboardCornersSB`，**只在 x86 / Mac 上跑**（Pi 上会卡住，见 §5）；
  测试 `tests/test_synth_calib_views.py` 因此在 aarch64 上默认 skip（`RUN_CALIB_IT=1` 可强制）。
- 2026-11-28 第一版生成器写错过两处（棋盘纹理画成 11×9 格却按 10×7 的高宽贴、右目平移
  方向取反），结果标定回来 f 偏 +5%、baseline 偏 +11%、RMS 0.87 px —— **看起来和真实的
  标定问题一模一样**。教训：合成器要给真值、并且自己先被验证过，才能拿来当"链路没问题"
  的证据。

### 5.2 v4 侧躺/竖装：整批转 90° 再标定（2026-11-28）

```bash
# 采集就带上角度：会写进 capture.json 的 rotation_deg；落盘帧仍是原始方向
.venv/bin/python -m pi.tools.capture_calib --out data/recordings/calib_board \
    --target 20 --min-cells 8 --min-fill 0.35 --rotate 90
# 体检：--rotate 不传就从 <root>/capture.json 读
python3 -m tools.check_calib --root ../raspberry-pi/data/recordings/calib_board
# 标定：同上自动读角度；也可以显式 --rotate 90
python3 -m pc.calib.cli calibrate --replay-root ../raspberry-pi/data/recordings/calib_board \
    --pattern 9x6 --square-mm 30 --min-views 15 --out data/calib/stereo_calib.npz
```

- **90 = 顺时针 90°**，与 `cv2.ROTATE_90_CLOCKWISE` 一致。**270 会把左右相机对调**（`T` 的 x 分量变号、
  视差变负、点云跑到背面），视差符号不对先查这个。
- 转 90° 后 `image_size` 必须**宽高互换**（1640×1232 → 1232×1640）；npz 里存 `image_size=(1232,1640)`
  与 `rotation_deg=90`。`pc/pipeline.py` 在 `process()` 开头按这个角度转帧，构造时也按转正后的尺寸校验分辨率。
- 实测（2026-11-28，20 对旧数据，手工给 `capture.json` 补 `"rotation_deg": 90`）：
  20/20 角点 OK、自动剔除 2 个极线残差异常视图（原序号 [18,19]）、
  `baseline=82.790 mm f=1339.184 px RMS=2.629 px`、校正后残差中位 2.11 px / p90 5.73 px；
  SGBM 中位视差 **+118 px**（正号 = 左相机在左、右相机在右），对应深度 0.94 m，与板距 0.6–0.8 m 同量级。
- 标完跑真实相机要**每帧都转**（pipeline 已自动做）。代价是慢：Pi 上 1640×1232 的 SGBM 约 **1.7 s/帧**，
  要实时得降到 640×480 或换 x86 主机跑。

### 5.3 验收：拿到 npz 之后怎么判断它是对的（2026-11-28 实测）

标定器自己会报 RMS 和校正后极线残差，但**别只看它自报**。下面三步是本机跑通过的最小验收：

```bash
# 1) 体检（auto 检测器；会从 <root>/capture.json 读 rotation_deg）
python3 -m tools.check_calib --root ../raspberry-pi/data/recordings/calib_board2 --out runs/calib_check2.json --montage runs/calib_coverage2.png
# 期望：20/20 可用、9/9 格、最大占宽 >= 45%、无 warning

# 2) 标定（Mac 上几秒）
python3 -m pc.calib.cli calibrate --replay-root ../raspberry-pi/data/recordings/calib_board2 --pattern 9x6 --square-mm 30 --min-views 15 --out data/calib/stereo_calib.npz

# 3) 打印 npz 摘要，核对这几个数
python3 -m pc.calib.cli inspect --calib data/calib/stereo_calib.npz
```
2026-11-28 这一批（`calib_board2`，v4 + `--rotate 90`）的实测值：

| 项 | 值 | 怎么判 |
|---|---|---|
| 体检 | 20/20 可用、9/9 格、198..574 px（2.89x）、最大占宽 59% | 全过；少于 8 格或小于 35% 占宽就别标 |
| RMS | **0.634 px** | 小于 1 px 算好；大于 1.5 px 先查糊 / 板不平 |
| baseline | **80.196 mm** | 与卡尺量的 80 mm 差小于 1% |
| 焦距 P1[0,0] | **1230.9 px** | 与 K1[0,0]（1185.3）差 3.8% 正常（alpha=0 裁剪），差 15% 以上要警觉 |
| 校正后极线残差 | 中位 **0.50**、p90 **1.17 px** | 阈值 MAX_EPIPOLAR_DY_PX = 5，超了标定器直接报错不写 npz |

**活帧复核**（Pi 上抓一对真实场景，用 StereoPipeline(cfg, npz) 跑一遍）：

- 校正图上再算一次 SIFT 匹配的 dy：这次 174 个好匹配、**|dy| 中位 1.26 px**；
- 用视差反推距离对一下画面：50.4 px 得 Z = f*B/d = 1.96 m，深度图中位 1.91 m、p10–p90 1.74–2.12 m，和衣柜 / 椅子距离一致；
- 覆盖只有 3.3% 是白墙低纹理，不是标定问题；换有纹理的场景会高得多。

**坑（2026-11-28 踩过）**：在第二台机器上跑体检 / 标定前，先确认那台机器的仓库副本是**当前代码**。那天 Mac 上是旧副本，check_calib 报 18/20、8/9 格、44%，同步仓库后同一批数据变成 20/20、9/9、59%。结论不同不是因为数据变了，而是检测器的降采样回退没在那份旧代码里。
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
.venv/bin/python -m pi.tools.capture_calib --out data/recordings/calib_board --target 20 --min-cells 8 --min-fill 0.35 \
    --rotate 90          # v4 支架（基线在画面里竖直）必须带；换成 fpc_down 版就不带

# Pi：体检（看得懂就往下走）
cd ~/桌面/深度/macos-pc
python3 -m tools.check_calib --root ../raspberry-pi/data/recordings/calib_board --montage runs/calib_coverage.png

# x86：拷过去再体检一次，然后标定
# 产物落到 data/calib/stereo_calib.npz，被 pc.receive.live / ros2 节点用 --calib 指过去
```
