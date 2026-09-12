#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""logcheck —— 从 mpv 日志里提取 auto-quality 的闭环证据。

背景：`auto-quality.lua` 的降档/回升闭环（full ↔ light ↔ emergency）长期
处于「未验证」状态。mpv 默认不写日志文件，所以播一场之后什么都查不到。
开启 `log-file` + 脚本 v5.5 的采样日志后，这个脚本负责把几万行 debug 噪声
里真正有用的几十行挑出来。

mpv 日志格式（实测）:
    [   0.124][w][auto_quality] AUTO-QUALITY -> full (新片源 width=1080)
    [   3.055][d][auto_quality] sample rate=0.00/s drops=0 dt=3.0s tier=full baseline=full stutter=false

⚠ 注意：mpv 的 `--log-file` **恒定按 debug 级写**，`--msg-level` 管不着它
（实测 `--msg-level=all=no` 也照样写满）。所以日志里噪声极多，必须靠本脚本过滤。

用法:
    python logcheck.py                     # 自动找 %APPDATA%\\mpv\\mpv.log
    python logcheck.py path/to/mpv.log
    python logcheck.py --tail 200          # 只看最后 200 行
"""
import argparse
import os
import re
import sys
from collections import Counter

TIER_LABEL = {"full": "满血", "light": "4K轻量", "emergency": "应急"}

RE_TS = re.compile(r"^\[\s*([0-9.]+)\]")
RE_TIER = re.compile(r"\[auto_quality\]\s+AUTO-QUALITY -> (\w+)\s+\((.*?)\)\s*$")
RE_SAMPLE = re.compile(
    r"\[auto_quality\]\s+sample rate=([0-9.]+)/s drops=(\d+) dt=([0-9.]+)s "
    r"tier=(\w+) baseline=(\w+) stutter=(true|false)")
RE_SRC = re.compile(r"^\s*\(\+\) Video.*?\((\w+)\s+(\d+)x(\d+)")


def default_log():
    if os.name == "nt":
        return os.path.join(os.environ.get("APPDATA", ""), "mpv", "mpv.log")
    return os.path.expanduser("~/.config/mpv/mpv.log")


def mmss(t):
    return "%d:%02d" % (int(t) // 60, int(t) % 60)


def main():
    ap = argparse.ArgumentParser(description="从 mpv 日志提取 auto-quality 闭环证据")
    ap.add_argument("log", nargs="?", default=default_log())
    ap.add_argument("--tail", type=int, default=0, help="只分析最后 N 行")
    a = ap.parse_args()

    if not os.path.exists(a.log):
        print("找不到日志文件: %s" % a.log)
        print("检查 mpv.conf 里是否有:  log-file=\"~~/mpv.log\"")
        return 1

    with open(a.log, encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    if a.tail:
        lines = lines[-a.tail:]

    tiers, samples = [], []
    src = None
    ver = None
    for ln in lines:
        m = RE_TS.match(ln)
        if not m:
            continue
        t = float(m.group(1))
        if ver is None and "mpv v" in ln:
            v = re.search(r"mpv (v[0-9][^\s]*)", ln)
            if v:
                ver = v.group(1)
        if src is None:
            s = RE_SRC.search(ln)
            if s:
                src = s.groups()
        mt = RE_TIER.search(ln)
        if mt:
            tiers.append((t, mt.group(1), mt.group(2)))
            continue
        ms = RE_SAMPLE.search(ln)
        if ms:
            samples.append((t, float(ms.group(1)), int(ms.group(2)),
                            float(ms.group(3)), ms.group(4), ms.group(5),
                            ms.group(6) == "true"))

    print("=" * 66)
    print("  mpv 日志闭环分析  —  %s" % a.log)
    print("=" * 66)
    total = 0
    if lines:
        m = RE_TS.match(lines[-1])
        total = float(m.group(1)) if m else 0
    print("  mpv 版本   : %s" % (ver or "?"))
    if src:
        print("  片源       : %s %sx%s" % src)
    print("  日志覆盖   : %s（末行时间戳）" % mmss(total))
    print("  档位变更   : %d 次      采样点: %d 个" % (len(tiers), len(samples)))

    print()
    print("─" * 66)
    print("  档位变更时间线")
    print("─" * 66)
    if tiers:
        for t, tier, why in tiers:
            print("  %7s   -> %-10s %s  %s"
                  % (mmss(t), tier, TIER_LABEL.get(tier, ""), why))
    else:
        print("  （无档位变更记录）")

    print()
    print("─" * 66)
    print("  丢帧采样统计")
    print("─" * 66)
    if not samples:
        print("  （无采样记录）")
        print("  可能原因：脚本版本 < v5.5（没有 sample 日志），")
        print("            或日志被截断，或本次播放时长不足一个采样周期（3s）。")
    else:
        rates = [s[1] for s in samples]
        nonzero = [s for s in samples if s[1] > 0]
        print("  最大丢帧率 : %.2f 帧/s" % max(rates))
        print("  平均丢帧率 : %.3f 帧/s" % (sum(rates) / len(rates)))
        print("  非零采样   : %d / %d (%.1f%%)"
              % (len(nonzero), len(samples), 100.0 * len(nonzero) / len(samples)))
        by_tier = Counter(s[4] for s in samples)
        print("  采样分布   : %s"
              % "  ".join("%s=%d" % (k, v) for k, v in by_tier.most_common()))
        if nonzero:
            print()
            print("  非零采样明细（前 20 条）:")
            for s in nonzero[:20]:
                print("  %7s   rate=%6.2f/s  drops=%-6d tier=%-9s stutter=%s"
                      % (mmss(s[0]), s[1], s[2], s[4], s[6]))
        print()
        print("  丢帧率分布直方图（0.5 帧/s 一档）:")
        buckets = Counter(min(int(r / 0.5), 10) for r in rates)
        for b in range(11):
            n = buckets.get(b, 0)
            if not n:
                continue
            label = "%.1f-%.1f" % (b * 0.5, (b + 1) * 0.5) if b < 10 else ">=5.0"
            print("  %-9s %6d  %s" % (label, n, "#" * min(int(60 * n / len(samples)), 60)))

    print()
    print("─" * 66)
    print("  判定")
    print("─" * 66)
    fired = [t for t, tier, _ in tiers if tier == "emergency"]
    if fired:
        print("  ✅ 闭环【已触发】：%d 次进入应急档，首次在 %s"
              % (len(fired), mmss(fired[0])))
        print("     —— 这是本策略第一次拿到闭环真跑过的证据，请把日志留档。")
    elif tiers:
        print("  ⚪ 闭环【未触发】：只有基线档决策，没有降档/回升。")
        if samples and max(s[1] for s in samples) > 0:
            print("     期间有过丢帧（最大 %.2f 帧/s），但没到 DROP_THRESHOLD=1.0 的"
                  "连续 2 周期，属正常。")
        else:
            print("     期间零丢帧 —— 要么片子很轻，要么压力不够，没能压出闭环。")
    else:
        print("  ⚠ 没有任何 auto_quality 记录：脚本可能没加载，或日志不是这次播放的。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
