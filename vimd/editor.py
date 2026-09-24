"""编辑区 — TextArea 封装。

markdown 语法高亮需要对应 tree-sitter 语法包；装了就用，没装就纯文本
（与 GUI 版持平：QTextEdit 本来也没有编辑区高亮）。
撤销/重做由 TextArea 内置绑定提供 (ctrl+z / ctrl+y, _text_area.py:287)。
"""

from __future__ import annotations

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
            soft_wrap=False,
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
