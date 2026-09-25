"""双击入口 (窗口化 exe, 自己不开任何窗口): 先判单例, 再决定唤醒还是拉起 TUI。

为什么必须多这一层 (2026-09-25 实测):
控制台 exe 被双击时, 控制台 (→ Windows Terminal 窗口) 由系统在我们的代码跑起来
**之前**就建好了 —— 于是"第二次双击"必然先闪出一个 WT 窗口, 再被我们判为重复而
关掉。想做到"重复打开同一个文件不弹窗", 判单例的就必须是一个没有控制台的窗口化
进程, VIMD.exe 就是它:

  * 已经有窗口开着这个文件 → 抬手把那个窗口抬起来, 本进程自始至终不建任何窗口;
  * 没有 → CREATE_NEW_CONSOLE 拉起同目录的 VIMD-tui.exe (真 TUI, 控制台 exe)。

从终端里手敲 VIMD.exe 时能附到父控制台, 那种情况把 TUI 拉进同一个终端标签里跑,
不另开窗口 (与改造前的行为一致)。
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time
from pathlib import Path

from .singleton import (
    SPAWNED_ENV,
    peek,
    singleton_key,
    usable_parent_console,
    wake,
)

CREATE_NEW_CONSOLE = 0x00000010
TUI_EXE = "VIMD-tui.exe"
_PEEK_TRIES = 6  # 首个实例可能刚起来: 映射在、窗口句柄还没发布 -> 等它几拍
_PEEK_DELAY = 0.25

if os.name == "nt":
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _u32 = ctypes.WinDLL("user32", use_last_error=True)

    class _STARTUPINFOW(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("lpReserved", wintypes.LPWSTR),
            ("lpDesktop", wintypes.LPWSTR),
            ("lpTitle", wintypes.LPWSTR),
            ("dwX", wintypes.DWORD),
            ("dwY", wintypes.DWORD),
            ("dwXSize", wintypes.DWORD),
            ("dwYSize", wintypes.DWORD),
            ("dwXCountChars", wintypes.DWORD),
            ("dwYCountChars", wintypes.DWORD),
            ("dwFillAttribute", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("wShowWindow", wintypes.WORD),
            ("cbReserved2", wintypes.WORD),
            ("lpReserved2", ctypes.c_void_p),
            ("hStdInput", wintypes.HANDLE),
            ("hStdOutput", wintypes.HANDLE),
            ("hStdError", wintypes.HANDLE),
        ]

    class _PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("hProcess", wintypes.HANDLE),
            ("hThread", wintypes.HANDLE),
            ("dwProcessId", wintypes.DWORD),
            ("dwThreadId", wintypes.DWORD),
        ]

    _k32.CreateProcessW.argtypes = [
        wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.LPVOID, wintypes.LPVOID,
        wintypes.BOOL, wintypes.DWORD, wintypes.LPVOID, wintypes.LPCWSTR,
        ctypes.POINTER(_STARTUPINFOW), ctypes.POINTER(_PROCESS_INFORMATION),
    ]
    _k32.CreateProcessW.restype = wintypes.BOOL
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _u32.MessageBoxW.argtypes = [wintypes.HWND, wintypes.LPCWSTR,
                                 wintypes.LPCWSTR, wintypes.UINT]


def main(argv: list[str] | None = None) -> int:
    """返回进程退出码; 0 = 该干的都干了 (唤醒 / 拉起都算成功)。"""
    args = list(sys.argv[1:] if argv is None else argv)
    key = singleton_key(args[0] if args else None)
    owner = _peek_retry(key)
    if owner is not None and wake(owner.hwnd):
        return 0  # 已有窗口: 抬到前台, 本进程没开过窗口
    return _spawn_tui(args)


def _peek_retry(key: str, tries: int = _PEEK_TRIES, delay: float = _PEEK_DELAY):
    """等对面把窗口句柄发布出来 (首个实例可能正启动, 映射里还是 0)。"""
    owner = peek(key)
    for _ in range(tries - 1):
        if owner is None or owner.hwnd:
            break
        time.sleep(delay)
        owner = peek(key)
    return owner


def _tui_target():
    """真正跑 TUI 的命令 (exe + 参数); 找不到 = None (只拷走 VIMD.exe 的情况)。"""
    exe = Path(sys.executable).with_name(TUI_EXE)
    if exe.is_file():
        return str(exe), []
    source = Path(__file__).resolve().parent.parent / "vimd_tui.py"
    if source.is_file():
        return sys.executable, [str(source)]  # 源码运行: python vimd_tui.py
    return None


def _spawn_tui(argv: list[str]) -> int:
    target = _tui_target()
    if target is None:
        _complain()
        return 1
    exe, prefix = target
    if usable_parent_console():
        # 终端里手敲: 接管这个终端 (子进程附到同一个控制台), 不另开窗口
        os.environ.pop(SPAWNED_ENV, None)
        flags = 0
    else:
        # 双击: 给它开一个新窗口; 记一笔, 万一它是"重复的那个"好意思把自己藏掉
        os.environ[SPAWNED_ENV] = "1"
        flags = CREATE_NEW_CONSOLE
    return 0 if _create_process(exe, prefix + list(argv), flags) else 1


def _create_process(exe: str, args: list[str], flags: int) -> bool:
    """CreateProcessW 直呼 —— 关键是 STARTUPINFO.dwFlags 保持 0。

    subprocess 会无条件塞 STARTF_USESTDHANDLES 并把**父进程的**标准句柄发给
    子进程; 而窗口化的 VIMD.exe 手上那些句柄是 PyInstaller 指向 NUL 的, 子进程
    拿到就成了哑巴。不给这个标志, 子进程的控制台句柄由系统按它自己接上的控制台
    填好 (新开的那个 / 附上来的那个)。
    """
    if os.name != "nt":
        return subprocess.Popen([exe, *args]).pid is not None
    cmdline = ctypes.create_unicode_buffer(subprocess.list2cmdline([exe, *args]))
    startup = _STARTUPINFOW()
    startup.cb = ctypes.sizeof(startup)
    info = _PROCESS_INFORMATION()
    ok = _k32.CreateProcessW(
        exe, cmdline, None, None, False, flags, None, None,
        ctypes.byref(startup), ctypes.byref(info),
    )
    if not ok:
        return False
    _k32.CloseHandle(info.hThread)
    _k32.CloseHandle(info.hProcess)
    return True


def _complain() -> None:
    """同目录缺 VIMD-tui.exe: 说清楚, 别让双击变成"什么都没发生"。"""
    message = (
        f"找不到 {TUI_EXE}。\n\n"
        "VIMD.exe 是启动器, 编辑器本体在同目录的 " + TUI_EXE + "。\n"
        "请从发布包的完整 dist/VIMD 文件夹里运行 VIMD.exe。"
    )
    if os.name == "nt":
        _u32.MessageBoxW(None, message, "VIMD", 0x00000010)
    else:
        print(message, file=sys.stderr)
