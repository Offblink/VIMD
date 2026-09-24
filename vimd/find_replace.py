"""查找与替换 — 居中弹窗 (ModalScreen) + 可独立调用的查找函数。

弹窗内键位: Enter(查找框)=下一个 · Enter(替换框)=全部替换 ·
F6=区分大小写 · Esc=关闭。查询状态存在 app.find_state —
关掉弹窗后 Ctrl+G / Ctrl+Shift+G 仍按最后的查询继续跳转。

与 GUI 版语义对齐 (下一个/上一个循环、实时计数); 替换逐处调用
TextArea.replace, 每处是独立 undo 批次 (已知简化)。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.document._document import Selection
from textual.events import Click
from textual.screen import ModalScreen
from textual.content import Content
from textual.style import Style
from textual.widgets import Button, Checkbox, Input, Static

from .dialogs import ModalBox
from .editor import Editor


@dataclass
class FindState:
    """跨弹窗存续的查找状态 (由 app 持有)。"""

    query: str = ""
    replace: str = ""
    case: bool = False


def _offset_of(text: str, row: int, col: int) -> int:
    """(行, 列) → 全文偏移。"""
    offset = 0
    for i, line in enumerate(text.split("\n")):
        if i == row:
            return offset + col
        offset += len(line) + 1
    return offset


def _location_of(text: str, offset: int) -> tuple[int, int]:
    """全文偏移 → (行, 列)。"""
    row = 0
    col = offset
    for line in text.split("\n"):
        if col <= len(line):
            return row, col
        col -= len(line) + 1
        row += 1
    return row, col


def _matches(text: str, state: FindState) -> list[tuple[int, int]]:
    """state.query 在 text 中全部匹配的 (起, 止) 偏移列表。"""
    if not state.query:
        return []
    flags = 0 if state.case else re.IGNORECASE
    return [
        m.span() for m in re.finditer(re.escape(state.query), text, flags)
    ]


def find_next(editor: Editor, state: FindState, backward: bool = False,
              notify=None) -> None:
    """选中下一个/上一个匹配, 到头循环。"""
    matches = _matches(editor.text, state)
    if not matches:
        if state.query and notify:
            notify("找不到: " + state.query)
        return
    cursor = _offset_of(editor.text, *editor.cursor_location)
    if backward:
        before = [m for m in matches if m[1] <= cursor]
        start, end = before[-1] if before else matches[-1]
    else:
        after = [m for m in matches if m[0] >= cursor]
        start, end = after[0] if after else matches[0]
    text = editor.text
    editor.selection = Selection(
        _location_of(text, start), _location_of(text, end)
    )
    editor.scroll_cursor_visible()


def replace_all(editor: Editor, state: FindState, notify=None) -> None:
    """全部替换 (从后往前, 前面匹配的偏移不受影响)。"""
    matches = _matches(editor.text, state)
    if not matches:
        if notify:
            notify("没有可替换的匹配")
        return
    text = editor.text
    for start, end in reversed(matches):
        editor.replace(
            state.replace,
            _location_of(text, start),
            _location_of(text, end),
        )
    if notify:
        notify(f"已替换 {len(matches)} 处")


def replace_current(editor: Editor, state: FindState, notify=None) -> None:
    """替换当前选中匹配 (须恰好选中一处) 后选中下一处;
    未选中匹配时仅定位下一处 — 连点按钮 = 逐个走过并替换。"""
    matches = _matches(editor.text, state)
    if not matches:
        if notify:
            notify("没有可替换的匹配")
        return
    text = editor.text
    a = _offset_of(text, *editor.selection.start)
    b = _offset_of(text, *editor.selection.end)
    sel = (a, b) if a <= b else (b, a)
    if sel in matches:
        editor.replace(
            state.replace,
            _location_of(text, sel[0]),
            _location_of(text, sel[1]),
        )
        if not _matches(editor.text, state):
            if notify:
                notify("已替换全部匹配")
            return
    find_next(editor, state, notify=notify)


class CaseCheckbox(Checkbox):
    """区分大小写勾选框: 开 = √ (success 绿), 关 = 留空。

    ToggleButton 的 BUTTON_INNER 恒为 "X", 两种状态只换颜色 —
    这里按状态换字形: 关态不显示叉, 直接留空。
    """

    @property
    def _button(self) -> Content:
        button_style = self.get_visual_style("toggle--button")
        side_style = Style(
            foreground=button_style.background,
            background=self.background_colors[1],
        )
        inner = "√" if self.value else " "
        return Content.assemble(
            (self.BUTTON_LEFT, side_style),
            (inner, button_style),
            (self.BUTTON_RIGHT, side_style),
        )


class FindScreen(ModalScreen[None]):
    """居中的查找/替换弹窗。"""

    CSS = """
    #find-box {
        width: 64; height: auto; max-width: 90%; max-height: 90%;
        padding: 1 2; background: $surface; border: thick $accent;
        margin: 0 1 3 0;
    }
    #find-box Input { width: 100%; margin-top: 1; }
    #case-check { margin-top: 1; }
    #find-buttons { height: 3; margin-top: 1; }
    #find-buttons Button { margin-right: 1; }
    #find-count { margin-top: 1; color: $text-muted; }
    #find-hint { margin-top: 1; color: $text-muted; }
    """
    BINDINGS = [
        ("escape", "close", "关闭"),
        ("shift+enter", "find_prev", "上一个"),
    ]

    def compose(self) -> ComposeResult:
        with ModalBox(id="find-box"):
            yield Static("[bold]查找与替换[/bold]", id="find-title")
            yield Input(placeholder="查找 (Enter 下一个)", id="find-input")
            yield Input(placeholder="替换", id="replace-input")
            yield CaseCheckbox("区分大小写", id="case-check")
            yield Static("", id="find-count")
            with Horizontal(id="find-buttons"):
                yield Button("替换当前", id="replace-one")
                yield Button("全部替换", id="replace-all", variant="primary")
            yield Static(
                "[dim]Shift+Enter 上一个 · Esc 关闭[/dim]",
                id="find-hint",
            )

    @property
    def state(self) -> FindState:
        return self.app.find_state

    @property
    def editor(self) -> Editor:
        return self.app.query_one(Editor)

    def on_mount(self) -> None:
        state = self.state
        find_input = self.query_one("#find-input", Input)
        replace_input = self.query_one("#replace-input", Input)
        case_check = self.query_one("#case-check", Checkbox)
        find_input.value = state.query
        replace_input.value = state.replace
        if case_check.value != state.case:
            case_check.value = state.case
        # 有单行选区时用它做初始查询
        selection = self.editor.selected_text
        if selection and "\n" not in selection and not state.query:
            find_input.value = selection
            state.query = selection
        find_input.focus()
        self.refresh_count()

    def refresh_count(self) -> None:
        count = len(_matches(self.editor.text, self.state))
        label = f"{count} 处匹配" if self.state.query else ""
        if self.state.case and label:
            label += "（区分大小写）"
        self.query_one("#find-count", Static).update(label)

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "find-input":
            self.state.query = event.value
        elif event.input.id == "replace-input":
            self.state.replace = event.value
        self.refresh_count()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "find-input":
            find_next(self.editor, self.state, notify=self.app.notify)
        elif event.input.id == "replace-input":
            replace_all(self.editor, self.state, notify=self.app.notify)

    @on(Checkbox.Changed, "#case-check")
    def case_changed(self, event: Checkbox.Changed) -> None:
        self.state.case = event.value
        self.refresh_count()

    @on(Button.Pressed, "#replace-one")
    def press_replace_one(self) -> None:
        replace_current(self.editor, self.state, notify=self.app.notify)
        self.refresh_count()

    @on(Button.Pressed, "#replace-all")
    def press_replace_all(self) -> None:
        replace_all(self.editor, self.state, notify=self.app.notify)
        self.refresh_count()

    def action_find_prev(self) -> None:
        find_next(self.editor, self.state, backward=True, notify=self.app.notify)

    def action_close(self) -> None:
        self.dismiss(None)

    def on_click(self, event: Click) -> None:
        # 点盒外空白关闭 (盒内点击被 ModalBox.stop 吃掉, 到不了这里)
        self.action_close()
