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
from textual.widget import Widget
from textual.widgets import Button, Static

from .io import FileGuard, read_text_file, write_text_file

from . import formatting
from .dialogs import HelpScreen, PathPrompt, QuitConfirm, RecoveryPrompt
from .editor import Editor
from .find_replace import FindState, FindScreen, find_next as _find_next
from .preview import Preview
from .sysdialog import system_open_file_dialog, system_save_file_dialog

MODES = ("edit", "preview", "split")
PREVIEW_DEBOUNCE = 0.4  # 秒; GUI 版 v1.3.0 用 0.2, 但 Textual 版全量重建
# 实测单次 ~100-400ms 且分段阻塞事件循环 (打字停顿 0.2-0.5s 是词间常态,
# 0.2 阈值 = 几乎每写一个词就重建一次, 续打撞上阻塞就"卡一下")


def _settings_path() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / "VIMD" / "settings.json"


def _recovery_path() -> Path:
    """脏状态恢复日志 — 被强杀(窗口×)后下次启动据此弹恢复框。"""
    return _settings_path().parent / "recovery.json"


def _recovery_key(file_path: Path | None) -> str:
    """恢复条目键: 归一化绝对路径 (未命名 = "")。

    不同文件各存各的草稿 — 否则打开文件 B 会拿文件 A 的草稿弹恢复框,
    选恢复就把 A 的内容盖进 B (T0 数据丢失)。
    """
    if file_path is None:
        return ""
    return os.path.normcase(os.path.abspath(str(file_path)))


def _load_recovery() -> dict[str, dict]:
    """读恢复日志 → {key: {path, content}}; 兼容 v0.1.x 单条旧格式。"""
    try:
        data = json.loads(_recovery_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    entries = data.get("entries")
    if isinstance(entries, dict):
        return {k: v for k, v in entries.items() if isinstance(v, dict)}
    # 旧版: 单条 {path, content} — 按其 path 归入对应文件的条目
    if isinstance(data.get("content"), str):
        p = data.get("path") or ""
        return {
            _recovery_key(Path(p) if p else None): {"path": p,
                                                    "content": data["content"]}
        }
    return {}


def _store_recovery(entries: dict[str, dict]) -> None:
    """写恢复日志; 无条目时删文件。"""
    try:
        rp = _recovery_path()
        if not entries:
            rp.unlink()
        else:
            rp.parent.mkdir(parents=True, exist_ok=True)
            rp.write_text(
                json.dumps({"entries": entries}, ensure_ascii=False),
                encoding="utf-8",
            )
    except OSError:
        pass  # 写不进去不影响编辑


class TitleRow(Horizontal):
    """标题行: ⭘ + VIMD 居中, 整行可点 -> 打开帮助 (复刻原 Header 手感)。"""

    def on_click(self, event) -> None:
        event.stop()
        self.app.action_show_help()


class PreviewScroll(VerticalScroll):
    """预览滚动容器 — 跟随光标, 但只在"光标位置被推到预览底边"时才向下推进。

    跟随状态挂在滚动位置上 (用户约定的口径):
      - 预览在底部        → 跟随: 分屏下光标所在块被钉在可视区底边, 上面永远留得住
                            刚写过的上文 (写完一行, 预览跟着走一行);
      - 手动滚离底部      → 不跟随: 可以安心"预览上文、写下文", 打字不再把它拽回去;
      - 再滚回底部 (End)  → 恢复跟随。
    只向下推进, 绝不自动回滚 — 于是"看上文"和"编辑预览同步"两件事互不打架。
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.follow = True
        self._auto_y: int | None = None  # 上次程序滚动的落点 (据此认出"不是用户滚的")

    def watch_scroll_y(self, old_value: float, new_value: float) -> None:
        super().watch_scroll_y(old_value, new_value)
        if self._auto_y is not None and round(new_value) == self._auto_y:
            return  # 本类自己滚的, 跟随状态不动
        self._auto_y = None  # 用户滚的: 落点记账作废
        self.follow = self.is_vertical_scroll_end

    def watch_virtual_size(self, *_size: object) -> None:
        # 预览重建 (打字防抖到期 / 换文件) 后内容高度变了: 跟随态下把光标重新钉回底边
        self.call_after_refresh(self.sync_caret)

    def reset(self) -> None:
        """换文件 / 新建: 预览回顶部, 跟随重新武装 (跟 MDPad 打开新文档的起点一致)。"""
        self.follow = True
        self._auto_y = 0
        self.scroll_to(y=0, animate=False)

    def _content_y(self, block: Widget) -> float:
        """块在滚动内容坐标系里的 y — 与当前滚动位置无关。

        不能用 region: 程序滚动后它和 scroll_y 一样要到下一帧才更新 (实测
        滚到 28 后立刻再读 block.region.y 还是滚动前那个值), 同一帧里第二次
        同步就会照着旧位置再滚一遍 -> 重复下滚。virtual_region 是内容坐标,
        不受滚动影响, 同一帧里问几次都是同一个答案。
        """
        y = 0.0
        node: Widget | None = block
        while isinstance(node, Widget) and node is not self:
            y += node.virtual_region.y
            node = node.parent if isinstance(node.parent, Widget) else None
        return y

    def sync_caret(self) -> bool:
        """把光标所在块推进可视区底边; 不需要动 (或不在分屏) 时返回 False。

        目标位置直接解方程算出来 (offset = 锚点内容 y - 可视区高 + 1), 不依赖
        当前 region/scroll_y, 所以同一帧被叫多少次结果都一样 (幂等)。只向下:
        光标已被"看过"(在可视区内, 或用户把预览滚到了更下面)就不动 — 这就是
        "没被推到最底下就不跟随"。
        """
        if not self.follow or not self.display:
            return False
        editor = self.app.query_one(Editor)
        if not editor.display:  # 纯预览 (F3): 没有光标可跟, 滚动权全归用户
            return False
        hit = self.query_one(Preview).block_for_line(editor.cursor_location[0])
        if hit is None:
            return False
        block, frac = hit
        box = block.virtual_region
        if box.height <= 0:
            return False  # 预览刚重建, 块还没布局 -> 下一帧 virtual_size 变化时再钉
        anchor = self._content_y(block) + round(frac * (box.height - 1))
        target = anchor - (self.scrollable_content_region.height - 1)
        current = max(int(self.scroll_target_y), int(self.scroll_y),
                      self._auto_y or 0)
        if target <= current:
            return False
        target = min(target, self.max_scroll_y)
        if target <= current:
            return False
        self._auto_y = target  # 先记账: watch_scroll_y 据此认出这次是自己滚的
        self.scroll_to(y=target, animate=False)
        return True


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
    #hint-recover { display: none; }
    #help-box Static { text-wrap: wrap; }
    HelpScreen, PathPrompt, QuitConfirm, RecoveryPrompt {
        align: center middle;
    }
    FindScreen { align: right bottom; }
    ToastRack { dock: top; align: right top; }
    #help-box, #prompt-box, #quit-box {
        width: 76; height: auto; max-height: 90%;
        padding: 1 2; background: $surface; border: thick $accent;
    }
    #help-box { height: 90%; }          /* 内容固定比一屏高: 靠里面的滚动容器翻页 */
    #help-scroll { height: 1fr; }
    #quit-box { width: 60; }
    #recover-box {
        width: auto; height: auto; max-width: 90%; max-height: 90%;
        padding: 1 2; background: $surface; border: thick $accent;
    }
    #recover-box Static { text-wrap: wrap; }
    #help-hint, #quit-title { margin-top: 1; }
    #quit-buttons { height: 3; margin-top: 1; }
    #quit-buttons Button { margin: 0 1; }
    #recover-buttons { width: auto; height: 3; margin-top: 1; }
    #recover-buttons Button { margin: 0 1; }
    """
    BINDINGS = [
        # 文件/编辑/查找键照常生效但不在 footer 展示:
        # 只留 5 个可见项, 窄终端也排得开
        Binding("ctrl+n", "new", "新建", show=False),
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
        # 缩放: VIMD 不再自绑快捷键 (2026-09-25 用户定) — 用终端自带的
        # Ctrl+= / Ctrl+- / Ctrl+0, 那三个键被 Windows Terminal 接管, 应用本来也收不到
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
            with PreviewScroll(id="preview-scroll"):
                yield Preview(id="preview")
        with Horizontal(id="botbar"):
            yield Button("Ctrl+H 帮助", id="hint-h", compact=True)
            yield Button("F2 编辑", id="hint-2", compact=True)
            yield Button("F3 预览", id="hint-3", compact=True)
            yield Button("F4 分屏", id="hint-4", compact=True)
            yield Button("恢复", id="hint-recover", compact=True)
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

    # ── 恢复日志 (脏内容保命: 窗口×杀不死它; 按文件分条) ────
    def _clear_recovery(self) -> None:
        """只删当前文件的条目 — 其它文件的草稿不许动。"""
        key = _recovery_key(self.file_path)
        entries = _load_recovery()
        if key in entries:
            del entries[key]
            _store_recovery(entries)

    def _flush_recovery(self) -> None:
        """切换文件前把当前脏内容立即写入它自己的条目 (不等 0.5s 防抖)。"""
        text = self.query_one(Editor).text
        if text == self._saved_text:
            return
        entries = _load_recovery()
        entries[_recovery_key(self.file_path)] = {
            "path": str(self.file_path) if self.file_path else "",
            "content": text,
        }
        _store_recovery(entries)

    def _sync_recovery_if(self, gen: int) -> None:
        """防抖到期: 脏 -> 写当前文件的条目; 干净 -> 删当前文件的条目。

        干净态删除仅在没有待决恢复框时执行 — 启动加载触发的
        "文本==已存" 定时器会晚于恢复框 0.5s 到期, 不拦就把
        刚弹出的草稿从盘上删了 (选"稍后"后重启再也问不到)。
        """
        if gen != self._recovery_gen:
            return
        key = _recovery_key(self.file_path)
        entries = _load_recovery()
        text = self.query_one(Editor).text
        if text != self._saved_text:
            entries[key] = {
                "path": str(self.file_path) if self.file_path else "",
                "content": text,
            }
            _store_recovery(entries)
        elif key in entries and self._recovery_data is None:
            del entries[key]
            _store_recovery(entries)

    def _check_recovery(self) -> None:
        """当前文件有自己的草稿才弹恢复框; 已有待决框则不叠。"""
        if self._recovery_data is not None:
            return
        entry = _load_recovery().get(_recovery_key(self.file_path))
        if entry is None:
            return
        content = entry.get("content", "")
        if content == self.query_one(Editor).text:
            self._clear_recovery()  # 内容一致 = 没有真正丢失的东西
            return
        # 缓存在内存: 回调时盘上条目可能已被清掉, 不再二次读盘
        self._recovery_data = entry
        self.push_screen(
            RecoveryPrompt(entry.get("path", "")), self._on_recovery_choice
        )

    def _reset_recovery_prompt(self) -> None:
        """换文件/新建: 旧文件的"稍后"缓存与底栏按钮收回。

        草稿仍在盘上自己的条目里 — 重新打开那个文件会再问。
        """
        self._recovery_data = None
        self.query_one("#hint-recover").display = False

    def _on_recovery_choice(self, choice: str) -> None:
        chip = self.query_one("#hint-recover")
        if choice == "restore":
            data = self._recovery_data
            if not data:
                data = _load_recovery().get(_recovery_key(self.file_path))
            if not data:
                self._recovery_data = None
                chip.display = False
                self.notify("恢复数据不存在", severity="warning")
                return
            self.query_one(Editor).text = data.get("content", "")
            self._recovery_data = None
            chip.display = False
            self.notify("已恢复未保存的内容 (记得保存)")
        elif choice == "discard":
            self._recovery_data = None
            chip.display = False
            # 交给同步器判而非直接删: 底栏按钮让"编辑中途丢弃"可达,
            # 此时盘上的条目可能已是本轮新改动 (脏 -> 改写, 干净 -> 删)
            self._sync_recovery_if(self._recovery_gen)
        else:
            # "" (Esc/点外/稍后) = 草稿留在内存+落盘, 底栏亮出"恢复"按钮
            chip.display = True

    def action_recover(self) -> None:
        """底栏"恢复"按钮: 稍后留下的草稿重弹恢复框。"""
        if self._recovery_data is None:
            self.query_one("#hint-recover").display = False
            return
        self.push_screen(
            RecoveryPrompt(self._recovery_data.get("path", "")),
            self._on_recovery_choice,
        )

    def _apply_mode(self, mode: str) -> None:
        self._mode = mode
        body = self.query_one("#body", Horizontal)
        for m in MODES:
            body.remove_class("m-" + m)
        body.add_class("m-" + mode)
        editor = self.query_one(Editor)
        scroll = self.query_one("#preview-scroll", PreviewScroll)
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
        self._sync_preview_scroll()

    def _render_if(self, gen: int) -> None:
        if gen != self._preview_gen:
            return
        # 编辑视图 (F2): 预览隐藏却仍会全量重建 — 实测单次几百 ms CPU 且
        # 分段阻塞事件循环 ~100ms, 打字停顿后立刻卡一下, 纯浪费。
        # 切到 F3/F4 时 _apply_mode 会立即补渲染, 内容不丢。
        scroll = self.query_one("#preview-scroll", PreviewScroll)
        if not scroll.display:
            return
        self._render_preview_now()

    def _render_preview_now(self) -> None:
        self.query_one(Preview).update(self.query_one(Editor).text)
        # 重建是异步挂载的, 块位置要等下一帧才生效 — 排到刷新后补一次定位
        # (跟随态下把光标钉回底边; 不跟随时 sync_caret 自己就短路了)
        self.call_after_refresh(self._sync_preview_scroll)

    def _sync_preview_scroll(self) -> None:
        self.query_one("#preview-scroll", PreviewScroll).sync_caret()

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
            "hint-recover": self.action_recover,
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
        # 换文件: 旧文件的脏内容立即写进它自己的条目 (不等 0.5s 防抖),
        # 旧文件的"稍后"提示态收回 — 草稿仍在盘上, 重开那个文件再问
        self._flush_recovery()
        self._reset_recovery_prompt()
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
        # 新文档预览从顶部开始, 跟随重新武装 (上一个文件的滚离底部状态不带过来)
        self.query_one("#preview-scroll", PreviewScroll).reset()
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
            self._check_recovery()  # 这个文件有自己的草稿才弹
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
        """另存为接管路径并保存 (系统对话框分支 / 内置输入回调共用)。"""
        if self._adopt_save_as_path(value):
            asyncio.create_task(self.action_save())

    def _adopt_save_as_path(self, value: str | None) -> bool:
        """只接管 file_path / 预览基目录 / 文件锁, 不保存。

        需要"保存真正完成后再继续"的流程 (退出/新建) 接管后自己
        await action_save — 裸调 action_save() 没人 await, 不会执行。
        """
        if not value:
            return False
        path = Path(value)
        if not path.suffix:
            path = path.with_suffix(".md")
        # 缓冲归属换了文件: 旧文件的草稿留在盘上自己的条目里, 提示态收回
        self._reset_recovery_prompt()
        self.file_path = path
        self.query_one(Preview).doc_dir = path.parent
        self.file_guard.acquire(str(path))
        return True

    # ── 新建 (Ctrl+N): 脏态先问, 保存链镜像退出链 ──────────
    def action_new(self) -> None:
        if self.query_one(Editor).text != self._saved_text:
            self.push_screen(QuitConfirm("new"), self._on_new_choice)
        else:
            self._do_new()

    def _on_new_choice(self, choice: str) -> None:
        if choice == "save":
            if self.file_path is not None:
                asyncio.create_task(self._save_then_new())
            else:
                asyncio.create_task(self._new_via_save_as())
        elif choice == "discard":
            self._clear_recovery()
            self._do_new()

    def _do_new(self) -> None:
        self.file_path = None
        self.file_guard.release()
        editor = self.query_one(Editor)
        editor.text = ""
        self._saved_text = ""
        preview = self.query_one(Preview)
        preview.doc_dir = Path.cwd()
        preview.update("")
        self.query_one("#preview-scroll", PreviewScroll).reset()
        # 旧文件的提示态收回; 新缓冲 = 键 "", 有自己的草稿才再问
        self._reset_recovery_prompt()
        self._check_recovery()
        self.refresh_status()
        self.notify("已新建文件")

    async def _save_then_new(self) -> None:
        await self.action_save()
        self._do_new()

    async def _new_via_save_as(self) -> None:
        ok, value = await system_save_file_dialog("未命名.md")
        if ok and value:
            self._adopt_save_as_path(value)
            await self.action_save()
            self._do_new()
        elif not ok:
            self.push_screen(
                PathPrompt("另存为", "未命名.md"), self._new_after_save_path
            )
        # ok 且空 = 用户取消保存 → 放弃新建

    def _new_after_save_path(self, value: str | None) -> None:
        if self._adopt_save_as_path(value):
            asyncio.create_task(self._save_then_new())

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
            self._adopt_save_as_path(value)
            await self.action_save()  # 落盘完成再退出
            self.exit()
        elif not ok:
            self.push_screen(
                PathPrompt("另存为", "未命名.md"), self._save_then_quit
            )
        # ok 且空 = 用户取消保存 → 不退出

    def _save_then_quit(self, value: str | None) -> None:
        if self._adopt_save_as_path(value):
            asyncio.create_task(self._save_and_exit())
