---
type: resource
area: tech
date: 2026-08-13
status: active
version: 2.0
---

# mpv 播放器调优（Windows + 弱显卡）

> ⚠️ **2026-09-27 大幅订正**：本页原停留在 2026-08-13，且**推荐的 `tone-mapping=st2094-40` 已被用户实测否决**（08-21 换成 hable → 09-06 删除用默认）。**照旧页改配置 = 开倒车。**
>
> **唯一权威事实源是 `mpv.conf` 自己的注释**（18KB，绝大部分是带实测数据与回滚路径的决策日志），不是本页。改任何一项前先读那段注释。

## 硬件基线（本机）

- CPU：i7-8550U ｜ 显卡：Intel UHD 620（核显，**接显示器**）+ NVIDIA MX250 2GB（Optimus 拓扑）
- 屏幕：1080p SDR ｜ 内存带宽是本机瓶颈（4K 10bit 帧跨 PCIe 每帧约 25MB）
- mpv：v0.41.0-244 ｜ 安装于 `C:\Program Files\MPV Player`
- **配置路径：`C:\Users\Administrator\AppData\Roaming\mpv\`**（⚠️ 不是 `~/.config/mpv`）
- 策略脚本：`scripts/auto-quality.lua` v5.8 ｜ 开源 `github.com/jianglianbo300/mpv-auto-quality`

## 生效配置（2026-09-13 15:35 定稿，27 条生效行）

```ini
# 渲染
vo=gpu-next
gpu-api=d3d11
d3d11-adapter="Intel(R) UHD Graphics 620"   # 强制核显渲染（见「GPU 结论」）

# 解码（zero-copy，勿退回 auto-safe）
hwdec=d3d11va

# 同步（2026-09-13 13:10 由 display-resample 改 audio）
video-sync=audio

# 色彩
target-peak=250          # 旋钮：嫌亮改回 203，或换 mobius/clip（见配置 L197）
hdr-compute-peak=no
icc-profile-auto=yes
linear-downscaling=yes
dither-depth=auto

# 缩放
scale=ewa_lanczossharp
cscale=ewa_lanczossharp
dscale=mitchell

# 增强 / 体验
deband=yes
deband-iterations=1
deband-threshold=32
interpolation=no          # 桌面 GPU 被抢占时插帧是丢帧放大器
keep-open=yes
save-position-on-quit=yes
volume=100
sub-auto=fuzzy
sub-codepage=utf-8:gb18030
sub-font="Microsoft YaHei"
osd-font-size=44
title=${filename}
log-file="~~/mpv.log"
input-ipc-server="\\.\pipe\mpv-ipc"   # auto-quality.lua 的对外状态通道
```

> 注：`tone-mapping` / `target-colorspace-hint` / `d3d11-output-csp` **有意缺省**，理由见下节。备份 13 个，最新 `mpv.conf.bak-before-default-igpu-20260913-151628`。

## 已删除项与删除理由（勿恢复）

| 项 | 演进 | 删除理由 |
|---|---|---|
| `tone-mapping` | 08-13 `st2094-40` → **08-21 改 `hable`** → **09-06 删除**用默认(auto=spline) | 用户实测后逐步弃用。配置 L200 留有 A/B 记录：若白天窗外光多、屏幕偏亮，可临时改 `bt.2446a` |
| `target-colorspace-hint=yes` | 08-13 有 → 09-06 删 → 09-06 短暂恢复 → **09-13 彻底删** | **源码级实证**：`d3d11_get_mp_csp()` 把 G22/P709 映射为 UNKNOWN，与默认 auto 同路径 → 在 1080p SDR 屏上**空转**，不产生任何效果 |
| `d3d11-output-csp=srgb` | 同上，同批删除 | 同上（同函数同路径） |

## GPU 结论（2026-09-13 三形态实测 + 真实播放复验）

mpv 只有**一个 `vo`、一个 GPU 上下文**，没有"两块卡同时渲染一帧"的开关。能拆的只有解码与渲染两段。

| 形态 | 渲染 | 解码 | 应急档基准 | 基线档 |
|---|---|---|---|---|
| 全核显 | UHD 620 | Intel | 16.6 | 13.2 |
| 混合 | UHD 620 | MX250/cuda | 17.2（**最稳**） | 13.1 |
| 全独显 | MX250 | MX250 | 15.4 | 15.6（+18%） |

> **最终采用：核显渲染 + `hwdec=d3d11va`（不用 cuda）。** 09-13 15:16 又试了一次独显（备份 `bak-before-default-igpu`），19 分钟后改回核显。

- **轻负载核显赢**：独显渲染完仍要跨卡回传给接屏的核显，拷贝费抵消算力优势
- **重负载独显赢**（+18%）
- 争用测试：核显被占用时两者都掉约 30%，但**排序不反转** → 应用级"双卡分工"假设不成立
- **`icc-profile-auto=yes` 几乎零成本**（16.6 vs 16.5）→ 别为性能关 ICC

## ⚠️ 瓶颈定位：降档不是杠杆（最重要的一条）

**真实播放复验（150s，热机）**：

- 档位动作仅 2 次：`0.962s light` → `15.119s emergency（丢帧 14.6/s）`
- 丢帧率 50 采样：平均 **14.8/s**，累计 2215，**0 次回升 / 0 次冷却**
- **降档前后丢帧率几乎没变**：`light` ~15.0/s → `emergency` ~14.8/s（**-1.3%**）
- 有效帧率 ≈ **9.2 fps**（约 62% 帧被丢）

> **三条结论：**
> 1. **应急档救不了 4K DV**（热机状态）。冷机 33.1 fps > 24，属状态相关
> 2. **降档本身不是杠杆** —— 瓶颈在**缩放器之前**（解码吞吐 / DV RPU 色调映射 / 内存带宽 / present）
> 3. **`--untimed` 基准高估真实播放能力约 1.8 倍**（16.6 vs 9.2）

## 热漂移警告（比任何配置差异都大）

同参数同帧数重跑：冷机 **33.1 fps** → 连续 30 分钟后 **18.5 / 19.5 / 19.5 fps**。
**本机持续负载后性能掉到约 57%，漂移幅度 1.7 倍 > 任何 GPU 选择差异（±18%）。**

> ⚠️ **任何跨会话的性能数字都不可直接比较**，必须同时记录「帧数/参数/冷机还是热机」。

## auto-quality.lua v5.8（策略本体）

单写者设计（升降档逻辑全收进本脚本，避免与第二个脚本抢同一批属性）。核心常量：

| 常量 | 值 | 含义 |
|---|---|---|
| `SAMPLE_INTERVAL` | 3s | 丢帧采样周期 |
| `DROP_THRESHOLD` | 1.0 帧/秒 | 人眼可感阈值 |
| `CONFIRM` | 2 周期 | 连续超阈才降档（防毛刺） |
| `CLEAN_SECONDS` | 60s | 应急档零丢帧才试探回升 |
| `RECHECK_SECONDS` | 25s | 回升后观察期，复发记失败 |
| `COOLDOWN_SECONDS` | 300s | 连续 2 次回升失败 → 冷却 |
| `WIDTH_4K` | 3800px | 4K 判定 |
| `SETTLE_SECONDS` | 10s | 换档后静默期（只记录不判定） |

**v5.8 修的关键 bug**：`scale="fast_bilinear"` 是**旧 `vo=gpu` 后端**的缩放器名，在 `gpu-next`/libplacebo 下**非法**，v0.41 已彻底移除 → 降档时该键 `set` 静默失败（用户日志三次降档全报 `Invalid value for option scale: fast_bilinear`），**档位照切、OSD 照提示，唯独最重的 scale 根本没换 → 降档形同虚设**。改用 `bilinear`，`dscale` 改 `oversample`（实测 4K DV 300 帧 oversample 23.4fps vs bilinear 21.3fps）。

**v5.7 修的振荡**：换档会触发 libplacebo 重编译 shader（单次最慢 1199.7ms），编译期丢帧被误判成"还在卡" → full↔emergency 振荡。修法是换档后 10 秒静默期。

## 2026-09-27 实测：v5.8 首次真实播放验证 + 4K 瓶颈二分

ffmpeg QSV 硬编两个 HEVC Main10 样本（4K DV 常见码率量级），**同会话背靠背**跑（天然控制热漂移 1.7× 这个干扰变量），真实窗口 1920x1080 输出尺寸、真实用户配置、独立日志与管道：

| 样本 | 分辨率 | 码率 | 档位 | 丢帧率 | stutter | v5.8 档位表自检 |
|---|---|---|---|---|---|---|
| fhd_hevc10 | 1920x1080 | 16.7 Mbps | `full` | **0.00/s（17 采样全 0）** | 0/17 | ✅ 通过 |
| uhd_hevc10 | 3840x2160 | 55.9 Mbps | `light` | **0.00/s（17 采样全 0）** | 0/17 | ✅ 通过 |

**四条结论：**

1. **v5.8 的修复被证实** —— `Invalid value for option scale` 归零、档位表自检通过、`auto-quality: set ... failed` 0 次。09-13 判定的「降档形同虚设」是真 bug，**已确认修好**。
2. **零拷贝链路正常** —— 配置写 `hwdec=d3d11va`，实际加载驱动是 **`d3d11-egl`**（EGL interop 零拷贝路径，非拷贝回退）。渲染设备 `Intel(R) UHD Graphics 620`。
3. **宽度定基线 + 静默期按设计工作** —— 1080p→`full`、4K→`light`；v5.7 的 10s 静默期各命中 3 个样本。
4. **★ 4K HEVC10 56Mbps 本身跑得动（60s 零丢帧）** → 09-13「应急档救不了 4K DV」的根因**不在 4K 带宽/解码**，瓶颈指向 **DV RPU 色调映射**。而这正是 `auto-quality.lua`（只改缩放器）**够不到的一环** → **继续在缩放档位上使劲是无效杠杆**。

> ⚠️ **本实验的边界（别过度外推）**：样本是 `testsrc2` 合成（无胶片颗粒、编码难度低）、非全屏（规避 present 差异）、60 秒单次样本、无 DV RPU。**不能据此断言真实 4K DV 会流畅。** 要收口仍需一个真实 4K DV 片源。

## ⚠️ 未验证项（勿当已验证）

| # | 未解项 | 状态 |
|---|---|---|
| 1 | v5.8 修完后真实播放能否救场 | ✅ **已验证缩放器名合法、无 set 失败**（见上节）。但「应急档能否救 4K DV」仍未答 —— 因为 4K 非 DV 根本不触发降档 |
| 2 | 混合方案在真实播放下是否仍"最稳" | 未验证（最后改成了不用 cuda 的纯核显方案） |
| 3 | 「卡」有多少来自热漂移、多少来自配置 | 未解。漂移幅度 1.7× 大于任何配置差异 |
| 4 | 真实播放瓶颈在缩放器之前的**哪一环** | **已大幅收窄**：4K 非 DV 零丢帧 → 排除「解码吞吐 / 内存带宽」，**嫌疑集中到 DV RPU 色调映射**。仍需真实 DV 片源确认 |
| 5 | 丢帧阈值 `DROP_THRESHOLD=1.0` 是经验值，未标定 | **1080p 已标定（2026-09-28）**：真实播放 4.4 小时取到 **5,330 个采样**，丢帧速率**均值 0.001/s、最大 3.0/s、仅 1/5330 超阈值**。⇒ 1.0 这个阈值相对本机基线有**约 3 个数量级余量**，1080p 侧不激进。**但 4K / DV 场景的基线仍只有 17 采样**，"可接受丢帧率"在 4K DV 下依然未知 |
| 6 | 1440p / 2.5K 落 `full` 档是否合理 | 未查 |
| 7 | 真实 4K DV 全屏播放 | **缺片源**。有一个真实 DV 文件即可一次收口 #1/#4 |
| 8 | ~~脚本 debug 采样行默认进不了 `mpv.log`~~ | ✅ **已证伪（2026-09-28）**，见方法论 #10 |

## 排查方法论（本机踩过的坑）

1. **配置审计必须 dump 生效值，读注释会骗你**（09-12 栽过；`target-peak` 的"幽灵行"就是这么来的）
2. **mpv 排查三件套**：键位查 debug 日志（`input-bindings` 看不到脚本注册的键）、生效值写 dump 脚本、`mp.set_property` 必须 `pcall`（成功返回 nil、失败抛错）
3. **`MPV_HOME` 环境变量做配置隔离实验** —— 不碰用户真实配置就能做任意 A/B
4. **改用户配置前逐字节备份；验证用独立管道名 + 临时日志**
5. **先用「逻辑不可能」筛脏数据，再问"统计显著吗"** —— 子集比超集慢 → 直接丢掉重测
6. ⚠️ **mpv 0.41 + d3d11va 下 `video-params/*` 全读 nil**，必须用别名 `width`
7. ⚠️ `--untimed` 基准会高估真实播放能力约 1.8 倍，结论不可直接搬
8. ⚠️ `--vo=null` 测试环境会回退软解（"Using software decoding" 是假象），验证要用真窗口
9. ⚠️ **`--msg-level` 在 mpv 0.41 必须写 `module=level`，裸级别不合法** —— `--msg-level=all` 直接报 `Expected '=' and a value.` + `Error parsing option msg-level` 并**秒退**。正确：`--msg-level=all=debug` 或只抓脚本模块 `--msg-level=auto_quality=debug`（脚本模块名见 `auto-quality.lua` L24）
10. 🔄 **【2026-09-28 订正】~~脚本的 `mp.msg.debug` 采样行在默认配置下根本不进 `mpv.log`~~ —— 这条结论是错的。**
    **实测**：真实播放 4.4 小时，`mpv.log` 共 18,588 行，其中 **`[d][auto_quality] sample rate=…` 采样行 5,330 条**，`[d]` 前缀即 debug 级。⇒ **mpv 的 `--log-file` 恒定按 debug 写，`msg-level` 管不着它**（与仓库 README L350/L479 的实证一致）。
    **更正的更正**：原文说"`auto-quality.lua` L24 注释是错的" —— **这句也不对**。L24 原文是"`--msg-level` 管不着它，所以 debug 级这行必然落进 mpv.log"，**它一开始就是对的**；错的是本条方法论自己**把注释读反了**。⇒ 一条错误结论顺着注释读法又生出下一条错误结论，这类链条只能靠实测打断。
    **真正的坑（今天踩到）**：`mpv.log` 在**会话刚启动后长时间保持 0 字节**（缓冲未刷新）——实测 12:53 启动，17:18 读到仍是 0 字节，17:20 就是 2.3 MB。⇒ **绝不能用"文件大小 = 0"判断日志不可用**，那会得出完全相反的结论。
    **验证姿势（正确顺序）**：① `python tools/mpvctl.py status`（秒级、零侵入、直读脚本自报档位）→ ② 需要标定数据时再看 `mpv.log` 的 `sample rate=` 行。**两通道今天交叉验证一致**：`tier=full baseline=full rate=0.00/s stutter=false`。
11. ⚠️ **别把 mpv 属性当文件** —— tier 状态写在 `user-data/auto-quality/tier`（mpv 属性），**不存在任何状态目录/文件**。查"脚本有没有跑过"要去日志或运行中实例的属性，删目录式的检查是错的

## 2026-09-28 真实播放复验：1080p 满血档 + 阈值标定（数据）

**对象**：`火线.H265.1080P.SE01.01.mkv`（1920×1080 / HEVC / 23.976 fps），连续播放 4.4 小时。

### 双通道交叉验证（结果一致）

| 通道 | 读到的关键值 |
|---|---|
| `mpvctl.py status`（IPC，`\\.\pipe\mpv-ipc`） | `tier=full` · `baseline=full` · `hwdec=d3d11va` · 帧率 23.976=23.976 · **总丢帧 12 / 解码丢帧 0** · 托管键全在最高档 |
| `mpv.log` 的 `[d][auto_quality] sample rate=` | **5,330 个采样** · 丢帧速率**均值 0.001/s、最大 3.0/s、仅 1/5330 超阈值** · 末行 `tier=full baseline=full stutter=false` |
| 进程 | CPU 平均 **2.2% 单核**、内存 387 MB → 硬解真在干活（软解会打满一个核） |

### 三条结论

1. **当前是最佳状态**：档位 `full` = 1080p 的基线档（已到顶），零丢帧、闭环健康 —— 是**判定结果**不是"没触发"。
2. **但 1080p 片源下"满血"的视觉余量本就小**：面板本身 1080p SDR ⇒ 1:1 无缩放，而 `scale`/`cscale`（ewa_lanczossharp）只在**缩放时**生效、`dscale` 同理。⇒ full 与下一档的实际差别**基本只剩 `deband`**（`interpolation` 两档都关，是故意的）。**这套配置的上限要在 4K→1080p 的降档路径上才看得到。**
3. **`DROP_THRESHOLD=1.0` 在 1080p 侧已可判定为宽松**（基线 ~0.00/s，余量约 3 个数量级）。**4K/DV 侧仍只有 17 采样，不可外推。**

## 关联

- `_Agent/Session/2026-09-13_mpv显卡分工三形态实测_混合方案启用.md` —— GPU 三形态 + 真实播放复验 + 热漂移
- `_Agent/Session/2026-09-13_mpv配置漂移审计_策略开源_WorkBuddy接入记忆协议.md` —— 源码级审计（空转项实证）+ 开源交付
- `_Agent/Session/2026-09-13_mpv真实播放实证_换档振荡修复.md` / `..._降档静默失效修复.md`
- `03_Resources/技能索引.md` → skill `mpv-tuning` / `mpv-playback-audit`
