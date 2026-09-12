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
- **流畅优先** → 4K 降采样到 1080p 时缩放器是负载大头，MX250 喂不动

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
| `emergency` 应急 | 基线档持续丢帧 | 再砍 `linear-downscaling=no`，`scale`/`dscale` 降双线性 | 牺牲降采样画质换"能动"。兜底档 |

### 4.3 闭环参数

全部是脚本顶部的常量，**都是经验值，未经标定**：

```lua
local SAMPLE_INTERVAL  = 3      -- 秒: 丢帧采样周期
local DROP_THRESHOLD   = 1.0    -- 平均每秒丢 >=1 帧算卡
local CONFIRM          = 2      -- 连续 N 周期超阈值才降档(防瞬时毛刺)
local CLEAN_SECONDS    = 60     -- 应急档连续 N 秒零丢帧才试探回升
local RECHECK_SECONDS  = 25     -- 回升后观察 N 秒, 立刻复发记一次失败
local COOLDOWN_SECONDS = 300    -- 连续 2 次回升失败 => 冷却 N 秒不再回升
local WIDTH_4K         = 3800   -- 4K 判定阈值
```

- **降档**：每 3s 读一次 `frame-drop-count` 算速率，> 1 帧/s 连续 2 个周期 → 降 `emergency`
- **回升**：应急档连续 60s 零丢帧 → 试探回基线；回升后 25s 内复发记 1 次失败；连败 2 次 → 冷却 300s（防临界点反复横跳闪屏）
- **逃生口**：`Ctrl+a` 关掉自动档

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
| **降级/回升闭环从未真跑过一次** | **[未实证]** |
| 阈值 `1.0 帧/s` / `CONFIRM=2` / `60s` 全是拍的，未标定 | [未实证] |

闭环没跑过的原因很实在：**没法在不打扰实际观看的前提下构造"持续丢帧"场景。**

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

---

## 7. 已知未验证面（欢迎审计）

按优先级：

1. **[最高] 闭环未验证** —— 想办法构造可控丢帧（播放时并行压 GPU、或真卡顿时查日志有没有 `AUTO-QUALITY -> emergency`），确认降档真触发、回升真不横跳。
2. **[最高] 阈值未标定** —— 建议先实测"已知流畅"的片子上 `frame-drop-count` 基线值是多少，再定 `DROP_THRESHOLD`。6s 反应延迟对看片卡顿可能太慢；1 帧/s 也可能把 24fps→60Hz 的正常抖动误判成卡。
3. **`frame-drop-count` 语义是否含容器可变帧率（VFR）导致的正常丢帧？** 若是，动画/VFR 片会被误判。
4. **1440p / 2.5K（2560-3799）落 `full` 档**，`cscale=ewa` 在降采样场景白跑。比 4K 轻 4 倍大概率无碍，但未测。
5. **有没有更对症的原生旋钮？** 比如 mpv 是否已有原生 ABR/动态降级机制，或 `--demuxer-max-back-bytes`、解码线程优先级。有原生就该替换这套手写闭环。
6. **档位是否该写进 `mpv.conf` 的 `profile` 而不是 Lua 逐属性 set？** 更可维护，但受限于坑①。

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
`[未实证]` = 经验值，等你来推翻。审计待办列在 README 第 7 节。
