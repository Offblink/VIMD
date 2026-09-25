"""预览区 — Markdown 渲染 + 链接/图片点击分发。

内置 links 模块的分类与路径解析纯函数，语义与 MDPad GUI 版一致：

- 网址 (http/https/mailto)     → 默认浏览器
- 本地文件 (盘符/file:/相对路径) → 系统默认程序打开（图片同理：
  Textual 的 image 节点被当作 link(src) 发 LinkClicked, 走同一条分发）
- 页内锚点 (#xxx)              → 暂不跳转，提示

Markdown 构造必须传 open_links=False：Markdown 自身默认会把任何 href
丢给 app.open_url (_markdown.py:997,1139)，会绕过这里的本地分流。
"""

from __future__ import annotations

import os
import re
import webbrowser
from pathlib import Path
from urllib.parse import unquote, urlparse

from textual.widgets import Markdown

from .links import classify_navigation_url, local_path_from_navigation

PREVIEW_MAX = 262_144  # 预览渲染字符上限, 超出截断并提示
# 实测 2.9M 字符全文重建 ~4.4s/次: 每个防抖到期全量重建 = 大文件打字卡死


def open_href(href: str, doc_dir: Path, notify) -> None:
    """把一个 href 分流到 浏览器 / 系统默认程序；失败时 notify 提示。

    markdown-it 的 normalizeLink 会把反斜杠/中文/空格 percent 编码
    (C:\a b.png -> C:%5Ca%20b.png), 本地分支必须先 unquote 还原;
    浏览器分支保留原始编码形式 (http 查询串语义不能动)。
    """
    if href.startswith("#"):
        notify("页内锚点暂不支持跳转")
        return
    decoded = unquote(href)
    kind = classify_navigation_url(decoded)
    if kind == "anchor":
        notify("页内锚点暂不支持跳转")
        return
    if kind == "file":
        path = local_path_from_navigation(decoded)
        target = Path(path) if path else doc_dir / decoded
    elif not urlparse(decoded).scheme:
        # 相对路径 (classify 归为 web, 这里按本地文件处理)
        target = doc_dir / decoded
    else:
        webbrowser.open(href)  # 原始编码形式给浏览器
        return
    try:
        os.startfile(str(target))
    except OSError:
        notify(f"打不开: {target}")


_SPACE_DEST = re.compile(r"\]\((?!<)([^)\n]* [^)\n]*)\)")

# 链接/图片: [text](dest) 与 ![alt](dest), dest 可能已被 normalize_dests 包成 <...>
_LINK_RE = re.compile(r"(!?)\[([^\]]*)\]\((<[^>]*>|[^()\s]*)\)")

# 本地文件类型 -> emoji (目录单独判)
_EMOJI_BY_SUFFIX = {
    ".png": "🖼️", ".jpg": "🖼️", ".jpeg": "🖼️", ".gif": "🖼️", ".bmp": "🖼️",
    ".webp": "🖼️", ".svg": "🖼️", ".ico": "🖼️", ".avif": "🖼️",
    ".md": "📝", ".txt": "📄", ".log": "📄",
    ".pdf": "📕", ".doc": "📃", ".docx": "📃", ".rtf": "📃",
    ".xls": "📊", ".xlsx": "📊", ".csv": "📊",
    ".ppt": "📽️", ".pptx": "📽️",
    ".zip": "📦", ".7z": "📦", ".rar": "📦", ".tar": "📦", ".gz": "📦",
    ".mp4": "🎬", ".mkv": "🎬", ".avi": "🎬", ".mov": "🎬", ".webm": "🎬",
    ".mp3": "🎵", ".wav": "🎵", ".flac": "🎵", ".ogg": "🎵", ".m4a": "🎵",
    ".py": "💻", ".js": "💻", ".ts": "💻", ".java": "💻", ".c": "💻",
    ".cpp": "💻", ".html": "💻", ".css": "💻", ".json": "📋", ".xml": "📋",
    ".sh": "💻", ".bat": "💻", ".ps1": "💻",
}


def _is_local_dest(dest: str) -> bool:
    """目的地是本地文件/目录 (盘符、UNC、file:、无 scheme 相对路径) 还是网址。"""
    if not dest or dest.startswith("#"):
        return False
    if re.match(r"^[A-Za-z]:", dest):  # C:\ C:/ C:relative
        return True
    if dest.lower().startswith("file:"):
        return True
    if dest.startswith("\\\\"):  # UNC
        return True
    return urlparse(dest).scheme == ""


def dest_emoji(dest: str, doc_dir: Path) -> str:
    """本地目的地 -> 类型 emoji: 目录 📁, 按后缀映射, 未知后缀/无后缀 📄。"""
    if dest.endswith(("/", "\\")):
        return "📁"
    p = Path(dest)
    if not p.is_absolute():
        p = Path(doc_dir) / p
    try:
        if p.is_dir():
            return "📁"
    except OSError:
        pass
    suffix = p.suffix.lower()
    if suffix in _EMOJI_BY_SUFFIX:
        return _EMOJI_BY_SUFFIX[suffix]
    if suffix:
        return "📄"
    # 无后缀: 磁盘上是文件给 📄, 猜不到按目录给 📁
    return "📄" if p.is_file() else "📁"


def linkify_local_dests(text: str, doc_dir: Path) -> str:
    """[]()/![]() 指向本地文件/目录 -> 统一显示类型 emoji; 网址/锚点原样。

    URL 规则不变 (点击走浏览器); 本地目的地点击仍走系统程序"跳转"。
    只改显示, 目的地原样保留 (含 <...> 包裹), 代码围栏内不动。
    """
    def repl(m: re.Match) -> str:
        dest = m.group(3)
        inner = dest[1:-1] if dest.startswith("<") else dest
        if not inner.strip() or not _is_local_dest(inner):
            return m.group(0)
        return f"[{dest_emoji(inner, doc_dir)}]({dest})"

    parts = re.split(r"(```.*?```)", text, flags=re.S)
    for i in range(0, len(parts), 2):  # 偶数段 = 围栏之外
        parts[i] = _LINK_RE.sub(repl, parts[i])
    return "".join(parts)


def normalize_dests(text: str) -> str:
    """把带空格的链接/图片目的地包成 CommonMark 尖括号形式。

    CommonMark 裸目的地不允许空格, 所以 ![屏](C:/a b.png) 会被解析器
    当成没闭合、整条退回纯文本 — 图片"不识别"的根因。尖括号形式
    明确允许空格; 解析后 href/src 还原为原始路径, 点击分流不受影响。
    只处理代码围栏之外的内容, 不篡改代码块里展示的示例。
    """
    parts = re.split(r"(```.*?```)", text, flags=re.S)
    for i in range(0, len(parts), 2):  # 偶数段 = 围栏之外
        parts[i] = _SPACE_DEST.sub(r"](<\1>)", parts[i])
    return "".join(parts)


class Preview(Markdown):
    """实时预览面板。"""

    def __init__(self, **kwargs) -> None:
        super().__init__(open_links=False, **kwargs)
        self.can_focus = True  # 预览模式下接管键盘焦点
        self.doc_dir: Path = Path.cwd()
        self._source: str | None = None  # 上次喂给渲染的原文 (同文去重)

    def on_markdown_link_clicked(self, event: Markdown.LinkClicked) -> None:
        event.stop()
        open_href(event.href, self.doc_dir, self.app.notify)

    def update(self, markdown: str):
        """同文去重 + 超长截断 (PREVIEW_MAX) + 归一化空格目的地。"""
        if markdown == self._source:
            # 打开时显式渲染过, Changed 防抖又来一次: 同文重建纯属浪费
            return None
        self._source = markdown
        body = markdown
        if len(body) > PREVIEW_MAX:
            cut = body[:PREVIEW_MAX].rsplit("\n", 1)[0]
            notice = (
                f"> ⚠ 内容 {len(markdown)} 字符, "
                f"预览仅渲染前 {len(cut)} 字符 (编辑与保存不受影响)\n\n"
            )
            body = notice + cut
        # 顺序: 先包空格目的地, 再把本地文件/目录链接统一成类型 emoji
        return super().update(linkify_local_dests(normalize_dests(body),
                                                  self.doc_dir))
