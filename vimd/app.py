"""VIMD 应用主体 — 三视图、状态栏、文件生命周期、快捷键调度。

架构:
    Editor (TextArea) ──Changed──> 防抖 ──> Preview (Markdown)
    内置 io/links 纯逻辑 (源自 MDPad v2, 零 Qt 依赖)

视图模式 (持久化到 %APPDATA%/VIMD/settings.json):
    F2 编辑 · F3 预览 · F4 分屏
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Button, Static

from .io import FileGuard, read_text_file, write_text_file

from . import formatting
from .dialogs import HelpScreen, PathPrompt, QuitConfirm, RecoveryPrompt
from .editor import Editor
from .find_replace import FindState, FindScreen, find_next as _find_next
from .preview import Preview
from .sysdialog import system_open_file_dialog, system_save_file_dialog

MODES = ("edit", "preview", "split")
PREVIEW_DEBOUNCE = 0.2  # 秒; 与 GUI 版 v1.3.0 同思路: 连续输入合并渲染


def _settings_path() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / "VIMD" / "settings.json"


def _recovery_path() -> Path:
    """脏状态恢复日志 — 被强杀(窗口×)后下次启动据此弹恢复框。"""
    return _settings_path().parent / "recovery.json"


class TitleRow(Horizontal):
    """标题行: ⭘ + VIMD 居中, 整行可点 -> 打开帮助 (复刻原 Header 手感)。"""

    def on_click(self, event) -> None:
        event.stop()
        self.app.action_show_help()


class VIMDApp(App):
    """VIMD 应用 (VIM + Markdown)。"""

    TITLE = "VIMD"
    # 未注册任何命令, 面板是空的; 关掉同时移除 footer 的 ^p 行
    ENABLE_COMMAND_PALETTE = False
    CSS = """
    #body { height: 1fr; }
    #body > Editor, #preview-scroll { width: 100%; }
    #preview-scroll > Preview { width: 100%; }
    #body.m-split > Editor, #body.m-split > #preview-scroll { width: 50%; }
    #topbar {
        height: 1; background: $panel;
        color: $text-muted; padding: 0 1;
    }
    #title-row {
        height: 1; background: $panel; color: $text-muted;
    }
    #title-row #tr-icon { width: 3; }
    #title-row #tr-title { width: 1fr; text-align: center; color: $text; }
    #title-row #tr-pad { width: 3; }
    #tb-name { width: 1fr; }
    #botbar {
        height: 1; dock: bottom; background: $panel;
        color: $text-muted; padding: 0 1;
    }
    #gap { width: 1fr; }
    #preview-scroll {
        border: tall $border-blurred;
    }
    #preview-scroll:focus {
        border: tall $border;
    }
    #botbar Button {
        height: 1; min-width: 0;
        background: transparent; color: $text-muted;
        border: none; text-align: left;
    }
    #botbar Button:hover { color: $text; background: $panel; }
    #help-box Static { text-wrap: wrap; }
    HelpScreen, PathPrompt, QuitConfirm { align: center middle; }
    FindScreen { align: right bottom; }
    ToastRack { dock: top; align: right top; }
    #help-box, #prompt-box, #quit-box, #recover-box {
        width: 76; height: auto; max-height: 90%;
        padding: 1 2; background: $surface; border: thick $accent;
    }
    #quit-box { width: 60; }
    #help-hint, #quit-title { margin-top: 1; }
    #quit-buttons { height: 3; margin-top: 1; }
    #quit-buttons Button { margin: 0 1; }
    """
    BINDINGS = [
        # 文件/编辑/查找键照常生效但不在 footer 展示:
        # 只留 5 个可见项, 窄终端也排得开
        Binding("ctrl+s", "save", "保存", show=False),
        Binding("ctrl+shift+s", "save_as", "另存为", show=False),
        Binding("ctrl+o", "open", "打开", show=False),
        Binding("ctrl+f", "find", "查找", show=False),
        Binding("ctrl+g", "find_next", "下一个", show=False),
        Binding("ctrl+b", "format_bold", "加粗", show=False),
        Binding("ctrl+i", "format_italic", "斜体", show=False),
        Binding("alt+i", "format_italic", "斜体", show=False),
        Binding("ctrl+k", "format_code", "代码块", show=False),
        Binding("ctrl+l", "format_link", "链接", show=False),
        Binding("ctrl+shift+l", "format_image", "图片", show=False),
        Binding("ctrl+h", "show_help", "帮助"),
        Binding("f2", "mode_edit", "编辑"),
        Binding("f3", "mode_preview", "预览"),
        Binding("f4", "mode_split", "分屏"),
        Binding("ctrl+q", "request_quit", "退出"),
    ]

    def __init__(self, path: str | None = None) -> None:
        super().__init__()
        self.path_arg = path
        self.file_path: Path | None = None
        self.file_guard = FileGuard()
        self._saved_text = ""
        self._mode = "edit"
        self._preview_gen = 0  # 防抖代数计数
        self.find_state = FindState()  # 查找状态跨弹窗存续
        self.show_line_numbers = True  # 帮助弹窗内可切换 (CaseCheckbox 同款 UX)
        self._recovery_gen = 0
        self._recovery_data: dict | None = None

    # ── 组装 ────────────────────────────────────────────────
    def compose(self) -> ComposeResult:
        with TitleRow(id="title-row"):
            yield Static("⭘", id="tr-icon")
            yield Static("VIMD", id="tr-title")
            yield Static("", id="tr-pad")
        with Horizontal(id="topbar"):
            yield Static("", id="tb-name")
        with Horizontal(id="body"):
            yield Editor(id="editor")
            with VerticalScroll(id="preview-scroll"):
                yield Preview(id="preview")
        with Horizontal(id="botbar"):
            yield Button("Ctrl+H 帮助", id="hint-h", compact=True)
            yield Button("F2 编辑", id="hint-2", compact=True)
            yield Button("F3 预览", id="hint-3", compact=True)
            yield Button("F4 分屏", id="hint-4", compact=True)
            yield Static("", id="gap")
            yield Button("", id="meta", compact=True)

    def on_mount(self) -> None:
        # 终端标签/窗口标题 = VIMD。
        # 实测: 写 OSC2 到 sys.stdout 会被 textual 驱动吞掉 (输出抓包无 ]2;)。
        # 改走 SetConsoleTitleW — cmd 的 title 命令即此 API,
        # ConPTY 再转成 OSC2 送达 Windows Terminal 标签。
        if os.name == "nt":
            import ctypes

            ctypes.windll.kernel32.SetConsoleTitleW("VIMD")
        mode = self._load_mode()
        if self.path_arg:
            p = Path(self.path_arg)
            if p.exists():
                self._open_file(p)
            else:
                # 新文件: 记住路径, 保存时创建
                self.file_path = p
                self.query_one(Preview).doc_dir = p.parent
        self._apply_mode(mode)
        self.refresh_status()
        self._check_recovery()

    # ── 设置持久化 ──────────────────────────────────────────
    def _load_mode(self) -> str:
        try:
            data = json.loads(_settings_path().read_text(encoding="utf-8"))
            mode = data.get("mode")
            if mode in MODES:
                return mode
        except (OSError, ValueError):
            pass
        return "split"  # 首启默认分屏: 打开即见预览渲染

    def _store_mode(self) -> None:
        try:
            path = _settings_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps({"mode": self._mode}), encoding="utf-8"
            )
        except OSError:
            pass  # 设置写不进去不影响编辑

    # ── 视图模式 ────────────────────────────────────────────
    # ── 恢复日志 (脏内容保命: 窗口×杀不死它) ────────────────
    def _clear_recovery(self) -> None:
        try:
            _recovery_path().unlink()
        except OSError:
            pass

    def _sync_recovery_if(self, gen: int) -> None:
        """防抖到期仍是脏态 -> 落盘恢复文件; 已干净 -> 删除。"""
        if gen != self._recovery_gen:
            return
        text = self.query_one(Editor).text
        if text != self._saved_text:
            payload = {
                "path": str(self.file_path) if self.file_path else "",
                "content": text,
            }
            try:
                _recovery_path().write_text(
                    json.dumps(payload, ensure_ascii=False), encoding="utf-8"
                )
            except OSError:
                pass
        else:
            self._clear_recovery()

    def _check_recovery(self) -> None:
        rp = _recovery_path()
        if not rp.exists():
            return
        try:
            data = json.loads(rp.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        content = data.get("content", "")
        if content == self.query_one(Editor).text:
            self._clear_recovery()  # 内容一致 = 没有真正丢失的东西
            return
        # 缓存在内存: 回调时文件可能已被启动流程清掉, 不再二次读盘
        self._recovery_data = data
        self.push_screen(
            RecoveryPrompt(data.get("path", "")), self._on_recovery_choice
        )

    def _on_recovery_choice(self, choice: str) -> None:
        if choice == "restore":
            data = getattr(self, "_recovery_data", None)
            if not data:
                try:
                    data = json.loads(
                        _recovery_path().read_text(encoding="utf-8")
                    )
                except (OSError, ValueError):
                    self.notify("恢复数据不存在", severity="warning")
                    return
            self.query_one(Editor).text = data.get("content", "")
            self.notify("已恢复未保存的内容 (记得保存)")
        elif choice == "discard":
            self._clear_recovery()
        # "" (Esc/点外) = 稍后再问, 文件保留, 下次启动再弹

    def _apply_mode(self, mode: str) -> None:
        self._mode = mode
        body = self.query_one("#body", Horizontal)
        for m in MODES:
            body.remove_class("m-" + m)
        body.add_class("m-" + mode)
        editor = self.query_one(Editor)
        scroll = self.query_one("#preview-scroll", VerticalScroll)
        editor.display = mode in ("edit", "split")
        scroll.display = mode in ("preview", "split")
        if mode == "preview":
            scroll.focus()  # ScrollableContainer 自带方向键/Home/End/PgUp/PgDn
        else:
            editor.focus()
        self._store_mode()
        self._render_preview_now()  # 切到预览立即刷新, 不等防抖
        self.refresh_status()

    def action_mode_edit(self) -> None:
        self._apply_mode("edit")

    def action_mode_preview(self) -> None:
        self._apply_mode("preview")

    def action_mode_split(self) -> None:
        self._apply_mode("split")

    # ── 预览防抖 ────────────────────────────────────────────
    def on_text_area_changed(self, event) -> None:
        del event
        self._preview_gen += 1
        gen = self._preview_gen
        self.set_timer(PREVIEW_DEBOUNCE, lambda: self._render_if(gen))
        self._recovery_gen += 1
        rgen = self._recovery_gen
        self.set_timer(0.5, lambda: self._sync_recovery_if(rgen))
        self.refresh_status()

    def on_text_area_selection_changed(self, event) -> None:
        del event
        self.refresh_status()

    def _render_if(self, gen: int) -> None:
        if gen == self._preview_gen:
            self._render_preview_now()

    def _render_preview_now(self) -> None:
        self.query_one(Preview).update(self.query_one(Editor).text)

    # ── 状态栏 ──────────────────────────────────────────────
    @on(Button.Pressed)
    def botbar_pressed(self, event: Button.Pressed) -> None:
        """底行左侧键位芯片可点击; id=meta 无映射 → 按压动画有、动作无。

        不带选择器: @on 的选择器按发送者匹配, "#botbar"(容器) 永远不中。
        其它弹窗的按钮 id 不在映射里 → 无操作, 天然互不干扰。
        """
        actions = {
            "hint-h": self.action_show_help,
            "hint-2": self.action_mode_edit,
            "hint-3": self.action_mode_preview,
            "hint-4": self.action_mode_split,
        }
        action = actions.get(event.button.id)
        if action is not None:
            action()

    def set_line_numbers(self, visible: bool) -> None:
        self.show_line_numbers = visible
        self.query_one(Editor).show_line_numbers = visible

    def refresh_status(self) -> None:
        editor = self.query_one(Editor)
        _, col = editor.cursor_location
        # 软换行下右下行号也按视觉行走 (与 gutter 编号一致)
        _, vis_y = editor.wrapped_document.location_to_offset(
            editor.cursor_location
        )
        name = self.file_path.name if self.file_path else "未命名"
        dirty = "● " if editor.text != self._saved_text else ""
        self.query_one("#tb-name", Static).update(f" {dirty}{name}")
        meta = self.query_one("#meta", Button)
        meta_label = f"{self._mode}  {vis_y + 1}:{col + 1}"
        meta.label = meta_label
        # 实测 Button 宽度锁在挂载值不随 label 重排 -> 显式数字定宽
        # (meta 文案全 ASCII, 字符数 = 单元格数; +3 = 左右内边距)
        meta.styles.width = len(meta_label) + 3

    # ── 文件操作 ────────────────────────────────────────────
    def _open_file(self, path: Path) -> None:
        content = read_text_file(path)
        self.file_path = path
        self.file_guard.acquire(str(path))
        editor = self.query_one(Editor)
        editor.text = content
        self._saved_text = content
        # 加载触发的 Changed 会排一个"文本==已存 -> 删恢复文件"的定时器,
        # 会把待恢复的日志误删 — 作废它
        self._recovery_gen += 1
        preview = self.query_one(Preview)
        preview.doc_dir = path.parent
        preview.update(content)
        self.refresh_status()
        self.notify(f"已打开 {path.name}")

    async def action_open(self) -> None:
        ok, value = await system_open_file_dialog()
        if not ok:
            initial = str(
                self.file_path.parent if self.file_path else Path.cwd()
            )
            self.notify("系统对话框不可用, 改用内置输入", severity="warning")
            self.push_screen(
                PathPrompt("打开文件", initial), self._on_open_path
            )
        elif value:
            self._on_open_path(value)
        # ok 且空 = 用户取消

    def _on_open_path(self, value: str | None) -> None:
        if not value:
            return
        path = Path(value)
        if path.is_dir():
            path = path / "未命名.md"
        if path.is_file():
            self._open_file(path)
        else:
            self.notify(f"文件不存在: {path}", severity="warning")

    async def action_save(self) -> None:
        if self.file_path is None:
            await self.action_save_as()
            return
        text = self.query_one(Editor).text
        write_text_file(self.file_path, text)
        self._saved_text = text
        self._clear_recovery()
        self.refresh_status()
        self.notify(f"已保存 {self.file_path.name}")

    async def action_save_as(self) -> None:
        default = self.file_path.name if self.file_path else "未命名.md"
        ok, value = await system_save_file_dialog(default)
        if not ok:
            self.notify("系统对话框不可用, 改用内置输入", severity="warning")
            self.push_screen(
                PathPrompt("另存为", default), self._on_save_as_path
            )
        elif value:
            self._on_save_as_path(value)
        # ok 且空 = 用户取消

    def _on_save_as_path(self, value: str | None) -> None:
        if not value:
            return
        path = Path(value)
        if not path.suffix:
            path = path.with_suffix(".md")
        self.file_path = path
        self.query_one(Preview).doc_dir = path.parent
        self.file_guard.acquire(str(path))
        self.action_save()

    # ── 查找替换 (居中弹窗; 状态存 app.find_state) ──────────
    def action_find(self) -> None:
        self.push_screen(
            FindScreen(), lambda _: self.query_one(Editor).focus()
        )

    def action_find_next(self) -> None:
        _find_next(
            self.query_one(Editor), self.find_state, notify=self.notify
        )

    # ── 格式化快捷键 (键义见 formatting.py) ─────────────────
    def action_format_bold(self) -> None:
        formatting.toggle_bold(self.query_one(Editor))

    def action_format_italic(self) -> None:
        formatting.toggle_italic(self.query_one(Editor))

    def action_format_code(self) -> None:
        formatting.insert_code_block(self.query_one(Editor))

    def action_format_link(self) -> None:
        formatting.insert_link(self.query_one(Editor))

    def action_format_image(self) -> None:
        formatting.insert_image(self.query_one(Editor))

    # ── 帮助与退出 ──────────────────────────────────────────
    def action_show_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_request_quit(self) -> None:
        if self.query_one(Editor).text != self._saved_text:
            self.push_screen(QuitConfirm(), self._on_quit_choice)
        else:
            self.exit()

    def _on_quit_choice(self, choice: str) -> None:
        if choice == "save":
            if self.file_path is not None:
                asyncio.create_task(self._save_and_exit())
            else:
                # 未命名文档: 系统另存为, 取消则放弃退出
                asyncio.create_task(self._quit_via_save_as())
        elif choice == "discard":
            self._clear_recovery()
            self.exit()

    async def _save_and_exit(self) -> None:
        await self.action_save()
        self.exit()

    async def _quit_via_save_as(self) -> None:
        ok, value = await system_save_file_dialog("未命名.md")
        if ok and value:
            self._on_save_as_path(value)
            self.exit()
        elif not ok:
            self.push_screen(
                PathPrompt("另存为", "未命名.md"), self._save_then_quit
            )
        # ok 且空 = 用户取消保存 → 不退出

    def _save_then_quit(self, value: str | None) -> None:
        if not value:
            return
        path = Path(value)
        if not path.suffix:
            path = path.with_suffix(".md")
        self.file_path = path
        self.file_guard.acquire(str(path))
        asyncio.create_task(self._save_and_exit())
