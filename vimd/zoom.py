"""整体缩放 — 改 Windows Terminal 当前 profile 的 font.size。

TUI 画的是字符网格, "字体多大" 只有终端说了算 (Textual 没有 font-size 这种旋钮);
所以真正的"整体缩放"(编辑区+预览一起变大) 只能改终端的字体。做法:
WT 会热重载 settings.json, 于是直接改写当前 profile 的 `font.size` —
一次改到位、持久化到下次开窗, 且只影响 VIMD 这个 profile 的窗口。

本机实测 (WT 1.24.11911): WT 只注入 `WT_PROFILE_ID` / `WT_SESSION`, **不注入**
`WT_SETTINGS_DIR` (那个变量只在外部显式注入时才有, 测试用); WT 自己的临时缩放是
`Ctrl+=` / `Ctrl+-` / `Ctrl+0` (WT 接管, 应用收不到, 也不持久)。

**没有 `WT_PROFILE_ID` 就一个字节都不写**: 早先的版本在取不到 guid 时退回"改第一个
profile", 于是双击 exe 跑在控制台主机 (无 WT 变量) 里按 Alt+↓, 会悄悄把用户配置里
第一个 profile (Windows PowerShell) 的 font.size 改到 6, 而当前窗口毫无变化 — 用户
只会认为"按键不响应", 配置还被改坏了。

settings.json 是用户手写的 JSONC (可能带注释/自定义缩进), 所以这里做**定点文本改写**:
只动包住当前 guid 的那个 profile 对象里的 font.size, 其余字节一律不碰。
"""

from __future__ import annotations

import os
import re
from pathlib import Path

MIN_SIZE = 6.0
MAX_SIZE = 40.0
DEFAULT_SIZE = 12.0  # WT 默认字体大小 (profile 里没写 font.size 时按它算)

# 缩放结果为什么没生效 — 给界面直接拿去提示用户
OK = "ok"
NO_TERMINAL = "no-terminal"    # 不是从 Windows Terminal 起 (或读不到它的 settings.json)
NO_PROFILE = "no-profile"      # WT_PROFILE_ID 缺失 / settings.json 里没有这个 profile
WRITE_FAILED = "write-failed"  # 定位到了但写盘失败

MESSAGES = {
    NO_TERMINAL: "缩放只支持 Windows Terminal (找不到它的 settings.json)",
    NO_PROFILE: "缩放只在 Windows Terminal 的 VIMD 窗口里生效 — 当前窗口不是",
    WRITE_FAILED: "改不动 Windows Terminal 的 settings.json",
}

_GUID_RE = re.compile(r'"guid"\s*:\s*"(\{[0-9a-fA-F-]+\})"')
_SIZE_RE = re.compile(r'"size"\s*:\s*([0-9]+(?:\.[0-9]+)?)')


def profile_guid() -> str | None:
    """当前 WT profile 的 guid; 不在 Windows Terminal 的 profile 窗口里跑时为 None。"""
    return os.environ.get("WT_PROFILE_ID", "").strip() or None


def settings_path() -> Path | None:
    """WT 的 settings.json; WT_SETTINGS_DIR 只在外部显式注入时才有 (测试用)。

    注入了 WT_SETTINGS_DIR 就以它为准 (不在就认环境不对), 不再按包路径猜 —
    否则一旦那个目录里没有 settings.json, 就会悄悄改到另一处配置上去。
    """
    base = os.environ.get("WT_SETTINGS_DIR")
    if base:
        path = Path(base) / "settings.json"
        return path if path.is_file() else None
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return None
    for rel in (
        r"Packages\Microsoft.WindowsTerminal_8wekyb3d8bbwe\LocalState",
        r"Packages\Microsoft.WindowsTerminalPreview_8wekyb3d8bbwe\LocalState",
        r"Microsoft\Windows Terminal",
    ):
        path = Path(local) / rel / "settings.json"
        if path.is_file():
            return path
    return None


def _matching(text: str, open_index: int) -> int:
    """open_index 处是 '{' -> 返回与它配对的 '}' 下标; 找不到返回 -1。"""
    depth = 0
    for i in range(open_index, len(text)):
        char = text[i]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return i
    return -1


def _enclosing_object(text: str, index: int) -> tuple[int, int] | None:
    """从 index 往前找最近那个"还没闭合"的 '{' -> 它包住的对象区间 (含两端)。"""
    depth = 0
    for i in range(index, -1, -1):
        char = text[i]
        if char == "}":
            depth += 1
        elif char == "{":
            if depth == 0:
                end = _matching(text, i)
                return None if end < 0 else (i, end)
            depth -= 1
    return None


def profile_span(text: str, guid: str | None) -> tuple[int, int] | None:
    """当前 profile 的对象区间 (含两端)。

    guid 缺失或不匹配一律返回 None —— 绝不再"退回第一个 profile": 那会在非 WT
    环境里把用户的另一个 profile 改坏, 而当前窗口毫无反应, 排查起来毫无头绪。
    """
    if not guid:
        return None
    for candidate in _GUID_RE.finditer(text):
        if candidate.group(1).lower() == guid.lower():
            return _enclosing_object(text, candidate.start())
    return None


def _font_span(text: str, span: tuple[int, int]) -> tuple[int, int] | None:
    """profile 区间里 font 对象的区间 (含两端)。"""
    rel = text.find('"font"', span[0], span[1])
    if rel < 0:
        return None
    brace = text.find("{", rel, span[1])
    if brace < 0:
        return None
    end = _matching(text, brace)
    return None if end < 0 else (brace, end)


def _indent_of(text: str, index: int) -> str:
    start = text.rfind("\n", 0, index) + 1
    return "".join(c for c in text[start:index] if c in " \t")


def _read(path: Path) -> tuple[str, bool]:
    """读文本 + 是否带 BOM。

    一律走 bytes: 文本模式的读/写会做换行翻译 (\n <-> \r\n), 多轮改写下来
    行尾会翻倍 (\r\r\n), 把用户配置搞花 — 定点改写必须一个字节都不多动。
    """
    data = path.read_bytes()
    has_bom = data[:3] == b"\xef\xbb\xbf"
    return data.decode("utf-8-sig"), has_bom


def read_size() -> float | None:
    """当前 profile 的 font.size; 不在 WT / profile 不匹配 / 读不到时 None。"""
    path = settings_path()
    if path is None:
        return None
    text, _ = _read(path)
    span = profile_span(text, profile_guid())
    if span is None:
        return None
    font = _font_span(text, span)
    if font is None:
        return None
    match = _SIZE_RE.search(text, font[0], font[1])
    return float(match.group(1)) if match else None


def set_size(size: float) -> tuple[bool, float, str]:
    """把当前 profile 的 font.size 改成 size。返回 (是否真改了, 生效值, 原因)。"""
    size = min(max(size, MIN_SIZE), MAX_SIZE)
    path = settings_path()
    if path is None:
        return False, size, NO_TERMINAL
    text, has_bom = _read(path)
    span = profile_span(text, profile_guid())
    if span is None:
        return False, size, NO_PROFILE

    size_text = f"{size:g}"
    font = _font_span(text, span)
    nl = "\r\n" if "\r\n" in text else "\n"  # 插进去的行跟文件自己的换行风格一致

    if font is not None:
        inner = text[font[0] : font[1] + 1]
        if _SIZE_RE.search(inner):
            new_inner = _SIZE_RE.sub(r'"size": ' + size_text, inner, count=1)
        else:
            pad = _indent_of(text, font[0]) + "    "
            new_inner = inner[:1] + f'{nl}{pad}"size": {size_text},' + inner[1:]
        new_text = text[: font[0]] + new_inner + text[font[1] + 1 :]
    else:
        # profile 里还没有 font 段: 插在它自己的 '{' 之后
        pad = _indent_of(text, span[0]) + "    "
        new_text = (text[: span[0] + 1]
                    + f'{nl}{pad}"font": {{ "size": {size_text} }},'
                    + text[span[0] + 1 :])

    if new_text == text:
        return False, size, OK
    backup = path.with_suffix(path.suffix + ".bak-vimd")
    try:
        if not backup.exists():  # 头一次动手留个底, 免得把用户配置改坏了
            backup.write_bytes(path.read_bytes())
        path.write_bytes((b"\xef\xbb\xbf" if has_bom else b"") + new_text.encode("utf-8"))
    except OSError:
        return False, size, WRITE_FAILED
    return True, size, OK


def adjust(delta: float) -> tuple[bool, float, str]:
    """当前大小 ± delta (夹在 MIN/MAX 之间) 后写回。返回 (是否真改了, 生效值, 原因)。"""
    current = read_size()
    base = DEFAULT_SIZE if current is None else current
    return set_size(base + delta)
