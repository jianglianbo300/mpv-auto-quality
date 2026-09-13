# mpv-auto-quality

弱显卡笔记本上播放 **4K 杜比视界（HEVC 10bit）** 的 mpv 配置 + 自动画质档脚本。

核心是 [`scripts/auto-quality.lua`](scripts/auto-quality.lua)：**按分辨率定基线 + 按实际丢帧率闭环升降档**。卡了它自己降档，不卡了它自己回升，全程不用手动干预。

> **English TL;DR** — An mpv config plus a Lua script that auto-switches quality tiers using a resolution-derived baseline and a real frame-drop feedback loop. Built for 4K Dolby Vision playback on weak laptop GPUs (Intel UHD 620 + NVIDIA MX250). Every tunable is a plain constant at the top of the script.

---

## 1. 目标场景与硬件基线

| 项 | 值 |
|---|---|
| CPU | i7-8550U（4 核 8 线程，低压） |
| 核显 | Intel UHD 620 —— **驱动着 1080p 屏** |
| 独显 | NVIDIA GeForce MX250 2GB（Pascal） |
| 屏幕 | 1920x1080 **SDR** |
| mpv | v0.41.0-244，libplacebo v7.360 |
| 系统 | Windows 10/11 |

要解决的问题：**4K 杜比视界 HEVC 片源，在这台机器上既要流畅，颜色也要对。**

两个约束直接打架：

- **画质优先** → `scale`/`cscale` 拉满 `ewa_lanczossharp`、开 `deband`
- **流畅优先** → 4K 降采样到 1080p 时缩放器是负载大头：调它能把吞吐从 20.4 提到 33.1 fps（+62%），而换显卡只有 ±3%

静态配置解决不了这个矛盾：1080p 片源希望满血，4K 片源希望轻量，而同一台机器两种都要播。所以才有这个脚本。

---

## 2. 快速开始

```bash
git clone https://github.com/jianglianbo300/mpv-auto-quality.git
cd mpv-auto-quality
```

**Windows**（配置目录是 `%APPDATA%\mpv\`，**不是** `~/.config/mpv/`）：

```powershell
$dst = "$env:APPDATA\mpv"
New-Item -ItemType Directory -Force -Path "$dst\scripts" | Out-Null
Copy-Item mpv.conf, input.conf $dst -Force
Copy-Item scripts\auto-quality.lua "$dst\scripts" -Force
```

**Linux / macOS**：

```bash
mkdir -p ~/.config/mpv/scripts
cp mpv.conf input.conf ~/.config/mpv/
cp scripts/auto-quality.lua ~/.config/mpv/scripts/
```

先别急着整套照抄——第 3 节的色彩链是**针对 1080p SDR 屏**调过的，换屏要重调。

**热键**

| 键 | 作用 |
|---|---|
| `Ctrl+a` | 自动画质档总开关 |
| `Ctrl+d` | 手动切 deband（会自动关掉自动档，避免被覆盖） |
| `Ctrl+i` | 手动切插帧（同上） |
| `Ctrl+t` | 色调映射曲线三档循环：hable / bt.2390 / spline |

**诊断**（可选，但强烈建议 —— 否则闭环到底有没有动，你永远不会知道）

见第 7 节。最小配置就是 `mpv.conf` 末尾两行 `log-file` + `input-ipc-server`。
> Linux / macOS 的 IPC 路径写成 `input-ipc-server=/tmp/mpv-ipc`。
>
> ⚠️ 管道名只能被一个实例占用，`log-file` 是覆盖写 —— 用之前先读 7.3 的两条硬纪律。

---

## 3. 色彩链：HDR/DV → SDR 的取舍

这台机器是 **1080p SDR 屏**，所以 HDR/DV 片源必须做 tone mapping。下面每一条都注明了依据。

### 3.1 真正决定观感的三个旋钮

```ini
target-peak=250          # 显示器峰值亮度（SDR 曲线下默认 auto = 203 nit）
hdr-compute-peak=no      # 关掉逐帧片源峰值计算
# tone-mapping=...       # 留空 = auto，gpu-next 下解析为 spline
```

**关键区分**（这两条经常被混为一谈）：

| 选项 | 管的是 | 说明 |
|---|---|---|
| `hdr-compute-peak` | **片源**峰值 | 逐帧统计画面亮度直方图，做动态逐场景映射 |
| `target-peak` | **显示器**峰值 | 告诉 mpv 屏幕能到多少 nit，限制输出信号上限 |

**它们不会互相覆盖。** 源码位置：`vo_gpu_next.c` 的 `apply_target_options()` —— `target-peak` 写 `target->color.hdr.max_luma`，`hdr-compute-peak` 走 libplacebo 的源侧峰值检测，两条路径独立。

一个容易踩的点：`--hdr-reference-white` **默认是 `auto`（内部值 0）**，所以它**不会**悄悄覆盖你设的 `target-peak`。只有当它被显式赋值时，才会在 SDR 目标下顶掉 `target-peak`（见 `vo_gpu_next.c` 第 885-888 行的 `if (opts->hdr_reference_white && ...)`）。

> 官方文档还提到：SDR 目标下把 `target-peak` 抬到 203 以上，等于把 SDR 屏当成"HDR 显示器伪装"用，此时**建议配 `tone-mapping=mobius` 或 `clip`**。本仓库默认留空（用 `spline`），因为一次只改一个变量才好归因；如果高光变得生硬发白，把 `mpv.conf` 里那行 `tone-mapping=mobius` 取消注释。

### 3.2 这两个选项在 SDR 屏上是空转

```ini
# d3d11-output-csp=srgb
# target-colorspace-hint=yes
```

它们看起来很关键（很多教程这么写），但在 **1080p SDR 屏**上实测**等价于默认值 `auto`**，所以本仓库把它们注释掉了。

依据（mpv v0.41.0 源码）：

- `d3d11_helpers.c` 的 `d3d11_get_mp_csp()` 把 `DXGI_COLOR_SPACE_RGB_FULL_G22_NONE_P709` 映射成 `{transfer=UNKNOWN, primaries=UNKNOWN}` —— 注释里明说这是为了"保持 SDR 传输函数的默认流程"。
- 而 `d3d11-output-csp=auto`（默认）探测到的桌面色彩空间在 SDR 桌面下**同样是** `G22/P709`。
- 两者进的是同一条代码路径。**结果完全一致。**
- `target-colorspace-hint` 默认值是 `auto`，文档说"仅在显示器参数已知时设置"——SDR 屏的参数是可读的，所以 `auto` 已经会设置，写 `yes` 没有增量。

**那"偏紫/发灰"到底是谁修的？** 是上面那三个旋钮 + `vo=gpu-next`。色调映射的目标色彩空间来自**显示器上报值**（`d3d11_target_color_space()` 读 `DXGI_OUTPUT_DESC1`），不是由这两行决定的。

### 3.3 关于 `video-sync=display-resample`

```ini
video-sync=display-resample
```

它解决的是 **A/V 漂移和偶发丢帧**（音频重采样去补偿视频时钟），但**不能消除 24p 在 60Hz 屏上的 3:2 抖动**。

官方文档原文（`--video-sync-max-video-change` 一节）：

> Playing 24 fps video on a 60 Hz screen will play video in a 2-3-2-3-... pattern.

2-3-2-3 就是 3:2 pulldown 本身。真要消除它只有两条路：开 `interpolation=yes`（时间轴插值），或者把屏幕切成 48/120Hz。本仓库选择关插帧，理由见第 4 节。

---

## 4. 自动画质档：三档状态机

脚本位置：`scripts/auto-quality.lua`。**单写者**——所有档位属性只由它一个脚本写。

### 4.1 为什么必须单写者

mpv 按**文件名顺序**加载脚本，后加载者赢。曾经有两个脚本（一个按分辨率切档、一个按丢帧切档）同时写 `scale`/`cscale` 这批属性，结果 4K 片打开时轻量档被另一个脚本覆盖回满血，直接吃满丢帧。

**画质档必须单写者。** 这也是为什么 `Ctrl+d`/`Ctrl+i` 这两个手动键被收进脚本而不是留在 `input.conf`：它们改的正是脚本托管的那批属性，留在外面会被下一次换档静默覆盖。现在按键会先自动关掉自动档。

### 4.2 三档

托管键集合 `KEYS` = `scale` / `cscale` / `dscale` / `linear-downscaling` / `deband` / `interpolation`。**每档必须显式列全每个键**，否则降过应急档再回升时，没列出的键会永久停在降级值。

| 档 | 触发 | 内容 | 依据 |
|---|---|---|---|
| `full` 满血 | `width < 3800` 基线 | `scale`+`cscale`=`ewa_lanczossharp`、`deband=yes` | 1080p→1080p 屏是**放大**，`ewa_lanczossharp` 是收益项且成本可承受 |
| `light` 4K轻量 | `width >= 3800` 基线 | `cscale=bilinear`、`deband=no`，保留 `dscale`+`linear-downscaling` | 4K→1080p 是**降采样**，`scale` 根本不参与；`cscale`/`deband` 才是负载大头且画质敏感度低 |
| `emergency` 应急 | 基线档持续丢帧 | `scale`/`cscale`=`bilinear`、**`dscale=oversample`**、`linear-downscaling=no`、`deband=no` | 牺牲画质换"能动"。兜底档 |

> ⚠ **应急档的 `dscale` 用 `oversample` 而不是 `bilinear`** —— 实测 4K DV 降采样时 `oversample` 更快（23.4 fps vs 21.3 fps），且它"采样所有源像素"，避免降采样混叠。
> ⚠ **这些值必须与当前 mpv 版本对照校验**：应急档原先写的是 `scale="fast_bilinear"`，那是**旧版 mpv 的值，v0.41 已彻底移除** —— 于是每次降档该键都静默设置失败，**降档形同虚设**。v5.8 已修正并加了启动自检（见坑⑯）。

### 4.3 闭环参数

全部是脚本顶部的常量：

```lua
local SAMPLE_INTERVAL  = 3      -- 秒: 丢帧采样周期
local DROP_THRESHOLD   = 1.0    -- 平均每秒丢 >=1 帧算卡
local CONFIRM          = 2      -- 连续 N 周期超阈值才降档(防瞬时毛刺)
local CLEAN_SECONDS    = 60     -- 应急档连续 N 秒零丢帧才试探回升
local RECHECK_SECONDS  = 25     -- 回升后观察 N 秒, 立刻复发记一次失败
local COOLDOWN_SECONDS = 300    -- 连续 2 次回升失败 => 冷却 N 秒不再回升
local SETTLE_SECONDS   = 10     -- v5.7: 换档/起播后的静默期(见 5.2)
local WIDTH_4K         = 3800   -- 4K 判定阈值
```

- **降档**：每 3s 读一次 `frame-drop-count` 算速率，> 1 帧/s 连续 2 个周期 → 降 `emergency`
- **回升**：应急档连续 60s 零丢帧 → 试探回基线；回升后 25s 内复发记 1 次失败；连败 2 次 → 冷却 300s（防临界点反复横跳闪屏）
- **静默期**（v5.7）：换档/起播后 10s 内只记录采样、不参与判定 —— 因为换档本身会触发 shader 重编译
- **逃生口**：`Ctrl+a` 关掉自动档

`DROP_THRESHOLD` / `CONFIRM` / `CLEAN_SECONDS` 仍是经验值（真实触发值 2.0~6.6 帧/s，见 5.2）；`SETTLE_SECONDS` 有实测依据（换档到计数器稳定约 9s）。

### 4.4 独显实测：换它救不了 4K DV（附一版被推翻的结论）

本机是 Optimus 混合显卡笔记本：i7-8550U + **Intel UHD 620**（核显，接 1080p 显示器）+ **NVIDIA MX250**（2GB）。

**先看 mpv 到底用了哪块 GPU** —— 日志写得明明白白：

```
[0.879][v][vo/gpu-next/d3d11] Device Name: Intel(R) UHD Graphics 620
[0.879][v][vo/gpu-next/d3d11] Device ID: 8086:5917 (rev 07)
```

`8086` 是 Intel 的 PCI 厂商 ID。**解码（`d3d11va`）和渲染（`gpu-next`）全在核显上，MX250 一点没参与。**
（mpv 能看到它：`mpv --d3d11-adapter=help` 列出 `Adapter 1: vendor: 4318, description: NVIDIA GeForce MX250`，`4318` = `0x10DE`。）

**那强制用 MX250 会更快吗？** —— 这里有一版**被推翻的结论**，原样保留记录。

初测（单次，4K DV 300 帧）显示"独显更慢"：

| 配置 | 4K DV 300 帧 | 当时的判定 |
|---|---|---|
| 默认（核显） | **20.4 fps** | `d3d11va` ✓ |
| `--d3d11-adapter=NVIDIA` | 19.0 fps | 判为"硬解初始化失败、回退软解" |

**这个结论是错的。** 后来做多次交替重测（各 3 次取中位数），方向完全反转：

| 场景 | 核显 | 独显 | 差异 |
|---|---|---|---|
| 本配置 4K DV（200 帧） | 17.8 fps | 18.3 fps | +2.9% |
| 本配置 + `--hwdec=nvdec`（**完整独显方案**） | 17.8 fps | **17.9 fps** | **+0.4%** |
| `--dscale=bilinear`（轻 GPU 负载） | 13.7 fps | 15.0 fps | +9.5% |
| `--dscale=ewa_lanczos4sharpest`（重 GPU 负载） | 11.4 fps | 14.0 fps | **+22.8%** |

**错在哪：**

1. **单次测量** —— 本机桌面 GPU 被 Electron/服务抢占，同配置方差可达数倍，±3% 的差异完全在噪声内。
2. **首次 shader 编译被当成稳态** —— 第一次用某个缩放器时 libplacebo 要现场编译（`ewa_lanczos4sharpest` 首跑 21.2 秒 vs 复跑 7.1 秒，差 3 倍）。拿首跑数据做对比是错的。
3. **"初始化失败"是误判** —— 第二次测量日志确认硬解**真的跑起来了**（`Loading hwdec driver 'cuda'` + `Device Name: NVIDIA GeForce MX250`）。

**修正后的结论：MX250 并不弱，负载越重优势越明显（+23%）。但换它救不了 4K DV** —— 在你的实际配置下（light 档）两者**几乎完全相同**（17.9 vs 17.8，差 0.4%）。这说明**瓶颈不在 GPU 渲染算力**，而在 4K DV 这条链路的其它环节（解码吞吐 / 内存带宽 / 色调映射）。

> **教训：单次基准测试不足以支撑结论，尤其是"某个方向没用"这类否定性结论。**
> 否定性结论必须满足三条：① 重复测量取中位；② 排除缓存/预热效应；③ 确认被测对象**真的切换成功了**。

**本机现状（2026-09-13 起）**：已在 `mpv.conf` 中设置 `d3d11-adapter="NVIDIA GeForce MX250"` —— 实测日志确认 `Device Name: NVIDIA GeForce MX250`，吞吐 18.3 fps（核显 17.8，+2.9%）。想切回核显，注释掉该行即可。

**真正的瓶颈是「4K 本身」，不是「哪块 GPU」：**

| 片源 | 渲染吞吐 |
|---|---|
| 1080p H.264 | **197.9 fps** |
| 4K DV HEVC | 20.4 fps |

同一条管线、同一块核显，1080p 能跑 **198 fps** —— 说明 mpv、驱动、输出链路全都没毛病，纯粹是 4K 的处理量压垮了这台弱机。

**所以：降渲染负载比换 GPU 有用得多** —— 前者 **+62%**，后者 **±3%**（有时甚至为零，见 4.4 节修正数据）。

| 配置（4K DV 300 帧） | fps | vs 基线 |
|---|---|---|
| 默认（`dscale=mitchell`） | 20.4 | — |
| `dscale=oversample` | 23.4 | +15% |
| `scale`/`cscale`/`dscale` 全降 bilinear | 27.9 | +37% |
| **`scale=oversample` + `cscale=bilinear` + `dscale=oversample`** | **33.1** | **+62%** |
| 再加 `tone-mapping=clip` | 33.0 | 无增益 |

**33.1 fps > 片源 24 fps → 能流畅播。** 应急档的方向是对的 —— 前提是那些值真的设得进去（见坑⑯）。

**色彩链脚本永不触碰**——卡的时候只砍几何与后期，`tone-mapping`/`target-peak`/`hdr-compute-peak` 一个都不动。

---

## 5. 实测证据

分 [实证]（跑过、看过输出）和 [未实证]（推断、未跑）。**这个区分很重要，请照此对待。**

| 结论 | 强度 |
|---|---|
| 真 4K 片（3840）→ 日志 `AUTO-QUALITY -> light (新片源 width=3840)` | [实证] |
| 720p 片 → 日志 `AUTO-QUALITY -> full (新片源 width=720)` | [实证] |
| 脚本加载零 Lua 错误 | [实证] |
| 硬解在工作：播 2160p DV 时 mpv 进程 CPU ≈ 19-20% 单核（软解会 400%+） | [实证] |
| `frame-drop-count` 等属性名在本 build 存在，`read_drops()` 回退链成立 | [实证] |
| `--framedrop` 默认值已是 `vo`（本 build），所以 `frame-drop-count` 有数 | [实证] |
| display-sync 模式下丢帧计数仍有效（`vo.c` 里 `num_vsyncs < 1` 这条路径） | [实证] |
| `d3d11-output-csp=srgb` 与默认 `auto` 生效值等价 | [实证] |
| `hdr-reference-white` 生效值为 `auto`，不覆盖 `target-peak=250` | [实证] |
| **降档闭环真跑过**：加压后 `full → emergency`，日志 `AUTO-QUALITY -> emergency (丢帧159.9/s)` | **[实证]** |
| **回升闭环真跑过**：卸压后 `emergency → full`，日志 `AUTO-QUALITY -> full (自动回升)` | **[实证]** |
| 降档时机符合设计：连续 2 个采样周期（≈6~9s）超阈值才降 | [实证] |
| 回升时机符合设计：卸压后约 66s 回升（`CLEAN_SECONDS=60` + 采样粒度） | [实证] |
| `mpv --log-file` 恒定按 debug 级写，`--msg-level` 管不着 | [实证] |
| IPC 可用：能读 `frame-drop-count` / 托管键 / `user-data/auto-quality/*` | [实证] |
| **真实播放**（720p/1.4Mbps，非构造负载）下 `full → emergency` 触发 3 次 | **[实证]** |
| **真实播放**下 `emergency → full` 回升触发 2 次 | **[实证]** |
| `COOLDOWN_SECONDS=300` 冷却路径真被触发：连续 2 次回升失败后停止回升 | **[实证]** |
| 换档会触发 libplacebo 重编译 shader，最慢单次 **1199.7 ms**，编译期丢帧被计入判定 | **[实证]** |
| 阈值 `1.0 帧/s` 在真实片源上「能触发」，但**是否该触发第一次（2.0 帧/s）存疑** | **[未实证]** |
| 1440p / 2.5K 落 `full` 档是否合理 | [未实证] |
| 720p 轻片源在 `full` 档断续丢帧（0.66~4.67 帧/s）的真实原因 | [未实证] |
| mpv **默认用核显**渲染，MX250 全程闲置（`Device Name: Intel(R) UHD Graphics 620`） | [实证] |
| 强制用 MX250（`--d3d11-adapter` + `--hwdec=nvdec`，CUDA 硬解）后，4K DV 吞吐**与核显基本相同**（17.9 vs 17.8 fps，差 0.4%） | **[实证·3次取中位]** |
| MX250 在**纯 GPU 负载**下确实更强（`dscale=ewa_lanczos4sharpest`：14.0 vs 11.4 fps，**+23%**）→ 说明瓶颈不在 GPU 渲染算力 | **[实证·3次取中位]** |
| ~~强制用 MX250 渲染并不更快（19.0 vs 核显 20.4 fps），且 D3D11 硬解初始化失败~~ → **已被推翻**：单次测量 + 首次 shader 编译造成的假象 | ~~[实证]~~ **已修正 2026-09-13** |
| 同一管线 1080p 能跑 **197.9 fps**、4K DV 只有 20.4 fps → 瓶颈是 4K 本身而非 GPU 型号 | [实证] |
| 应急档缩放器组合（`oversample`+`bilinear`）能到 **33.1 fps**，超过片源 24 fps | [实证] |
| 应急档原先的 `scale="fast_bilinear"` 是**非法值** → 降档静默失效（v5.8 已修 + 加自检） | [实证] |
| `tone-mapping=clip` 对 4K DV 吞吐**无增益**（33.0 vs 33.1 fps） | [实证] |
| `option-info/<名>/choices` 可作为选项合法性的运行时校验源 | [实证] |

### 5.1 闭环验证是怎么做出来的

`tools/loop_test.py` —— 之前测不了，是因为「没法在不打扰观看的前提下构造持续丢帧」。解法是用 IPC 把负载**做成可控的**：

```
基线(full) → set speed 8 → 丢帧 → 降档(emergency) → set speed 1 → 干净 → 回升(full)
```

实测一轮（1080p H.264，`--vo=null` 无窗口）：

| 阶段 | 观测 |
|---|---|
| 基线 | `tier=full`，`frame-drop-count=0` |
| 加压 `speed=8` | t=3s 丢 267 帧；t=6s 丢 754 帧（仍 full，bad_streak=1）；**t=9s → emergency** |
| 加压结束 | 累计丢 1721 帧，档位 emergency |
| 卸压 `speed=1` | 丢帧停在 2150 不再增长；**t≈66s → full（自动回升）** |

**降档和回升都按设计触发。** 这是本策略第一次拿到端到端实证。

⚠ **但这不等于阈值标定完成。** 人工 `speed=8` 压出来的丢帧率是 **137~160 帧/s**，比 `DROP_THRESHOLD=1.0` 高两个数量级 —— 只证明了「远超阈值时会降、干净够久会升」，**没有回答「1.0 帧/s 这个阈值在真实卡顿下合不合适」**。那个要靠真实播放的日志（见下节）。

### 5.2 真实播放实证：闭环跑通了，但它会「自己把自己绊倒」

上面 5.1 是人为构造的负载。**真实负载**下的第一次完整实证来自一份 3 分 25 秒的播放日志（720p / h264 / 1.4 Mbps，用户实际观看）：

```
0:00  -> full        新片源 width=1280
0:33  -> emergency   丢帧2.0/s        ← 降档（真实触发）
1:40  -> full        自动回升          ← 回升
1:42  -> emergency   回升后复发        ← 仅隔 2 秒
2:42  -> full        自动回升
2:45  -> emergency   回升后复发
（此后不再回升 —— 冷却机制生效）
```

**好消息**：降档 / 回升 / 冷却三条路径全部在真实场景中触发，`COOLDOWN_SECONDS` 第一次被真正验证。

**坏消息（本轮最重要发现）**：把档位变更和 shader 编译日志对齐后，因果链非常清楚 ——

| 时间 | 事件 |
|---|---|
| `33.833` | `AUTO-QUALITY -> emergency (丢帧2.0/s)` ← 换档 |
| `35.345` | `Spent 1199.704 ms translating HLSL to DXBC (slow!)` ← **1.2 秒停滞** |
| `36.603` | `Spent 208.870 ms translating GLSL to SPIR-V (slow!)` |
| `37.491` | `Spent 876.862 ms translating HLSL to DXBC (slow!)` |
| `36.649` / `39.684` | 采样 `drops=42` / `drops=92` ← **全是编译期丢的** |
| `42.667` | 采样 `drops=0`（`-30.54/s`）← 编译完成、计数器归零 |

**降档后那 8 秒里丢的 134 帧，与「片源喂不动」毫无关系，全是换档引发的 shader 重编译。**
（`--gpu-shader-cache` 默认已是 `yes` —— 同 shader 重跑只需 8.7ms；但换档产生的**新** shader 变体是首次编译，缓存无从命中。）
而脚本把这些丢帧当成「还在卡」，于是 `full → emergency` 之后又被自己的编译尖峰推着走：

```
emergency 稳定 60s（丢帧全 0）
  → CLEAN_SECONDS 满足 → 回升 full
  → full 档重型 shader（ewa_lanczossharp + deband）重新编译
  → 编译期丢帧 → 立刻越过阈值 → 2~3 秒内又降回 emergency
  → 循环
```

**两个设计缺陷**（都是「换档」这个动作本身的副作用）：

- **A. 换档成本未计入** —— 换档触发 shader 重编译（实测最慢 1.2s），编译期丢帧被算进 `bad_streak` / `clean_accum` → 降档后看起来更卡、回升后立刻复发。
- **B. 对外部负载无免疫** —— `DROP_THRESHOLD=1.0/s` 偏低，系统瞬时负载造成的轻微丢帧也会触发降档。本次前 15 秒零丢帧、15s 后断续丢帧（无任何 seek/pause 事件），怀疑与外部系统负载有关（[未实证]）。

**修复方向**：

- **① 换档/起播静默期 —— ✅ 已实施（v5.7）**：新增 `SETTLE_SECONDS = 10`，静默期内只记录采样、不参与判定。依据是「换档 → 编译 → 计数器归零」实测约需 9 秒。回归测试：降档 ✅ / 回升 ✅，且换档后那波高丢帧（rate 6.66~10.67/s）被正确跳过 —— **振荡的燃料被拿掉了**。
- **② 阈值改双条件**（`rate > 3.0` 且本窗口绝对丢帧 ≥10）—— 待定，取决于用户对「2.0 帧/s 该不该触发」的判断。
- **③ `CLEAN_SECONDS` 60 → 120** 让升档更保守 —— 待定。
- ~~④ 给 libplacebo 配 `gpu-shader-cache`~~ —— **此路不通**：实测 `--gpu-shader-cache` 默认就是 `yes`（日志里 33.671s 那批只花 8.7ms 就是缓存命中）。缓存救不了「换档必然引入新 shader 变体」这件事，因为那个变体是**首次编译**。

⚠ **静默期尚未在真实渲染路径上复验** —— 上面的回归用 `--vo=null`，绕过了 shader 编译。下次真实播放时用 `logcheck.py` 确认「换档后不再出现 `回升后复发`」即可结案。

**同一份日志还暴露了第二个问题，比振荡更隐蔽**：那三次降档**全都失败了** —— 应急档写的 `scale="fast_bilinear"` 是 mpv v0.41 已移除的非法值，于是最重的 `scale` 根本没被换掉。**降档降了个寂寞**，这正是「降了还是卡」的直接原因。已修复并加了启动自检，详见坑⑯。

完整分析见 [`_evidence/2026-09-13-真实播放闭环分析.md`](_evidence/2026-09-13-真实播放闭环分析.md)（附日志快照与证据行定位）。

---

## 6. 踩过的坑

① **`profile-cond` 和 Lua 读 `video-params/*` 全失效** —— mpv 0.41 + `d3d11va` 下必须用别名 `width`。按分辨率切档的 profile 路线死在这里。

② **`file-loaded` 时 `width` 可能仍为 0** → 4K 会被误判成满血档。必须 `file-loaded` + `video-reconfig` + `observe_property("width")` **三路触发**兜底。

③ **`goto` 是 LuaJIT 保留字** —— 切档函数叫 `goto` 直接语法错误，脚本**静默不加载**，只报一行错。

④ **`mp.set_property` 成功返回 `nil`、失败抛错** —— 写成 `if not mp.set_property(...) then warn` 会对每个键误报警。必须 `pcall`。

⑤ **跨档属性不回滚** —— 某档表少写一个键，降过应急档再回升时该键永久停在降级值。所以每档必须列全 `KEYS`。

⑥ **两个脚本抢同一批属性 = 未定义行为**（见 4.1）。

⑦ **性能数字方差极大** —— 桌面 GPU 被 Electron/后台服务抢占时，同配置丢帧数 32~216，方差 6 倍。`--vo=null` 会回退软解、headless 的 vsync 估计失真。**单次小样本不能当结论。**

⑧ **排查 mpv 键位别走弯路**：`input-bindings` 属性**只列 `input.conf` 与内置默认绑定**，看不到脚本运行期 `mp.add_key_binding` 注册的键；`keybind` 命令在 `--idle` 下也一律返回 `<unbound>`。唯一可靠办法是 `--msg-level=all=debug` 跑一次，grep 脚本名，看它注册时打的 `Run command: define-section, args=[name="input_<脚本名>", ...]`。

⑨ **读 mpv 实际生效的选项值**：`mpv --idle --vo=null --script=<dump.lua>`，脚本里用 `mp.get_property("options/<名>")` 逐个打印。注意 Lua 的 `print` 走 info 级，必须配 `--msg-level=all=info` 才看得到。

⑩ **`--log-file` 恒定按 debug 级写，`--msg-level` 管不着它** —— `--msg-level=all=no` 也照样写满 276 行。别在配置里写 `msg-level` 试图降噪，那是个无效开关（写进去只会误导下一个读配置的人）。

⑪ **`input-ipc-server` 先到先得 + `--log-file` 覆盖写 = 测试会劫持用户**（真实事故，不是理论风险）：测试实例若沿用默认管道名，会连上用户正在看的那个 mpv，然后对着用户的播放发命令；同时以覆盖模式打开用户的日志文件，把那一场的日志整段截断。**测试必须用独立管道名 + 独立日志路径**，且连上后核对 `filename` 是不是自己要放的片源。

⑫ **mpv 会在同一根 IPC 管道上主动推事件**（`start-file` / `playback-restart` / `end-file` / `property-change`）。朴素的「写一条、读一行」会把事件当成响应，请求与应答就此错位 —— 症状是读 `frame-drop-count` 却拿到 `bilinear`（上一条 `cscale` 的值）。**必须带 `request_id` 按 id 配对。**

⑬ **单写者脚本需要「漂移自愈」，光在分辨率变化时重新套用是不够的**：`pick_baseline()` 若只在「width 跨过 3800」时才动手，那么属性一旦被外部改乱（IPC 直接 `set`、别的脚本、mpv 内置的 `b` 键 `cycle deband`），就会**永久停在混合态** —— 实测踩到 `scale=ewa_lanczossharp` 配 `dscale=bilinear`、`linear-downscaling=no`，不属于任何一档。v5.6 起：基线未变但托管键与当前档位不符时，自动重新套用。

⑭ **档位别靠反推，让脚本自己报**：v5.6 起内部状态外露成 `user-data/auto-quality/{tier,baseline,auto}`，`mpvctl.py get` 直接读。反推在混合态下必然误判（见 ⑬）。

⑮ **「改画质属性」本身是有成本的，而且成本会被误判成「画质没救回来」**（真实播放踩到）：切换 `scale` / `cscale` / `dscale` 会让 libplacebo 重新编译 shader 链，实测单次最长 **1199.7 ms**，期间渲染停滞、`frame-drop-count` 暴涨（一次降档后 8 秒内丢 134 帧）。一个「丢帧 → 降档 → 观察丢帧」的闭环如果不给换档留静默期，就会**自己触发自己的降档条件**，形成 full ⇄ emergency 振荡。附带现象：换档重建 filter chain 会让 `frame-drop-count` **归零**，采样里出现 `-30.54/s` 这类负值 —— 负值虽然不会被判成 stutter，但它掩盖了编译期的真实丢帧。**任何「调参 → 观察效果」的闭环都要先扣除调参动作自身的开销。**

⑯ **档位表里的选项值必须与当前 mpv 版本对照校验 —— 一个非法值会让降档「静默失效」**（真实播放踩到，最隐蔽的一类 bug）：应急档原先写的是 `scale="fast_bilinear"`，那是**旧版 mpv 的缩放器名，v0.41 已彻底移除**（`--scale=fast_bilinear` 直接 `Fatal error`，新旧后端都不认）。后果：降档时 `mp.set_property("scale", "fast_bilinear")` **失败**，但——档位照样切了、OSD 照样提示了、`bad_streak` 照样清零了，**唯独最重的 `scale` 根本没换掉**。用户真实日志里三次降档全部记着同一行 `[f][cplayer] Invalid value for option scale: fast_bilinear`，而表象是「降了还是卡」。附带风险：`keys_match()` 会永远返回 false → 一旦 `pick_baseline` 被触发就反复「漂移自愈」，反复触发 shader 重编译。
　　防御（v5.8 已实施）：mpv 把每个选项的合法值暴露在**只读**属性 `option-info/<名>/choices` 上（`scale` 39 个、`cscale`/`dscale` 各 40 个；`deband`/`interpolation` 这类 flag 没有 choices，跳过）。启动时逐个比对档位表，不合法就 `mp.msg.error` 点名报出。**该属性随当前 VO 变化**，所以换回旧 `vo=gpu` 时自检会自动适配。
　　**普适推论：凡「把配置值写进代码再交给程序执行」的地方，都要有一步「程序认不认这个值」的校验 —— 否则错误会被执行层吞掉，只在行为上留下一个说不清的「没生效」。**

---

## 7. 诊断：怎么知道闭环有没有动

这是本仓库最容易被跳过、但实际最要紧的一节。

**mpv 默认不写日志文件，也不开放任何外部接口。** 所以「播了一场之后回头查闭环到底触发没有」在原始状态下根本做不到 —— 这个策略的降档/回升逻辑因此长期停在「未验证」。下面三样东西就是为了让它可查。

### 7.1 打开日志与 IPC

`mpv.conf` 末尾两行：

```ini
log-file="~~/mpv.log"                      # ~~/ = 配置目录；每次启动覆盖写
input-ipc-server="\\.\pipe\mpv-ipc"        # Windows；Linux/macOS 用 /tmp/mpv-ipc
```

⚠ **两个实测坑**：

| 坑 | 实测结论 |
|---|---|
| 想用 `msg-level` 降低日志详细度 | **无效**。日志文件恒定按 debug 级写 —— `--msg-level=all=no` 也照样写满 276 行。要精简只能用过滤脚本。 |
| 日志体积 | 启动有一波 275~385 行的固定突刺，稳态约 20~30 行/秒 → 一集 45 分钟约几 MB。覆盖写，不跨会话累积（连跑两次均 275 行）。 |

脚本报档位走 `mp.msg.warn`，日志里的模块前缀是 `[auto_quality]`。

### 7.2 三个工具（`tools/`）

| 工具 | 干什么 |
|---|---|
| `mpvctl.py` | 通过 IPC 读运行中实例的实时状态：片源 / 分辨率 / 硬解 / 丢帧 / **脚本自报档位** / 6 个托管键。也能 `set`、发任意命令 |
| `logcheck.py` | 从几万行 debug 噪声里提取闭环证据：档位变更时间线、丢帧率分布直方图、闭环触发判定 |
| `loop_test.py` | **闭环端到端验证台**：用 IPC 控制 `speed` 可控地制造/撤除负载，把「基线 → 加压 → 降档 → 卸压 → 回升」四阶段全跑一遍 |

```bash
python tools/mpvctl.py status                  # 对着正在播的片子看一眼
python tools/logcheck.py                       # 分析上一场的日志
python tools/loop_test.py --video test.mp4     # 主动把闭环跑一遍
```

### 7.3 ⚠ 用 IPC 的两条硬纪律（都是踩出来的）

**一、测试必须用独立管道名 + 独立日志路径。**

`input-ipc-server` 是**先到先得**的，`--log-file` 是**覆盖写**的。测试若沿用默认名字，而用户正在用同一个名字看片：

- 测试会**连上用户的实例**，然后对着用户的播放发 `set speed 8` —— 用户画面突然 8 倍速，测试拿到的也全是别人的数据；
- 测试实例启动时**以覆盖模式打开用户的日志文件**，把用户那一场的日志整段截断（日志里会留下 NUL 空洞）。

`loop_test.py` 现在默认用 `\\.\pipe\mpv-ipc-looptest` + 临时目录日志，并在连上后**核对 `filename` 是不是本次要放的片源**，不匹配直接中止退出。

**二、mpv 会在同一根管道上主动推事件。**

朴素的「写一条、读一行」会把 `start-file` / `playback-restart` 这类事件当成响应，请求与应答就此错位 —— 症状是读 `frame-drop-count` 却拿到 `bilinear`（那是上一条 `cscale` 的返回值）。必须给每条命令带 `request_id` 并按 id 配对；两个工具都实现了。

### 7.4 档位读取：别反推

`auto-quality.lua` v5.6 起把内部状态外露成属性，**直接读**：

```bash
python tools/mpvctl.py get user-data/auto-quality/tier    # full / light / emergency
python tools/mpvctl.py get user-data/auto-quality/auto    # 自动档还开着没
```

在此之前只能拿 6 个托管键的值**反推**档位 —— 遇到混合态会给出错误结论。实测踩到过：`scale=ewa_lanczossharp` 配 `dscale=bilinear`、`linear-downscaling=no`，**不属于任何一档**。

混合态的成因是 v5.6 之前的一个真实缺陷：`pick_baseline()` 只在「分辨率跨过 3800」时才重新套用档位，所以属性一旦被外部改乱（IPC 直接 `set` / 别的脚本 / mpv 内置的 `b` 键 `cycle deband`），就会永久停在那儿，直到下次分辨率变化。v5.6 加了**漂移自愈**：基线未变、但托管键与当前档位不符时，自动重新套用。

---

## 8. 已知未验证面（欢迎审计）

按优先级：

1. ~~**[最高] 闭环未验证**~~ → **✅ 已解决（见 5.1 + 5.2）**：`tools/loop_test.py` 用 IPC 控制 `speed` 构造可控负载双向跑通；随后一份真实播放日志（3 分 25 秒）确认降档 / 回升 / 冷却三条路径在真实负载下全部触发。
2. ~~**[最高] 振荡修复未实施**~~ → **✅ 已修复（v5.7 静默期，见 5.2）**，回归测试通过；**但尚未在真实 `gpu-next` 渲染路径上复验**（`--vo=null` 绕过 shader 编译）—— 下次真实播放看 `logcheck.py` 是否还出现「回升后复发」即可结案。
3. **[最高] 修好值之后，应急档在真实 4K DV 播放里到底能不能救场？** —— v5.8 把非法值修掉了（坑⑯），吞吐测试也从 20.4 提升到 33.1 fps（> 片源 24 fps）。但**吞吐达标 ≠ 体验达标**：真实播放还要叠上 `video-sync=display-resample`、present、跨 GPU 输出、以及外部系统负载。需要找一部 4K DV 完整播一场，用 `logcheck.py` 确认「降档后丢帧真的归零」。**这是当前最该做的一件事。**
4. **[高] 阈值仍未标定** —— 真实数据把范围收窄了：真实触发时的丢帧率是 **2.0 / 5.64 / 6.64 帧/s**，`DROP_THRESHOLD=1.0` 会抓住全部三次，但第一次（2.0/s）是否**该**触发存疑。还需在**已知流畅**的片子上跑一场，用 `logcheck.py` 看采样分布，确认不会把 24fps→60Hz 的正常抖动误判成卡。
5. **720p 轻片源为何在 `full` 档断续丢帧** —— 5.2 那份日志的前 15 秒零丢帧、之后断续丢帧且无任何 seek/pause 事件，怀疑与外部系统负载有关（[未实证]）。若成立，则 `DROP_THRESHOLD` 需要能区分「解码喂不动」与「系统被抢」。
6. **`frame-drop-count` 语义是否含容器可变帧率（VFR）导致的正常丢帧？** 若是，动画/VFR 片会被误判。
7. **1440p / 2.5K（2560-3799）落 `full` 档**，`cscale=ewa` 在降采样场景白跑。比 4K 轻 4 倍大概率无碍，但未测。
8. **有没有更对症的原生旋钮？** 比如 mpv 是否已有原生 ABR/动态降级机制，或 `--demuxer-max-back-bytes`、解码线程优先级。有原生就该替换这套手写闭环。
9. **档位是否该写进 `mpv.conf` 的 `profile` 而不是 Lua 逐属性 set？** 更可维护，但受限于坑①。
10. ~~`--vo=null` 下的验证能否代表真实渲染路径？~~ → **已部分回答**：5.2 用的是真实 `gpu-next` 渲染路径的日志，暴露了 `--vo=null` 完全测不到的 shader 编译成本（坑⑮）。结论：**人工负载验证机制，真实负载验证参数，两者不可互替。**

---

## License

MIT —— 见 [LICENSE](LICENSE)。随意取用、修改、再发布，不担保任何后果。

---

## 附：本仓库的来路

这套配置不是凭空写的。它是一次**配置漂移审计**的产物：

2026-09-06 一次配置文件重写把 `mpv.conf` 从 44 行截断到更短，静默丢掉了整段「使用体验」9 项、
`tone-mapping` 与 `target-peak`，而**注释仍在声称这些选项存在**。三代备份逐字节 diff 才发现。
修复方式不是"照注释补回去"，而是先 dump mpv 实际生效值 + 对官方 v0.41.0 文档和源码逐个核对，
把**空转选项**（写了等于没写）和**真实旋钮**区分开，再决定哪些恢复、哪些注释停用。

所以本仓库里的每一行都带出处：`[实证]` = 本机实测或源码/文档核对过；
`[未实证]` = 经验值，等你来推翻。审计待办列在第 8 节。

### 然后是「把未验证面做掉」

配置修完之后，最刺眼的是一句 `[未实证]`：**丢帧闭环从 v5 起就没跑过一次**。
原因很实在 —— mpv 默认不写日志、不开接口，而「构造持续丢帧」又会打扰实际观看。

解法不是去猜，而是**把观测面打开**：加 `log-file` + `input-ipc-server`（第 7 节），
再写三个工具。最后用 `loop_test.py` 通过 IPC 控制 `speed` 把负载做成可控的，
一轮跑完 `full → emergency → full`（5.1 节）。

同一轮里也踩了两个新坑并且都写进第 6 节：`--log-file` 恒定 debug 级（`msg-level` 无效），
以及 `input-ipc-server` 先到先得 + `--log-file` 覆盖写导致**测试劫持了正在播放的实例**（⑪）。
后者是真实事故，不是理论风险 —— 所以 `loop_test.py` 里现在有一道「核对片源」的安全闸。
