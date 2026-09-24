"""模态对话框 — 帮助 / 路径输入 / 退出确认。"""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.events import Click
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static

HELP_TEXT = """\
[bold]VIMD 快捷键[/bold]

  [cyan]视图[/cyan]   F2 编辑 · F3 预览 · F4 分屏
  [cyan]文件[/cyan]   Ctrl+S 保存 · Ctrl+Shift+S 另存为 · Ctrl+O 打开
  [cyan]格式[/cyan]   Ctrl+B 加粗 · Ctrl+I 斜体 · Ctrl+K 代码块 · Ctrl+L 链接 · Ctrl+Shift+L 图片
  [cyan]查找[/cyan]   Ctrl+F 弹窗 · Enter 下一个 · Shift+Enter 上一个
  [cyan]其他[/cyan]   Ctrl+Z/Y 撤销重做 · Ctrl+H 帮助 · Ctrl+Q 退出

[dim]预览滚动: 方向键 / PgDn / Home / End · 弹窗外点一下即关 · 图片链接点开用系统程序[/dim]
"""


class ModalBox(Vertical):
    """弹窗内容盒: 吃掉内部点击 (不冒泡到屏级) — 屏级 on_click 只收盒外点击。"""

    def on_click(self, event: Click) -> None:
        event.stop()


class HelpScreen(ModalScreen[None]):
    """Ctrl+H 帮助。点击盒外关闭。"""

    BINDINGS = [
        Binding("escape", "dismiss_screen", "关闭"),
        Binding("enter", "dismiss_screen", "关闭"),
    ]

    def compose(self) -> ComposeResult:
        with ModalBox(id="help-box"):
            yield Static(HELP_TEXT)
            yield Static("[dim]Enter / Esc 关闭[/dim]", id="help-hint")

    def action_dismiss_screen(self) -> None:
        self.dismiss(None)

    def on_click(self, event: Click) -> None:
        self.action_dismiss_screen()


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
    """退出前的未保存确认: 返回 save / discard / cancel。"""

    BINDINGS = [("escape", "cancel", "取消")]

    def compose(self) -> ComposeResult:
        with ModalBox(id="quit-box"):
            yield Static("文档尚未保存", id="quit-title")
            with Horizontal(id="quit-buttons"):
                yield Button("保存并退出", id="save", variant="primary")
                yield Button("不保存退出", id="discard")
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
