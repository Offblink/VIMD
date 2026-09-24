"""模态对话框 — 帮助 / 路径输入 / 退出确认。"""

from __future__ import annotations

from pathlib import Path

from textual import on
from textual.app import ComposeResult
from textual.events import Click
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.content import Content
from textual.style import Style
from textual.widgets import Button, Checkbox, Input, Static

HELP_TEXT = """\
[bold]VIMD 快捷键[/bold]

  [cyan]视图[/cyan]   F2 编辑 · F3 预览 · F4 分屏
  [cyan]文件[/cyan]   Ctrl+N 新建 · Ctrl+O 打开 · Ctrl+S 保存 · Ctrl+Shift+S 另存为
  [cyan]格式[/cyan]   Ctrl+B 加粗 · Ctrl+I 斜体 · Ctrl+K 代码块
          Ctrl+L 链接 · Ctrl+Shift+L 图片
  [cyan]查找[/cyan]   Ctrl+F 弹窗 · Enter 下一个 · Shift+Enter 上一个
  [cyan]其他[/cyan]   Ctrl+Z/Y 撤销重做 · Ctrl+H 帮助 · Ctrl+Q 退出

[dim]预览滚动: 方向键 / PgDn / Home / End · 弹窗外点一下即关 · 图片链接点开用系统程序[/dim]
"""


class ModalBox(Vertical):
    """弹窗内容盒: 吃掉内部点击 (不冒泡到屏级) — 屏级 on_click 只收盒外点击。"""

    def on_click(self, event: Click) -> None:
        event.stop()


class CaseCheckbox(Checkbox):
    """开关勾选框: 开 = √ (success 绿), 关 = 留空。

    ToggleButton 的 BUTTON_INNER 恒为 "X", 两种状态只换颜色 —
    这里按状态换字形 (大小写 / 显示行号共用此 UX)。
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


class HelpScreen(ModalScreen[None]):
    """Ctrl+H 帮助。点击盒外关闭。"""

    BINDINGS = [
        Binding("escape", "dismiss_screen", "关闭"),
        Binding("enter", "dismiss_screen", "关闭"),
    ]

    def compose(self) -> ComposeResult:
        with ModalBox(id="help-box"):
            yield Static(HELP_TEXT)
            yield CaseCheckbox("显示行号", id="line-check")
            yield Static("[dim]Enter / Esc 关闭[/dim]", id="help-hint")

    def action_dismiss_screen(self) -> None:
        self.dismiss(None)

    def on_click(self, event: Click) -> None:
        self.action_dismiss_screen()

    def on_mount(self) -> None:
        box = self.query_one("#line-check", Checkbox)
        if box.value != self.app.show_line_numbers:
            box.value = self.app.show_line_numbers

    @on(Checkbox.Changed, "#line-check")
    def line_toggled(self, event: Checkbox.Changed) -> None:
        self.app.set_line_numbers(event.value)


class PathPrompt(ModalScreen[str | None]):
    """打开 / 另存为的路径输入。"""

    BINDINGS = [("escape", "cancel", "取消")]

    def __init__(self, title: str, initial: str = "") -> None:
        super().__init__()
        self.title_text = title
        self.initial = initial

    def compose(self) -> ComposeResult:
        with ModalBox(id="prompt-box"):
            yield Static(self.title_text, id="prompt-title")
            yield Input(value=self.initial, id="path-input")
            yield Static("[dim]Enter 确认 · Esc 取消[/dim]")

    def on_mount(self) -> None:
        self.query_one("#path-input", Input).focus()

    @on(Input.Submitted, "#path-input")
    def submit(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip() or None)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_click(self, event: Click) -> None:
        self.action_cancel()


class QuitConfirm(ModalScreen[str]):
    """退出/新建前的未保存确认: 返回 save / discard / cancel。"""

    BINDINGS = [("escape", "cancel", "取消")]

    def __init__(self, purpose: str = "quit") -> None:
        super().__init__()
        self.purpose = purpose  # "quit" (默认) | "new"

    def compose(self) -> ComposeResult:
        new = self.purpose == "new"
        with ModalBox(id="quit-box"):
            yield Static("文档尚未保存", id="quit-title")
            with Horizontal(id="quit-buttons"):
                yield Button(
                    "保存并新建" if new else "保存并退出",
                    id="save",
                    variant="primary",
                )
                yield Button(
                    "不保存新建" if new else "不保存退出", id="discard"
                )
                yield Button("取消", id="cancel")

    @on(Button.Pressed)
    def press(self, event: Button.Pressed) -> None:
        self.dismiss({"save": "save", "discard": "discard",
                      "cancel": "cancel"}[event.button.id])

    def action_cancel(self) -> None:
        self.dismiss("cancel")

    def on_click(self, event: Click) -> None:
        # 点盒外 = 取消退出, 绝不误触保存/丢弃
        self.action_cancel()


class RecoveryPrompt(ModalScreen[str]):
    """上次未正常退出 (含被窗口×强杀) -> 恢复未保存内容?

    返回: "restore" / "discard" / "" (Esc 或点盒外 = 稍后, 下次再问)。
    """

    BINDINGS = [("escape", "later", "稍后")]

    def __init__(self, source_path: str) -> None:
        super().__init__()
        self.source_path = source_path

    def compose(self) -> ComposeResult:
        shown = (
            Path(self.source_path).name if self.source_path else "未命名"
        )
        with ModalBox(id="recover-box"):
            yield Static("[bold]发现未保存的更改[/bold]", id="recover-title")
            yield Static(f"来源: {shown}", id="recover-src")
            yield Static("上次会话未正常退出，是否恢复？", id="recover-ask")
            with Horizontal(id="recover-buttons"):
                yield Button("恢复", id="restore", variant="primary")
                yield Button("丢弃", id="discard")
                yield Button("稍后", id="later")

    @on(Button.Pressed)
    def press(self, event: Button.Pressed) -> None:
        mapping = {"restore": "restore", "discard": "discard", "later": ""}
        self.dismiss(mapping.get(event.button.id, ""))

    def on_click(self, event: Click) -> None:
        # 点盒外 = 稍后 (保留恢复文件, 下次启动再问)
        self.dismiss("")

    def action_later(self) -> None:
        self.dismiss("")
