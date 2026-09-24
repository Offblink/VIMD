"""系统文件对话框 — PowerShell + WPF 弹原生打开/保存窗口，零额外依赖。

协议: async 函数返回 (ok, value):
    ok=True,  value=路径  -> 用户确认
    ok=True,  value=""    -> 用户取消 (调用方应静默收场)
    ok=False              -> PowerShell/WPF 不可用 (调用方回退内置输入)

实现要点:
- -EncodedCommand (UTF-16LE base64): 绕开命令行引号地狱, 中文脚本安全
- 脚本首行把 [Console]::OutputEncoding 设为 UTF8: 中文路径经 stdout 不乱码
- ShowDialog() 返回 bool?: OK=$true, 取消=$false/无输出 -> 天然区分确认/取消
"""

from __future__ import annotations

import asyncio
import base64

_FILTER = "Markdown (*.md)|*.md|所有文件 (*.*)|*.*"
_HEAD = (
    "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
    "Add-Type -AssemblyName PresentationFramework; "
)


def _encoded(script: str) -> str:
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def _open_script() -> str:
    return (
        _HEAD
        + "$d = New-Object Microsoft.Win32.OpenFileDialog; "
        + f"$d.Filter = '{_FILTER}'; $d.CheckFileExists = $true; "
        + "if ($d.ShowDialog()) { $d.FileName }"
    )


def _save_script(default_name: str) -> str:
    name = default_name.replace("'", "''")  # PS 单引号串内转义
    return (
        _HEAD
        + "$d = New-Object Microsoft.Win32.SaveFileDialog; "
        + f"$d.Filter = '{_FILTER}'; "
        + "$d.DefaultExt = '.md'; $d.AddExtension = $true; "
        + f"$d.FileName = '{name}'; "
        + "if ($d.ShowDialog()) { $d.FileName }"
    )


async def _run(script: str) -> tuple[bool, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            "powershell.exe",
            "-NoProfile",
            "-STA",
            "-EncodedCommand",
            _encoded(script),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError:
        return False, ""
    out, _ = await proc.communicate()
    if proc.returncode != 0:
        return False, ""
    return True, out.decode("utf-8", errors="replace").strip()


async def system_open_file_dialog() -> tuple[bool, str]:
    """弹系统「打开文件」窗口。"""
    return await _run(_open_script())


async def system_save_file_dialog(default_name: str = "未命名.md") -> tuple[bool, str]:
    """弹系统「另存为」窗口，默认文件名 default_name。"""
    return await _run(_save_script(default_name))
