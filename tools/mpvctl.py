#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mpvctl —— 通过 IPC 查询/控制【运行中】的 mpv 实例。

为什么需要它：`auto-quality.lua` 的降档/回升闭环一直没法验证，因为
mpv 默认不写日志、也不开放任何外部接口。开启 `input-ipc-server` 之后，
就能对着正在播的片子直接读 `frame-drop-count` / 当前档位属性，不用靠猜。

前置：mpv.conf 里要有
    input-ipc-server="\\\\.\\pipe\\mpv-ipc"      # Windows
    input-ipc-server=/tmp/mpv-ipc                # Linux / macOS

用法:
    python mpvctl.py                      # 打印状态摘要（默认）
    python mpvctl.py status               # 同上
    python mpvctl.py get scale deband     # 取任意属性
    python mpvctl.py all                  # 列出全部属性（很长）
    python mpvctl.py cmd '{"command":["seek","10","absolute"]}'
    python mpvctl.py watch                # 每 3s 刷新一次状态，Ctrl+C 停

注意：同时只能有一个 mpv 实例绑定同一个管道名；第二个会绑定失败（不影响播放）。
"""
import argparse
import json
import os
import sys
import time

DEFAULT_PIPE = r"\\.\pipe\mpv-ipc" if os.name == "nt" else "/tmp/mpv-ipc"

# 状态摘要关心的属性：(属性名, 显示名, 备注)
STATUS_PROPS = [
    ("filename",                  "片源",       ""),
    ("width",                     "宽",         ""),
    ("height",                    "高",         ""),
    ("video-format",              "编码",       ""),
    ("hwdec-current",             "硬解",       "no = 在软解"),
    ("container-fps",             "片源帧率",   ""),
    ("estimated-vf-fps",          "实测帧率",   "低于片源帧率 = 在丢帧"),
    ("video-sync",                "同步模式",   ""),
    ("frame-drop-count",          "总丢帧",     "脚本闭环读的就是它"),
    ("decoder-frame-drop-count",  "解码丢帧",   ""),
    ("time-pos",                  "播放位置",   ""),
    # —— 脚本自报状态（v5.6 起外露，权威）——
    ("user-data/auto-quality/tier",     "脚本档位",   "脚本自报，权威"),
    ("user-data/auto-quality/baseline", "基线档",     "脚本自报"),
    ("user-data/auto-quality/auto",     "自动档开关", "False = 被 Ctrl+a/Ctrl+d 关掉了"),
    # —— 以下 6 个是 auto-quality.lua 的托管键 ——
    ("scale",                     "scale",      "托管键"),
    ("cscale",                    "cscale",     "托管键"),
    ("dscale",                    "dscale",     "托管键"),
    ("linear-downscaling",        "linear-down", "托管键"),
    ("deband",                    "deband",     "托管键"),
    ("interpolation",             "interpolation", "托管键"),
]


def connect(pipe, retries=20, delay=0.5):
    for i in range(retries):
        try:
            return open(pipe, "r+b", buffering=0)
        except OSError:
            if i == retries - 1:
                raise
            time.sleep(delay)
    raise OSError("无法连接 " + pipe)


class Client:
    """mpv IPC 客户端。

    ⚠ 关键坑：mpv 会在同一根管道上【主动推送事件】（start-file / playback-restart /
    end-file / property-change ...）。朴素的「写一条、读一行」会把事件当成响应，
    请求与应答就此错位 —— 症状是读 `frame-drop-count` 却拿到 `bilinear`
    （那是上一条 `cscale` 的返回值）。必须带 `request_id` 按 id 配对。
    """

    def __init__(self, pipe):
        self.f = connect(pipe)
        self._id = 0

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
            # 事件推送 / 过期响应 —— 丢弃

    def get(self, prop):
        r = self.cmd(command=["get_property", prop])
        if r.get("error") == "success":
            return r.get("data")
        return "<%s>" % r.get("error")

    def close(self):
        try:
            self.f.close()
        except Exception:
            pass


def guess_tier(c):
    """档位。优先读脚本自报的 user-data（v5.6 起外露，权威）；
    读不到时按托管键反推 —— 但反推在「混合态」下会给出错误结论
    （实测踩到：scale=ewa_lanczossharp 却 dscale=bilinear、linear-downscaling=no，
    不属于任何一档），所以只作兜底。
    """
    t = c.get("user-data/auto-quality/tier")
    if isinstance(t, str) and t != "none" and not t.startswith("<"):
        auto = c.get("user-data/auto-quality/auto")
        tag = {"full": "满血", "light": "4K轻量", "emergency": "应急"}.get(t, "")
        suffix = "" if auto is True else "   ⚠ 自动档已关"
        return "%s（%s）%s" % (t, tag, suffix)

    scale = c.get("scale")
    lin = c.get("linear-downscaling")
    cscale = c.get("cscale")
    if scale == "fast_bilinear" or lin is False:
        return "疑似 emergency（应急）— 反推，脚本未外露状态"
    if cscale == "bilinear" or c.get("deband") is False:
        return "疑似 light（4K轻量）— 反推，脚本未外露状态"
    if scale == "ewa_lanczossharp":
        return "疑似 full（满血）— 反推，脚本未外露状态"
    return "未知（可能被外部改成了混合态）"


def print_status(c):
    print("=" * 62)
    print("  mpv 实时状态   " + time.strftime("%Y-%m-%d %H:%M:%S"))
    print("=" * 62)
    for prop, label, note in STATUS_PROPS:
        v = c.get(prop)
        line = "  %-14s %-22s" % (label, v)
        if note:
            line += "  # " + note
        print(line)
    print("-" * 62)
    print("  反推档位      " + guess_tier(c))
    print("=" * 62)


def main():
    ap = argparse.ArgumentParser(description="mpv IPC 控制/查询")
    ap.add_argument("action", nargs="?", default="status",
                    choices=["status", "get", "all", "cmd", "watch"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--pipe", default=DEFAULT_PIPE)
    a = ap.parse_args()

    c = Client(a.pipe)
    try:
        if a.action == "status":
            print_status(c)
        elif a.action == "get":
            for p in a.args:
                print("%-28s = %s" % (p, c.get(p)))
        elif a.action == "all":
            for p in sorted(c.cmd(command=["get_property", "property-list"]).get("data", [])):
                print("%-32s = %s" % (p, c.get(p)))
        elif a.action == "cmd":
            print(json.dumps(c.cmd(**json.loads(a.args[0])), ensure_ascii=False, indent=2))
        elif a.action == "watch":
            while True:
                os.system("cls" if os.name == "nt" else "clear")
                print_status(c)
                time.sleep(3)
    except KeyboardInterrupt:
        pass
    finally:
        c.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
