"""Markdown 格式化快捷键 — 加粗 / 斜体 / 代码块 / 链接 / 内嵌图片。

键位 (与 GUI 版对齐, 斜体因终端 Tab 撞车改道):
    Ctrl+B          加粗 **…**
    Alt+I / Ctrl+I  斜体 *…*   (Ctrl+I 在无 kitty 协议的终端 = TAB 字节, 用 Alt+I)
    Ctrl+K          代码块 ```…```
    Ctrl+L          链接 [文本](url)
    Ctrl+Shift+L    内嵌图片 ![ alt ](url)

行为:
- 有选区: 加粗/斜体是切换 (已包裹则解开); 代码块围栏包裹;
  链接/图片用选区做链接文本, 光标落到 url/空括号里
- 无选区: 插入成对标记, 光标落中间
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.document._document import Selection

if TYPE_CHECKING:
    from .editor import Editor


def _offset_of(text: str, row: int, col: int) -> int:
    offset = 0
    for i, line in enumerate(text.split("\n")):
        if i == row:
            return offset + col
        offset += len(line) + 1
    return offset


def _location_of(text: str, offset: int) -> tuple[int, int]:
    row = 0
    col = offset
    for line in text.split("\n"):
        if col <= len(line):
            return row, col
        col -= len(line) + 1
        row += 1
    return row, col


def _selection_span(editor: Editor) -> tuple[int, int]:
    """当前选区的 (起, 止) 偏移; 无选区返回 (cursor, cursor)。

    Selection.start/end 是 锚点/光标 语义, 反向选择时 start > end —
    必须归一化成 小→大, 否则 text[start:end] 取空、包裹落空。
    """
    text = editor.text
    a = _offset_of(text, *editor.selection.start)
    b = _offset_of(text, *editor.selection.end)
    return (a, b) if a <= b else (b, a)


def _replace_span(
    editor: Editor, start: int, end: int, insert: str,
    select: tuple[int, int] | None = None,
    cursor_at: int | None = None,
) -> None:
    """用 insert 替换 [start, end), 然后选中 select 或把光标放 cursor_at。

    偏移均以替换后的文本为基准。
    """
    old = editor.text
    editor.replace(
        insert,
        _location_of(old, start),
        _location_of(old, end),
    )
    new = editor.text
    if select is not None:
        editor.selection = Selection(
            _location_of(new, select[0]), _location_of(new, select[1])
        )
    elif cursor_at is not None:
        loc = _location_of(new, cursor_at)
        editor.selection = Selection(loc, loc)
    editor.scroll_cursor_visible()


def _try_unwrap_outer(
    editor: Editor, start: int, end: int, mark: str
) -> bool:
    """选区(不含标记)外侧正好包着 mark → 拆掉外层标记。"""
    n = len(mark)
    text = editor.text
    if start < n or text[start - n : start] != mark:
        return False
    if text[end : end + n] != mark:
        return False
    if mark == "*" and (
        text[start - 2 : start] == "**" and text[end : end + 2] == "**"
    ):
        return False  # 外层是粗体, 不当斜体拆
    bare = text[start:end]
    _replace_span(
        editor, start - n, end + n, bare,
        select=(start - n, start - n + len(bare)),
    )
    return True


def toggle_bold(editor: Editor) -> None:
    start, end = _selection_span(editor)
    text = editor.text
    if start == end:
        # 无选区: 插入 **** 光标居中
        _replace_span(editor, start, end, "****", cursor_at=start + 2)
        return
    inner = text[start:end]
    if inner.startswith("**") and inner.endswith("**") and len(inner) >= 4:
        # 选区本身含标记 (手动框选)
        bare = inner[2:-2]
        _replace_span(
            editor, start, end, bare,
            select=(start, start + len(bare)),
        )
        return
    if _try_unwrap_outer(editor, start, end, "**"):
        return
    wrapped = f"**{inner}**"
    _replace_span(
        editor, start, end, wrapped,
        select=(start + 2, start + 2 + len(inner)),
    )


def toggle_italic(editor: Editor) -> None:
    start, end = _selection_span(editor)
    text = editor.text
    if start == end:
        _replace_span(editor, start, end, "**", cursor_at=start + 1)
        return
    inner = text[start:end]
    # 选区本身含单星 (手动框选), 但粗体 (**…**) 不当斜体拆
    if (
        inner.startswith("*")
        and inner.endswith("*")
        and len(inner) >= 2
        and not inner.startswith("**")
    ):
        bare = inner[1:-1]
        _replace_span(
            editor, start, end, bare,
            select=(start, start + len(bare)),
        )
        return
    if _try_unwrap_outer(editor, start, end, "*"):
        return
    wrapped = f"*{inner}*"
    _replace_span(
        editor, start, end, wrapped,
        select=(start + 1, start + 1 + len(inner)),
    )


def insert_code_block(editor: Editor) -> None:
    start, end = _selection_span(editor)
    text = editor.text
    if start == end:
        # 无选区: 三反引号 + 空行 + 三反引号, 光标在空行
        block = "```\n\n```"
        _replace_span(editor, start, end, block, cursor_at=start + 4)
    else:
        inner = text[start:end]
        block = f"```\n{inner}\n```"
        after = start + len(block)
        _replace_span(editor, start, end, block, cursor_at=after)


def insert_link(editor: Editor) -> None:
    start, end = _selection_span(editor)
    text = editor.text
    if start == end:
        _replace_span(editor, start, end, "[]()", cursor_at=start + 1)
    else:
        inner = text[start:end]
        block = f"[{inner}]()"
        _replace_span(
            editor, start, end, block, cursor_at=start + len(block) - 1
        )


def insert_image(editor: Editor) -> None:
    start, end = _selection_span(editor)
    text = editor.text
    if start == end:
        _replace_span(editor, start, end, "![]()", cursor_at=start + 2)
    else:
        inner = text[start:end]
        block = f"![{inner}]()"
        _replace_span(
            editor, start, end, block, cursor_at=start + len(block) - 1
        )
