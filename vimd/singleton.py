"""单例 (按文件) + 唤醒已有窗口 — 命名内核对象 + 终端窗口特例。

判定"这个文件已经开着吗"用命名文件映射 `Local\\VIMD.doc.<sha1(key)>`:
内核对象随最后一个句柄关闭而消失 —— 进程被杀/崩溃都不留脏状态, 无需清理代码;
映射里放 {magic, pid, console hwnd}, 于是第二实例不必跟第一实例通信, 读到
句柄直接把它抬起来就行 (思路同 feng-lazi/flet/singleton.py 的
"命名互斥 + 唤醒窗口", 这里换成按文件粒度)。

终端窗口特例 (2026-09-25 四个探针实测, 见 C:/tmp/scratch/vimd-singleton):
  * VIMD 的"窗口"其实是 Windows Terminal 的窗口; 进程自己只拿得到
    GetConsoleWindow() 给的 `PseudoConsoleWindow`, 它是 WT 窗口的 owned window。
  * SetForegroundWindow 打在 WT 窗口本身上: **返回 True 却纹丝不动**;
  * 打在 PseudoConsoleWindow 上: WT 窗口真的被抬到前台, 最小化的也会先被
    ShowWindow(SW_RESTORE) 拉回来。
所以唤醒的落点是 pseudo console 句柄, 且结论必须用 GetForegroundWindow 复核
(不能信 SetForegroundWindow 的返回值)。
"""
from __future__ import annotations

import hashlib
import os
import struct
import time
from typing import NamedTuple

_IS_WIN = os.name == "nt"

# 启动器给"我专门为它开了一个窗口"的子进程打的环境变量标记 (见 launcher.py):
# 判重复时它才敢把自己的窗口整个藏掉 —— 窗口是专门为它开的, 藏了不伤别人
SPAWNED_ENV = "VIMD_LAUNCHED"

_MAGIC = b"VMD1"
_FMT = "<4sIQ"  # magic, pid, 控制台窗口句柄
_SIZE = struct.calcsize(_FMT)
_PREFIX = "Local\\VIMD.doc."

PAGE_READWRITE = 0x04
FILE_MAP_WRITE = 0x0002
FILE_MAP_READ = 0x0004
ERROR_ALREADY_EXISTS = 183

GW_OWNER = 4
SW_HIDE = 0
SW_RESTORE = 9
ATTACH_PARENT_PROCESS = 0xFFFFFFFF

if _IS_WIN:
    import ctypes
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _u32 = ctypes.WinDLL("user32", use_last_error=True)

    _k32.CreateFileMappingW.argtypes = [
        wintypes.LPVOID, wintypes.LPVOID, wintypes.DWORD,
        wintypes.DWORD, wintypes.DWORD, wintypes.LPCWSTR,
    ]
    _k32.CreateFileMappingW.restype = wintypes.HANDLE
    _k32.OpenFileMappingW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    _k32.OpenFileMappingW.restype = wintypes.HANDLE
    _k32.MapViewOfFile.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
        wintypes.DWORD, ctypes.c_size_t,
    ]
    _k32.MapViewOfFile.restype = ctypes.c_void_p
    _k32.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _k32.GetConsoleWindow.restype = wintypes.HWND
    _k32.AttachConsole.argtypes = [wintypes.DWORD]
    _k32.AttachConsole.restype = wintypes.BOOL
    _k32.FreeConsole.restype = wintypes.BOOL
    _k32.GetCurrentThreadId.restype = wintypes.DWORD

    _u32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    _u32.GetWindow.restype = wintypes.HWND
    _u32.IsWindow.argtypes = [wintypes.HWND]
    _u32.IsIconic.argtypes = [wintypes.HWND]
    _u32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    _u32.SetForegroundWindow.argtypes = [wintypes.HWND]
    _u32.SetForegroundWindow.restype = wintypes.BOOL
    _u32.BringWindowToTop.argtypes = [wintypes.HWND]
    _u32.GetForegroundWindow.restype = wintypes.HWND
    _u32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND, ctypes.POINTER(wintypes.DWORD),
    ]
    _u32.GetWindowThreadProcessId.restype = wintypes.DWORD
    _u32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    # 未公开但 user32 一直导出的老 API: 前台规则比 SetForegroundWindow 宽松
    _switch_window = getattr(_u32, "SwitchToThisWindow", None)
    if _switch_window is not None:
        _switch_window.argtypes = [wintypes.HWND, wintypes.BOOL]


def singleton_key(path) -> str:
    """单例键 = 归一化绝对路径 (未命名 = "")。

    同一个文件的两种写法 (相对/绝对、大小写、`..`) 归一到同一个键, 否则
    "重复打开同一个文件"会漏判。
    """
    if path is None:
        return ""
    text = str(path).strip()
    if not text:
        return ""
    return os.path.normcase(os.path.abspath(text))


def _obj_name(key: str) -> str:
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
    return _PREFIX + digest


class Owner(NamedTuple):
    """谁开着这个文件: 进程 + 它的控制台窗口句柄 (可能还没发布 = 0)。"""

    pid: int
    hwnd: int


def own_console_hwnd() -> int:
    """本进程控制台窗口句柄 (WT 下即 PseudoConsoleWindow); 没有控制台 = 0。"""
    if not _IS_WIN:
        return 0
    return int(_k32.GetConsoleWindow() or 0)


def usable_parent_console() -> bool:
    """父进程的控制台能不能接手 (在终端里手敲 = 能, 双击 = 不能)。

    GUI 子系统进程默认不附到父控制台, 双击时父进程是资源管理器 (本来就没控制台),
    所以先试 AttachConsole(父进程)。

    **附上了还得看它有没有窗口**: 有些父进程带的是无窗口控制台 (CREATE_NO_WINDOW
    之类), 附上去等于把 TUI 藏进看不见的地方 —— 实测过一次, 编辑器就这么凭空没了。
    那种情况撤回 (FreeConsole), 让上层改走"开一个新窗口"。
    """
    if not _IS_WIN:
        return False
    if own_console_hwnd():
        return True
    if not _k32.AttachConsole(ATTACH_PARENT_PROCESS):
        return False
    if own_console_hwnd():
        return True
    _k32.FreeConsole()
    return False


def _open_view(key: str, access: int):
    handle = _k32.OpenFileMappingW(access, False, _obj_name(key))
    if not handle:
        return None, None
    view = _k32.MapViewOfFile(handle, access, 0, 0, _SIZE)
    if not view:
        _k32.CloseHandle(handle)
        return None, None
    return handle, view


def peek(key: str) -> Owner | None:
    """读"谁开着这个 key"; 没人开 = None (键为空或对面是别的平台的进程也返回 None)。"""
    if not _IS_WIN:
        return None
    handle, view = _open_view(key, FILE_MAP_READ)
    if view is None:
        return None
    try:
        raw = ctypes.string_at(view, _SIZE)
    finally:
        _k32.UnmapViewOfFile(view)
        _k32.CloseHandle(handle)
    magic, pid, hwnd = struct.unpack(_FMT, raw)
    if magic != _MAGIC:
        return None  # 同名对象但不是我们写的 (版本不一致) -> 当没人开
    return Owner(int(pid), int(hwnd))


class DocClaim:
    """一个窗口同时登记一个文件: 拿 -> 发布自己的窗口句柄 -> 换文件 / 释放。"""

    def __init__(self) -> None:
        self._key: str | None = None
        self._handle = None
        self._view = None

    @property
    def key(self) -> str | None:
        """当前登记的文件键; None = 没登记 (别人开着同名文件, 或没有单例能力)。"""
        return self._key

    @property
    def held(self) -> bool:
        return self._handle is not None

    def claim(self, key: str) -> bool:
        """尝试持有 key: True = 现在是我持有 (含"本来就是我"); False = 别人开着它。

        换文件时先放掉旧键 — 放不掉的话旧文件会被误判成"还开着"。
        """
        if key == self._key and self.held:
            return True
        self.release()
        if not _IS_WIN:
            return True
        handle = _k32.CreateFileMappingW(
            None, None, PAGE_READWRITE, 0, _SIZE, _obj_name(key)
        )
        if not handle:
            return False  # 建不出来: 当作被别人占着, 交回上层保守处理
        if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
            _k32.CloseHandle(handle)
            return False
        self._handle = handle
        self._view = _k32.MapViewOfFile(handle, FILE_MAP_WRITE, 0, 0, _SIZE)
        self._key = key
        self.publish()
        return True

    def publish(self) -> bool:
        """把自己的进程号 + 控制台窗口句柄写进映射 — 第二实例靠它唤醒本窗口。

        没有控制台 (无头测试/pilot) 时窗口句柄写 0, 但**照样写头**: 映射内容
        被读到时至少要能看出"有人开着这个文件", 否则判重会漏掉这种实例。
        """
        if self._view is None:
            return False
        hwnd = own_console_hwnd()
        ctypes.memmove(
            self._view, struct.pack(_FMT, _MAGIC, os.getpid(), hwnd), _SIZE
        )
        return True

    def release(self) -> None:
        """放掉当前登记 (换文件 / 退出)。进程被杀时内核也会自动回收。"""
        if self._view is not None:
            _k32.UnmapViewOfFile(self._view)
            self._view = None
        if self._handle is not None:
            _k32.CloseHandle(self._handle)
            self._handle = None
        self._key = None


def _foreground_is(hwnd: int) -> bool:
    return int(_u32.GetForegroundWindow() or 0) == hwnd


def _raise_direct(hwnd: int) -> None:
    _u32.ShowWindow(hwnd, SW_RESTORE)
    _u32.BringWindowToTop(hwnd)
    _u32.SetForegroundWindow(hwnd)


def _raise_switch(hwnd: int) -> None:
    if _switch_window is not None:
        _switch_window(hwnd, True)


def _raise_steal(hwnd: int) -> None:
    """硬抢: 附到当前前台线程上再置前 (前两种都被前台锁规则挡住时用)。"""
    fg = _u32.GetForegroundWindow()
    fg_tid = _u32.GetWindowThreadProcessId(fg, None) if fg else 0
    my_tid = _k32.GetCurrentThreadId()
    if not fg_tid or fg_tid == my_tid:
        return
    _u32.AttachThreadInput(my_tid, fg_tid, True)
    try:
        _u32.BringWindowToTop(hwnd)
        _u32.SetForegroundWindow(hwnd)
    finally:
        _u32.AttachThreadInput(my_tid, fg_tid, False)


def wake(hwnd: int) -> bool:
    """把某个 VIMD 实例 (pseudo console 句柄) 的窗口抬到前台; 抬不动 = False。

    三级递进, 每级都用 GetForegroundWindow 复核 —— 这个 API 会撒谎。
    """
    if not _IS_WIN or not hwnd or not _u32.IsWindow(hwnd):
        return False
    owner = int(_u32.GetWindow(hwnd, GW_OWNER) or 0) or hwnd
    if _u32.IsIconic(owner):
        _u32.ShowWindow(owner, SW_RESTORE)
    if _foreground_is(owner):
        return True  # 已经在前台: 无事可做
    for attempt in (_raise_direct, _raise_switch, _raise_steal):
        attempt(hwnd)
        time.sleep(0.12)
        if _foreground_is(owner):
            return True
    return False


def wake_if_open(key: str) -> bool:
    """已有人开着这个 key 就把它的窗口抬起来; 没人开 / 抬不动 = False。"""
    owner = peek(key)
    if owner is None or not owner.hwnd:
        return False
    return wake(owner.hwnd)


def hide_own_window() -> bool:
    """把自己的整个 WT 窗口藏掉 — 只在"这个窗口是启动器专门为我开的"时才敢用。

    判断依据是环境变量 (见 launcher.py): 直接双击 VIMD-tui.exe、或在已有终端
    标签里跑时, 窗口不止属于我们, 藏掉会连用户别的标签一起吞掉。
    """
    if not _IS_WIN:
        return False
    me = own_console_hwnd()
    if not me:
        return False
    # WT 下控制台是 PseudoConsoleWindow, 窗口是它的 owner; 传统 conhost 下控制台
    # 窗口本身就是顶层窗口 (owner 为 0) —— 两种都藏掉同一个东西
    target = int(_u32.GetWindow(me, GW_OWNER) or 0) or me
    return bool(_u32.ShowWindow(target, SW_HIDE))
