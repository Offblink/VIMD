"""编辑区 — TextArea 封装。

markdown 语法高亮需要对应 tree-sitter 语法包；装了就用，没装就纯文本
（与 GUI 版持平：QTextEdit 本来也没有编辑区高亮）。
撤销/重做由 TextArea 内置绑定提供 (ctrl+z / ctrl+y, _text_area.py:287)。
"""

from __future__ import annotations

from rich.segment import Segment

from textual.strip import Strip
from textual.widgets import TextArea
from textual.widgets._text_area import LanguageDoesNotExist

from . import formatting


def _markdown_language_or_none() -> str | None:
    """探测 markdown 语法包是否可用；不可用返回 None 而非报错。"""
    try:
        TextArea(language="markdown")
    except LanguageDoesNotExist:
        return None
    return "markdown"


class Editor(TextArea):
    """Markdown 文档编辑区。"""

    def __init__(self, **kwargs) -> None:
        super().__init__(
            language=_markdown_language_or_none(),
            show_line_numbers=True,
            soft_wrap=True,
            **kwargs,
        )

    def on_key(self, event) -> None:
        """TextArea 默认占用的两个键在此拦截:
        ctrl+f = delete_word_right (_text_area.py:266) 会误删文本 → 转交查找;
        ctrl+k = 删到行尾 (TextArea 键位表) → 转交代码块。
        其余键不处理, 基类走独立 _on_key 通道不受影响。"""
        if event.key == "ctrl+f":
            event.prevent_default()
            event.stop()
            self.app.action_find()
        elif event.key == "ctrl+k":
            event.prevent_default()
            event.stop()
            formatting.insert_code_block(self)

    def _watch_show_line_numbers(self) -> None:
        """行号开关: 清渲染缓存 + 栅栏宽度参与布局刷新。"""
        self._line_cache.clear()
        self.refresh(layout=True)

    def render_line(self, y: int) -> Strip:
        """视觉行号: 每个折出来的视觉行都编号 — 软换行后行号自动 +1。

        textual 原生规则是续行留白 (_text_area.py:1436 section_offset==0)。
        y 本身就是视觉行序 (天然含上方所有折行), 所以编号 = y + start。
        super() 走原有缓存, 这里只替换首段 gutter 文本, 保留其样式。
        """
        strip = super().render_line(y)
        if self.show_line_numbers and len(strip):
            # textual Strip 无公开 segments 属性: 迭代取段, 构造新 Strip
            # (宽度不变 -> cell_length 与上游缓存全部保持有效)
            segments = list(strip)
            first = segments[0]
            # 只有真正的 gutter 段才重编号: 原生 gutter 恒为 gutter_width 宽
            # (文档末尾之外的视口行没有 gutter, 盲改会把整行内容段砍塌 -> 边框错乱)
            # y = 屏幕行 (native 用 y_offset = y + scroll_y 定位文档行,
            # 见 _render_line 头) — 行号必须同样加滚动偏移, 否则滚动时冻结 1..N
            if len(first.text) == self.gutter_width:
                width = max(self.gutter_width - 2, 0)
                text = (
                    f"{str(y + int(self.scroll_y) + self.line_number_start):>{width}}  "
                )
                segments[0] = Segment(text, first.style, first.control)
                strip = Strip(segments, strip.cell_length)
        return strip
