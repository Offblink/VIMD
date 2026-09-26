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

from markdown_it import MarkdownIt
from textual.await_complete import AwaitComplete
from textual.widgets import Markdown
from textual.widgets.markdown import MarkdownBlock

from .links import classify_navigation_url, local_path_from_navigation

PREVIEW_MAX = 32_768  # 预览渲染字符上限, 超出截断并提示
# 实测 2.9M 字符全文重建 ~4.4s/次: 每个防抖到期全量重建 = 大文件打字卡死。
# 也别调大: 预览里的 widget 数几乎正比这个窗口, 而 Textual 的模式切换/重排成本
# 就是 ∝ widget 数。4.6MB 文档实测 (2026-09-26, 每键≈进程 CPU, 6s 观察窗):
#   窗口 256KB -> 7817 widget / 模式切换 CSS apply 43262 / 单键 CPU 20-44s
#   窗口  32KB -> 1044 widget / 模式切换 CSS apply  5546 / 单键 CPU 1.2-2.4s
#  (对照: 编辑视图预览隐藏时单键 0.78-1.53s, 即 32KB 已基本把预览成本压平)


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


def _line_starts(text: str) -> list[int]:
    """每行行首的字符下标 (第 i 行 = text[starts[i]:starts[i+1]], 末行到 len(text))。

    增量比对的键要拿"块自己的那段源文本", 而 markdown-it 给的 `source_range` 是
    **行号**。行号当字符下标去切片会错位: 只要这次编辑改了行数 (Ctrl+X 删整行 /
    回车 / 多行粘贴), 被编辑位置以下的每个块切出来的都是错开的字符窗口, 首尾公共段
    比对当场崩掉 → 半篇重建 (实测 321 块测出 拆 160 / 挂 160, 单次 800ms, 这就是
    「Ctrl+X 特别慢」: 删行正是改行数的编辑; 行内打字/退格不改行数, 所以一直不卡)。
    先把行号换成字符下标, 键才是块的真实源文本, 行号平移不再影响比对。
    """
    starts = [0]
    index = text.find("\n")
    while index != -1:
        starts.append(index + 1)
        index = text.find("\n", index + 1)
    return starts


def _block_text(body: str, starts: list[int], start_line: int, end_line: int) -> str:
    """按行号取块源文本; 行号越界 (token.map 缺失 / 末行) 时取空串。"""
    lines = len(starts)
    start = starts[start_line] if 0 <= start_line < lines else len(body)
    end = starts[end_line] if 0 <= end_line < lines else len(body)
    return body[start:end] if end > start else ""


class Preview(Markdown):
    """实时预览面板。"""

    def __init__(self, **kwargs) -> None:
        super().__init__(open_links=False, **kwargs)
        self.can_focus = True  # 预览模式下接管键盘焦点
        self.doc_dir: Path = Path.cwd()
        self._source: str | None = None  # 上次喂给渲染的原文 (同文去重)
        self._body: str | None = None  # 上次真正渲染的归一化文本 (增量比对用)
        self._body_starts: list[int] = []  # _body 的行首下标表 (比对键要用)
        self._parser = MarkdownIt("gfm-like")  # 复用: 每次新建实测要 ~30ms
        self._line_offset = 0  # 截断提示插在正文前的行数 (源码行 → 渲染行)

    def on_markdown_link_clicked(self, event: Markdown.LinkClicked) -> None:
        event.stop()
        open_href(event.href, self.doc_dir, self.app.notify)

    def block_for_line(self, source_line: int) -> tuple[MarkdownBlock, float] | None:
        """源码行 → (命中的渲染块, 块内纵向比例 0..1)；对不上任何块时 None。

        渲染块自带 source_range (markdown-it 的 token.map, 行号与源码逐行对应),
        截断提示插在正文前, 先按 _line_offset 平移。块会嵌套 (列表项里还有段落),
        walk_children 是文档序 → 命中的最后一个即最深那个, 定位更准。
        (实测 walk_children 比 query(MarkdownBlock) 快 30 倍: 0.02ms vs 0.6ms,
          这个方法每次光标移动都要跑, 用 query 会白吃打字延迟)
        """
        target = source_line + self._line_offset
        hit: MarkdownBlock | None = None
        nearest: MarkdownBlock | None = None
        nearest_gap = 0
        for block in self.walk_children(MarkdownBlock):
            start, end = block.source_range
            if end <= start:
                continue  # token.map 缺失的合成块 (source_range 退化为 (0, 0))
            if start <= target < end:
                hit = block
                continue
            # 空行/围栏外的散行不在任何块里: 退到最近那个块 (取其首/尾行)
            gap = start - target if target < start else target - end
            if nearest is None or gap < nearest_gap:
                nearest, nearest_gap = block, gap
        if hit is None:
            hit = nearest
        if hit is None:
            return None
        start, end = hit.source_range
        return hit, min(max((target - start) / (end - start), 0.0), 1.0)

    def update(self, markdown: str):
        """同文去重 + 超长截断 (PREVIEW_MAX) + 归一化, 然后**增量重建**。"""
        if markdown == self._source:
            # 打开时显式渲染过, Changed 防抖又来一次: 同文重建纯属浪费
            return None
        self._source = markdown
        body = markdown
        self._line_offset = 0
        if len(body) > PREVIEW_MAX:
            cut = body[:PREVIEW_MAX].rsplit("\n", 1)[0]
            # 文案必须与文档内容 / 长度无关: 原来的 "{全文} 字符 / 前 {cut} 字符" 每按
            # 一键就变, 于是块复用比对里第一块永远失配 -> 前缀复用失效, 编辑点之前的
            # 所有块被迫重建 (实测 doomed 842/1699 块 = 3963 次挂载 + 10695 次 apply)。
            kb = PREVIEW_MAX // 1024
            notice = (
                f"> ⚠ 文档超过 {kb}KB, "
                f"预览仅渲染前 {kb}KB (编辑与保存不受影响)\n\n"
            )
            body = notice + cut
            self._line_offset = notice.count("\n")
        # 顺序: 先包空格目的地, 再把本地文件/目录链接统一成类型 emoji
        return self._update_incremental(linkify_local_dests(normalize_dests(body),
                                                            self.doc_dir))

    def _top_blocks(self, tokens: list) -> list[tuple[str, int, int, int, int]]:
        """markdown-it token 流 → 顶层块清单: (类型, 起始行, 结束行, 首 token, 尾 token)。

        与 Textual 的 _parse_markdown 建块口径一致 (level 0 的 *_open / hr / fence /
        code_block 各对应一个块), 但**不建控件** — 比对阶段只需要类型 + 源文本切片。
        """
        groups: list[tuple[str, int, int, int, int]] = []
        pending: tuple[str, int, int, int] | None = None
        for index, token in enumerate(tokens):
            if token.level != 0:
                continue
            if token.type in ("hr", "fence", "code_block"):
                start, end = token.map or (0, 0)
                groups.append((token.type, start, end, index, index))
            elif token.type.endswith("_open"):
                start, end = token.map or (0, 0)
                pending = (token.type, start, end, index)
            elif token.type.endswith("_close") and pending is not None:
                groups.append((pending[0], pending[1], pending[2], pending[3], index))
                pending = None
        return groups

    def _update_incremental(self, body: str):
        """只重挂"内容变了的那些块", 其余复用 — 全量重建实测 250-1040ms/次 (241 块)。

        块的渲染只取决于它自己的那段 markdown, 所以拿 (块类型, 该块源文本) 做首尾公共
        段比对 (键里不含行号: 上面插一行会让后面所有块的行号平移, 但内容没变; 复用后
        把新行号刷回去即可)。源文本按行号→字符下标取 (见 `_line_starts`: 直接拿行号
        当字符下标会错位, 改行数的编辑 —— 删整行 / 回车 / 多行粘贴 —— 会退化成半篇重建)。
        比对走 token, 不给没变的块建控件 — 建 241 个块控件本身就要 ~40ms, 白建就等于没省。
        首渲染 / 空文档 / 拿不到旧块时退回 Textual 的全量重建。
        """
        previous, old_blocks = self._body, list(self.children)
        prev_starts = self._body_starts
        self._body = body
        if previous is None or not old_blocks:
            self._body_starts = _line_starts(body)
            return super().update(body)
        tokens = list(self._parser.parse(body))
        groups = self._top_blocks(tokens)
        starts = _line_starts(body)
        self._body_starts = starts
        self._markdown = body
        self._table_of_contents = None

        def old_key(block: MarkdownBlock) -> tuple[str, str]:
            start, end = block.source_range
            return block.name, _block_text(previous, prev_starts, start, end)

        def new_key(group: tuple[str, int, int, int, int]) -> tuple[str, str]:
            name, start, end, _, _ = group
            return name, _block_text(body, starts, start, end)

        old_keys = [old_key(b) for b in old_blocks]
        new_keys = [new_key(g) for g in groups]
        span = min(len(old_keys), len(new_keys))
        head = 0
        while head < span and old_keys[head] == new_keys[head]:
            head += 1
        tail = 0
        while (tail < span - head
               and old_keys[len(old_keys) - 1 - tail] == new_keys[len(new_keys) - 1 - tail]):
            tail += 1

        keep_prefix = old_blocks[:head]
        keep_suffix = old_blocks[len(old_blocks) - tail:] if tail else []
        for old_block, group in zip(keep_prefix, groups[:head]):
            old_block.source_range = (group[1], group[2])  # 行号可能已平移
        for old_block, group in zip(keep_suffix, groups[len(groups) - tail:]):
            old_block.source_range = (group[1], group[2])

        if head == len(old_keys) == len(new_keys):
            return AwaitComplete()  # 内容没变 (例如只差一个行尾换行)

        doomed = old_blocks[head:len(old_blocks) - tail]
        fresh_groups = groups[head:len(groups) - tail]
        fresh = (list(self._parse_markdown(
            tokens[fresh_groups[0][3]:fresh_groups[-1][4] + 1]))
            if fresh_groups else [])
        anchor = keep_suffix[0] if keep_suffix else None

        async def apply() -> None:
            if doomed or fresh:
                with self.app.batch_update():
                    if doomed:
                        await self.remove_children(doomed)
                    if fresh:
                        await self.mount_all(fresh, before=anchor)
            self.post_message(
                Markdown.TableOfContentsUpdated(self, self.table_of_contents)
            )

        return AwaitComplete(apply())
