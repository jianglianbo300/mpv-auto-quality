#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""loop_test —— auto-quality 闭环的端到端验证台。

为什么需要：`auto-quality.lua` 的降档/回升闭环从 v5 起就是「未验证」状态。
没法在不打扰实际观看的前提下构造持续丢帧，所以一直没跑过。
本脚本用 IPC 控制 `speed` 来**可控地**制造和撤除负载，把四个阶段全跑一遍：

    基线 → 加压(丢帧) → 应降档到 emergency → 卸压 → 应回升到基线

前置：
  - mpv.conf 里有 `input-ipc-server`
  - `scripts/auto-quality.lua` >= v5.5（有 sample 日志）
  - 一个够小的测试视频（1080p 即可，本脚本用 --vo=null 不弹窗）

用法:
    python loop_test.py --video "D:/path/to/test.mp4"
    python loop_test.py --video test.mp4 --speed 8 --load-seconds 12 --clean-seconds 75
    python loop_test.py --video test.mp4 --keep-log      # 保留日志供人工分析

⚠ 阈值必须和 auto-quality.lua 顶部常量保持一致，脚本会做一次核对。
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time

# 与 auto-quality.lua 顶部常量保持一致
DROP_THRESHOLD = 1.0
CONFIRM = 2
SAMPLE_INTERVAL = 3
CLEAN_SECONDS = 60
# v5.7 起脚本有换档/起播静默期（SETTLE_SECONDS，默认 10s），期间采样不参与判定。
# 所以加压窗口必须覆盖 静默期 + 降档确认期，否则测不出降档（详见 --load-seconds 说明）。
SETTLE_SECONDS = 10

MANAGED = ["scale", "cscale", "dscale", "linear-downscaling", "deband", "interpolation"]

MPV_CANDIDATES = [
    r"C:\Program Files\MPV Player\mpv.exe",
    "/usr/bin/mpv",
    "/usr/local/bin/mpv",
    "/opt/homebrew/bin/mpv",
]


def find_mpv():
    for p in MPV_CANDIDATES:
        if os.path.exists(p):
            return p
    return "mpv"


def default_pipe():
    return r"\\.\pipe\mpv-ipc" if os.name == "nt" else "/tmp/mpv-ipc"


def default_log():
    if os.name == "nt":
        return os.path.join(os.environ.get("APPDATA", ""), "mpv", "mpv.log")
    return os.path.expanduser("~/.config/mpv/mpv.log")


def test_pipe(tag="looptest"):
    """测试专用管道名。

    ⚠ 为什么必须另起名字：mpv 的 `input-ipc-server` 是【先到先得】的。如果测试
    沿用默认管道名，而用户正在用同一个名字看片，测试就会连上【用户的实例】，
    然后对着用户的播放发 `set speed 8` —— 既毁了测试结果，也毁了用户在看的东西。
    同一个坑还适用于 `--log-file`：测试实例会以覆盖模式打开用户的日志文件，
    把用户那一场的日志整段截断。
    """
    if os.name == "nt":
        return r"\\.\pipe\mpv-ipc-" + tag
    return os.path.join(os.path.expanduser("~"), ".mpv-ipc-" + tag)


def test_log(tag="looptest"):
    return os.path.join(tempfile.gettempdir(), "mpv-%s.log" % tag)


class Client:
    """mpv IPC 客户端。

    ⚠ 关键坑：mpv 会在同一根管道上【主动推送事件】（start-file / playback-restart /
    end-file / property-change ...）。朴素的「写一条、读一行」会把事件当成响应，
    导致请求与应答错位 —— 表现是读 `frame-drop-count` 却拿到 `bilinear`
    （那是上一条 `cscale` 的值）。必须给每条命令带 `request_id` 并按其配对。
    """

    def __init__(self, pipe, retries=40, delay=0.5):
        self.f = None
        self._id = 0
        for i in range(retries):
            try:
                self.f = open(pipe, "r+b", buffering=0)
                return
            except OSError:
                if i == retries - 1:
                    raise
                time.sleep(delay)

    def _readline(self):
        buf = b""
        while not buf.endswith(b"\n"):
            c = self.f.read(1)
            if not c:
                return None
            buf += c
        return buf.decode("utf-8", "replace")

    def cmd(self, **kw):
        self._id += 1
        kw["request_id"] = self._id
        self.f.write((json.dumps(kw) + "\n").encode("utf-8"))
        while True:
            line = self._readline()
            if line is None:
                raise EOFError("mpv 关闭了连接")
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("request_id") == self._id:
                return msg
            # 否则是事件推送或过期响应 —— 丢弃，继续读

    def get(self, prop):
        r = self.cmd(command=["get_property", prop])
        return r.get("data") if r.get("error") == "success" else None

    def set(self, prop, val):
        return self.cmd(command=["set_property", prop, val]).get("error")

    def tier(self):
        """档位。优先读脚本自报的 user-data（v5.6 起外露，权威）；
        读不到时按托管键反推（混合态下会误判，仅兜底）。"""
        t = self.get("user-data/auto-quality/tier")
        if isinstance(t, str) and t not in ("none", None) and not t.startswith("<"):
            return t
        scale = self.get("scale")
        lin = self.get("linear-downscaling")
        cscale = self.get("cscale")
        if scale == "fast_bilinear" or lin is False:
            return "emergency"
        if cscale == "bilinear" or self.get("deband") is False:
            return "light"
        if scale == "ewa_lanczossharp":
            return "full"
        return "unknown"

    def auto_enabled(self):
        return self.get("user-data/auto-quality/auto")

    def drops(self):
        return (self.get("frame-drop-count"),
                self.get("decoder-frame-drop-count"))

    def close(self):
        try:
            self.f.close()
        except Exception:
            pass


def phase(name):
    print()
    print("─" * 64)
    print("  %s" % name)
    print("─" * 64)


def main():
    ap = argparse.ArgumentParser(description="auto-quality 闭环端到端验证")
    ap.add_argument("--video", required=True)
    ap.add_argument("--mpv", default=find_mpv())
    ap.add_argument("--pipe", default=test_pipe(),
                    help="测试专用管道名（默认与用户实例隔离，勿改成 mpv-ipc）")
    ap.add_argument("--log", default=test_log(),
                    help="测试专用日志（默认写到临时目录，勿指向用户的 mpv.log）")
    ap.add_argument("--speed", type=float, default=8.0, help="加压用的倍速")
    ap.add_argument("--load-seconds", type=float,
                    default=SETTLE_SECONDS + (CONFIRM + 2) * SAMPLE_INTERVAL,
                    help="加压持续时长。必须 >= 静默期(SETTLE_SECONDS=%d) + "
                         "(CONFIRM+1)×SAMPLE_INTERVAL(%d)，否则降档还没确认加压就结束了"
                         % (SETTLE_SECONDS, (CONFIRM + 1) * SAMPLE_INTERVAL))
    ap.add_argument("--clean-seconds", type=float, default=CLEAN_SECONDS + 15,
                    help="卸压后等待时长（需 > CLEAN_SECONDS）")
    ap.add_argument("--keep-log", action="store_true")
    a = ap.parse_args()

    need = SETTLE_SECONDS + (CONFIRM + 1) * SAMPLE_INTERVAL
    if a.load_seconds < need:
        print("⚠ --load-seconds=%.0fs 偏短，建议 >= %.0fs"
              "（静默期 %d + (CONFIRM+1)×SAMPLE_INTERVAL %d）"
              % (a.load_seconds, need, SETTLE_SECONDS, (CONFIRM + 1) * SAMPLE_INTERVAL))
    if a.clean_seconds <= CLEAN_SECONDS:
        print("⚠ --clean-seconds=%.0fs 必须 > CLEAN_SECONDS=%d，否则测不出回升"
              % (a.clean_seconds, CLEAN_SECONDS))

    if os.path.exists(a.log) and not a.keep_log:
        try:
            os.remove(a.log)
        except OSError:
            pass

    cmd = [a.mpv, "--vo=null", "--ao=null", "--loop-file=inf",
           "--no-terminal",
           "--input-ipc-server=" + a.pipe,   # 独立管道，绝不碰用户实例
           "--log-file=" + a.log,            # 独立日志，绝不截断用户日志
           a.video]
    print("启动: %s" % " ".join(cmd))
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)

    results = {}
    try:
        c = Client(a.pipe)
        print("IPC 已连接 (%s)" % a.pipe)

        # ── 安全闸：确认连上的是【自己刚启动的】实例，不是用户正在看的那个 ──
        want = os.path.basename(a.video)
        got = c.get("filename") or ""
        if got != want:
            print()
            print("✗ 中止：连到的实例在放 %r，不是本测试要放的 %r。" % (got, want))
            print("  说明管道名撞车了（可能已有别的 mpv 占用了 %s）。" % a.pipe)
            print("  换一个 --pipe 名字重试。绝不要对着别人的播放发命令。")
            return 3
        print("安全闸通过：确认是本测试自己的实例（%s）" % got)

        time.sleep(SAMPLE_INTERVAL + 2)   # 等基线决策落定

        # ── 阶段 1: 基线 ──
        phase("阶段 1/4  基线")
        base = c.tier()
        print("  width=%s  tier=%s  drops=%s" % (c.get("width"), base, c.drops()))
        results["baseline"] = base
        if base not in ("full", "light"):
            print("  ✗ 基线档异常，后续判定不可靠")

        # ── 阶段 2: 加压 ──
        phase("阶段 2/4  加压 (speed=%g, %.0fs)" % (a.speed, a.load_seconds))
        c.set("speed", a.speed)
        t0 = time.time()
        peak = 0.0
        while time.time() - t0 < a.load_seconds:
            time.sleep(SAMPLE_INTERVAL)
            d = c.drops()[0] or 0
            print("  t=%4.1fs  tier=%-9s  frame-drop-count=%s"
                  % (time.time() - t0, c.tier(), d))

        # ── 阶段 3: 应已降档 ──
        phase("阶段 3/4  降档判定")
        after_load = c.tier()
        print("  加压后 tier = %s" % after_load)
        results["after_load"] = after_load

        # ── 阶段 4: 卸压，等回升 ──
        phase("阶段 4/4  卸压 (speed=1) 后等 %.0fs 看回升" % a.clean_seconds)
        c.set("speed", 1.0)
        t0 = time.time()
        while time.time() - t0 < a.clean_seconds:
            time.sleep(SAMPLE_INTERVAL)
            el = time.time() - t0
            print("  t=%4.1fs  tier=%-9s  frame-drop-count=%s"
                  % (el, c.tier(), c.drops()[0]))
        after_clean = c.tier()
        results["after_clean"] = after_clean

        # ── 结论 ──
        print()
        print("=" * 64)
        print("  结论")
        print("=" * 64)
        print("  基线档        : %s" % base)
        print("  加压后        : %s" % after_load)
        print("  卸压后        : %s" % after_clean)
        ok_down = after_load == "emergency"
        ok_up = after_clean == base
        print()
        print("  降档 (baseline -> emergency) : %s"
              % ("✅ 通过" if ok_down else "❌ 未触发"))
        print("  回升 (emergency -> baseline) : %s"
              % ("✅ 通过" if ok_up else "❌ 未触发"))
        print()
        if ok_down and ok_up:
            print("  🎉 闭环完整跑通 —— 这是该策略第一次拿到端到端实证。")
        elif ok_down:
            print("  降档通过、回升未通过。可能是卸压后仍未达 CLEAN_SECONDS，")
            print("  或回升后 25s 内复发被计了一次失败（连续 2 次会冷却 300s）。")
        else:
            print("  降档都没触发。检查 --speed 是否够高、--load-seconds 是否够长，")
            print("  或 DROP_THRESHOLD 是否被改过（当前假定 %g）。" % DROP_THRESHOLD)
        print("=" * 64)

        if os.path.exists(a.log):
            print()
            print("日志已留在: %s" % a.log)
            print("下一步:  python logcheck.py \"%s\"" % a.log)
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    return 0 if results.get("after_load") == "emergency" else 2


if __name__ == "__main__":
    sys.exit(main())
