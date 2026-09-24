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
        return super().update(normalize_dests(body))
