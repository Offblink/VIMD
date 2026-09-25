"""整体缩放 — 改 Windows Terminal 当前 profile 的 font.size。

TUI 画的是字符网格, "字体多大" 只有终端说了算 (Textual 没有 font-size 这种旋钮);
所以真正的"整体缩放"(编辑区+预览一起变大) 只能改终端的字体。做法:
WT 会热重载 settings.json, 于是直接改写当前 profile 的 `font.size` —
一次改到位、持久化到下次开窗, 且只影响 VIMD 这个 profile 的窗口。

WT 自带的 ctrl+plus / ctrl+minus / ctrl+0 是 WT 自己接管的临时缩放 (应用收不到那些键),
所以 VIMD 绑 ctrl+shift+↑ / ctrl+shift+↓ / ctrl+shift+0。

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

_GUID_RE = re.compile(r'"guid"\s*:\s*"(\{[0-9a-fA-F-]+\})"')
_SIZE_RE = re.compile(r'"size"\s*:\s*([0-9]+(?:\.[0-9]+)?)')


def settings_path() -> Path | None:
    """WT 的 settings.json; WT_SETTINGS_DIR 由终端自己注入 (本机实测存在)。

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


def _profile_span(text: str, guid: str | None) -> tuple[int, int] | None:
    """当前 profile 的对象区间 (含两端); guid 缺失/找不到时退回第一个 profile。"""
    if guid:
        for candidate in _GUID_RE.finditer(text):
            if candidate.group(1).lower() == guid.lower():
                return _enclosing_object(text, candidate.start())
    list_start = text.find('"list"', text.find('"profiles"'))
    if list_start < 0:
        return None
    item = text.find("{", list_start)
    if item < 0:
        return None
    end = _matching(text, item)
    return None if end < 0 else (item, end)


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
    """当前 profile 的 font.size; 不在 WT / 读不到时 None。"""
    path = settings_path()
    if path is None:
        return None
    text, _ = _read(path)
    span = _profile_span(text, os.environ.get("WT_PROFILE_ID"))
    if span is None:
        return None
    font = _font_span(text, span)
    if font is None:
        return None
    match = _SIZE_RE.search(text, font[0], font[1])
    return float(match.group(1)) if match else None


def set_size(size: float) -> tuple[bool, float]:
    """把当前 profile 的 font.size 改成 size。返回 (是否真改了, 生效值)。"""
    size = min(max(size, MIN_SIZE), MAX_SIZE)
    path = settings_path()
    if path is None:
        return False, size
    text, has_bom = _read(path)
    span = _profile_span(text, os.environ.get("WT_PROFILE_ID"))
    if span is None:
        return False, size
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
        return False, size
    backup = path.with_suffix(path.suffix + ".bak-vimd")
    try:
        if not backup.exists():  # 头一次动手留个底, 免得把用户配置改坏了
            backup.write_bytes(path.read_bytes())
        path.write_bytes((b"\xef\xbb\xbf" if has_bom else b"") + new_text.encode("utf-8"))
    except OSError:
        return False, size
    return True, size


def adjust(delta: float) -> tuple[bool, float]:
    """当前大小 ± delta (夹在 MIN/MAX 之间) 后写回。"""
    current = read_size()
    base = DEFAULT_SIZE if current is None else current
    return set_size(base + delta)
