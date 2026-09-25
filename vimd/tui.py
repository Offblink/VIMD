"""TUI 入口 — 先判单例 (在任何 Textual 导入之前), 再起 VIMDApp。

单例在这里判而不是在 app 里判, 是为了"第二个进程尽早退出":
Textual 光是 import 就要几百毫秒, 而重复启动的那一个进程从系统给它建好控制台
窗口那一刻起就已经在屏幕上了 —— 越早判出来、越早走人, 那一闪越短。
(常态的双击路径根本不经过这里: 窗口化的 VIMD.exe 启动器已经先判过一次, 连
窗口都不会建。这一层是兜底: 直接跑 VIMD-tui.exe、或源码 `python vimd.py`。)
"""
from __future__ import annotations

import os

from . import singleton


def main(argv: list[str] | None = None) -> int:
    args = list(argv or [])
    path_arg = args[0] if args else None
    claim = singleton.DocClaim()
    key = singleton.singleton_key(path_arg)
    if not claim.claim(key):
        # 这个文件已经开着: 把那个窗口抬起来, 本进程就此打住
        if singleton.wake_if_open(key):
            spawned = os.environ.pop(singleton.SPAWNED_ENV, None)
            if spawned:
                singleton.hide_own_window()  # 启动器刚给我开的空窗, 藏掉别闪
            return 0
        # 抬不动 (对面刚没了 / 还没发布窗口): 落到下面照常启动, 只是不登记

    os.environ.pop(singleton.SPAWNED_ENV, None)
    # 重量级 import 放到最后: 确认真要开窗口了再拖 Textual 进来
    from .app import VIMDApp

    app = VIMDApp(path=path_arg, claim=claim)
    app.run()
    app.file_guard.release()
    claim.release()
    return 0
