"""模态对话框 — 帮助 / 路径输入 / 退出确认。"""

from __future__ import annotations

from pathlib import Path

from rich.cells import cell_len
from textual import on
from textual.app import ComposeResult
from textual.events import Click, Paste
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.content import Content
from textual.style import Style
from textual.widgets import Button, Checkbox, Input, Static

# 快捷键一览: (分组, [(键, 说明), ...]) — 一行一条, 键列按单元格宽度对齐
# (键里有 ↑/↓ 这类箭头, 另存为这类中文说明在右边, 用 len() 对齐会错位)
HELP_KEYS: list[tuple[str, list[tuple[str, str]]]] = [
    ("视图", [("Alt+1", "编辑模式"), ("Alt+2", "预览模式"), ("Alt+3", "分屏模式")]),
    ("文件", [("Ctrl+N", "新建"), ("Ctrl+O", "打开"), ("Ctrl+S", "保存"),
              ("Ctrl+Shift+S", "另存为")]),
    ("编辑", [("Ctrl+A", "全选"), ("Ctrl+Z", "撤销"), ("Ctrl+Y", "重做"),
              ("Ctrl+F", "查找 / 替换"), ("Ctrl+G", "查找下一个"),
              ("Shift+Enter", "查找上一个")]),
    ("格式", [("Ctrl+B", "加粗"), ("Ctrl+I", "斜体"), ("Ctrl+K", "代码块"),
              ("Ctrl+L", "链接"), ("Ctrl+Shift+L", "内嵌图片")]),
    ("其他", [("Alt+H", "帮助"), ("Alt+L", "显示行号"), ("Alt+Q", "退出"),
              ("Enter / Esc", "关闭弹窗")]),
]

# 底部说明: 一行一条, 说明列与上面的键表**同一列** (整块看起来才是一张表)
# 每条都要能在一行里放下 (终端宽度不够时才会自动折行)
HELP_NOTES: list[tuple[str, str]] = [
    ("预览滚动", "方向键 / PgDn / Home / End"),
    ("预览跟随", "跟着光标把预览推到最底; 滚开预览即停跟, 滚回底部恢复"),
    ("缩放", "终端自带 Ctrl+= / Ctrl+- / Ctrl+0 (不记住)"),
    ("图片 / 链接", "点击用系统默认程序打开"),
]


def _key_column_width() -> int:
    """键列的显示宽度 (按最宽的键算, 用单元格数; 键里有 ↑/↓ 这类箭头)。"""
    return max(cell_len(key) for _, items in HELP_KEYS for key, _ in items)


def help_keys_text() -> str:
    """把 HELP_KEYS 排成一行一条的对齐文本 (键列按最宽的单元格对齐)。"""
    width = _key_column_width()
    lines = ["[bold]VIMD 快捷键[/bold]", ""]
    for group, items in HELP_KEYS:
        lines.append(f"[cyan]{group}[/cyan]")
        for key, desc in items:
            lines.append(f"  {key}{' ' * (width - cell_len(key))}  {desc}")
    return "\n".join(lines)


def help_notes_text() -> str:
    """把 HELP_NOTES 排成一行一条, **说明列与键表同一列** (两块共用一张表)。"""
    width = _key_column_width()
    lines = [f"  {label}{' ' * (width - cell_len(label))}  {note}"
             for label, note in HELP_NOTES]
    return "\n[dim]" + "\n".join(lines) + "[/dim]"


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
    """Alt+H 帮助。点击盒外关闭。"""

    BINDINGS = [
        Binding("escape", "dismiss_screen", "关闭"),
        Binding("enter", "dismiss_screen", "关闭"),
    ]

    def on_unmount(self) -> None:
        """关窗把焦点还给编辑器 — 从底栏芯片点开时焦点在按钮上,
        不还回去后续打字全喂给按钮 (2026-09-26 pilot 实测踩过)。"""
        editors = self.app.query("#editor")
        if editors:
            editors[0].focus()

    def compose(self) -> ComposeResult:
        with ModalBox(id="help-box"):
            # 内容比盒子高: 一行一条的快捷键列表放进可滚动容器 (方向键/PgDn 滚动),
            # 提示钉在盒底, 滚到哪都能看到 (行号/光标样式已移到「设置」窗)
            with VerticalScroll(id="help-scroll"):
                yield Static(help_keys_text())
                yield Static(help_notes_text())
            # 提示也一行一条, 跟上面的键表统一
            yield Static("[dim]Enter / Esc 关闭\n方向键 / PgDn 滚动[/dim]", id="help-hint")

    def action_dismiss_screen(self) -> None:
        self.dismiss(None)

    def on_click(self, event: Click) -> None:
        self.action_dismiss_screen()

    def on_mount(self) -> None:
        self.query_one("#help-scroll", VerticalScroll).focus()


class SettingsScreen(ModalScreen[None]):
    """设置 (底栏「设置」芯片): 显示行号。点击盒外关闭。

    行号开关原在帮助窗底 — 用户要求「提取出来放设置窗, 按钮常驻左下角帮助右边」
    (2026-09-26)。光标相关设置项做过后按用户要求整体回退 (终端一格一字形的
    约束下没有满意形态), 这里只留行号。
    """

    BINDINGS = [
        Binding("escape", "dismiss_screen", "关闭"),
        Binding("enter", "dismiss_screen", "关闭"),
    ]

    def compose(self) -> ComposeResult:
        with ModalBox(id="settings-box"):
            yield CaseCheckbox("显示行号", id="line-check")
            yield Static("[dim]Enter / Esc 关闭[/dim]", id="settings-hint")

    def action_dismiss_screen(self) -> None:
        self.dismiss(None)

    def on_unmount(self) -> None:
        """关窗把焦点还给编辑器 — 「设置」芯片点击会把焦点挪到按钮上,
        不还回去后续打字全喂给按钮 (2026-09-26 pilot 实测踩过)。"""
        editors = self.app.query("#editor")
        if editors:
            editors[0].focus()

    def on_click(self, event: Click) -> None:
        self.action_dismiss_screen()

    def on_mount(self) -> None:
        box = self.query_one("#line-check", Checkbox)
        if box.value != self.app.show_line_numbers:
            box.value = self.app.show_line_numbers

    @on(Checkbox.Changed, "#line-check")
    def line_toggled(self, event: Checkbox.Changed) -> None:
        self.app.set_line_numbers(event.value)


class PathInput(Input):
    """路径输入框: 拖入 WT 的带引号路径落地前去引号。"""

    async def on_event(self, event) -> None:
        # 同 Editor: 覆写 _on_paste 再调 super 会被 MRO 派发 + 自己那次插两遍
        from .io import unquote_dropped_path

        if isinstance(event, Paste):
            event.text = unquote_dropped_path(event.text)
        await super().on_event(event)


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
            yield PathInput(value=self.initial, id="path-input")
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
