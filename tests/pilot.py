"""VIMD 全量 pilot — 视图/滚动/格式/查找弹窗/两行状态栏/底行芯片/软换行视觉行号/回退与链接分发。

运行: python tests/pilot.py        (退出码 0 = 全部通过; 不需要 pytest)

测试件写在临时目录 (VIMD_TEST_SCRATCH 可覆盖), 并且**自己把 APPDATA 指到隔离目录** ——
本脚本开头会删恢复日志, 用真实 APPDATA 会删掉你正在跑的那个 VIMD 的草稿。
"""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

SCRATCH = Path(os.environ.get("VIMD_TEST_SCRATCH")
               or Path(tempfile.gettempdir()) / "vimd-pilot")
# 必须在 import vimd.* 之前: vimd.io / singleton 在 import 期读 APPDATA
os.environ["APPDATA"] = str(SCRATCH / "appdata")
SCRATCH.mkdir(parents=True, exist_ok=True)
Path(os.environ["APPDATA"]).mkdir(parents=True, exist_ok=True)
SAMPLE = SCRATCH / "sample.md"
SAMPLE.write_text(
    "# 标题\n\n"
    + "\n\n".join(f"这是第 {i} 段, 用于撑高预览面板。" for i in range(1, 26))
    + "\n\n你好, MDPad TUI\n\n[官网](https://example.com)\n\n"
    "```python\nprint(1)\n```\n",
    encoding="utf-8",
)

RECOVERY = Path(os.environ["APPDATA"]) / "VIMD" / "recovery.json"
try:
    RECOVERY.unlink()
except OSError:
    pass

failures = []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        failures.append(name)


async def main():
    from textual.document._document import Selection
    from textual.containers import VerticalScroll
    from textual.widgets import Button, Input

    import vimd.app as appmod
    from vimd.app import VIMDApp
    from vimd.dialogs import (
        HelpScreen,
        PathPrompt,
        QuitConfirm,
        RecoveryPrompt,
        SettingsScreen,
    )
    from vimd.editor import Editor
    from vimd.find_replace import FindScreen
    from vimd.formatting import _location_of
    from vimd.preview import Preview

    app = VIMDApp(path=str(SAMPLE))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.5)
        ed = app.query_one(Editor)
        scr = app.query_one("#preview-scroll")

        def select_range(word: str, backward: bool = False):
            text = ed.text
            off = text.index(word)
            a, b = off, off + len(word)
            ed.selection = Selection(
                _location_of(text, b if backward else a),
                _location_of(text, a if backward else b),
            )

        # ── 视图 ──
        check("加载样例", "MDPad TUI" in ed.text)
        check("无恢复草稿时底栏无恢复按钮",
              not app.query_one("#hint-recover").display)
        check("预览渲染出块", len(app.query_one(Preview).children) > 0)
        await pilot.press("alt+3")
        await pilot.pause(0.2)
        check("Alt+3 分屏", bool(ed.display) and bool(scr.display))
        await pilot.press("alt+2")
        await pilot.pause(0.2)
        check("Alt+2 预览+焦点在滚动容器",
              not ed.display and bool(scr.display) and app.focused is scr)

        # 回归: 编辑器未聚焦时格式键不许响应 (2026-09-26 用户 bug:
        # 预览模式下 Ctrl+B/I 还在改编辑器选区 — App 级绑定不看焦点)
        ed.selection = Selection((0, 0), (0, 3))
        await pilot.pause(0.2)
        text_before = ed.text
        await pilot.press("ctrl+b")
        await pilot.press("ctrl+i")
        await pilot.pause(0.3)
        check(f"预览聚焦时格式键不改文档 (unchanged={ed.text == text_before})",
              ed.text == text_before)
        ed.cursor_location = (0, 0)  # 收起选区, 别影响后续断言
        await pilot.pause(0.2)

        # ── 预览滚动 ──
        for _ in range(6):
            await pilot.press("down")
        await pilot.pause(0.3)
        y1 = scr.scroll_y
        check("方向键滚动", y1 > 0)
        await pilot.press("pagedown")
        await pilot.pause(0.3)
        check("PgDn 继续滚", scr.scroll_y > y1)
        await pilot.press("home")
        await pilot.pause(0.3)
        check("Home 回顶", scr.scroll_y == 0)
        await pilot.press("alt+1")
        await pilot.pause(0.2)
        check("Alt+1 回编辑", bool(ed.display) and not scr.display)

        # 隐藏的预览不许渲染 (2026-09-26: 编辑视图装载期渲染隐藏预览实测 7.8k 次
        # widget 挂载 + 2 万次 CSS apply, 打开后要 16s 才排空) — 口径: 只有预览可见
        # 的模式 (preview/split) 才渲染; 切到可见模式时立即补渲染, 内容不丢
        _calls = []
        _orig_now = app._render_preview_now
        app._render_preview_now = lambda: _calls.append(1)
        app._apply_mode("edit")
        await pilot.pause(0.2)
        check("编辑视图: 不渲染隐藏的预览", not _calls)
        app._apply_mode("split")
        await pilot.pause(0.2)
        check("切到分屏: 预览立即补渲染", bool(_calls))
        app._render_preview_now = _orig_now
        app._apply_mode("edit")
        await pilot.pause(0.2)
        check("恢复编辑视图", bool(ed.display) and not scr.display)

        # ── 输入/撤销 ──
        await pilot.press("end")
        await pilot.press("x")
        await pilot.press("y")
        await pilot.pause(0.5)
        check("输入生效", "xy" in ed.text)
        await pilot.press("ctrl+z")
        await pilot.pause(0.2)
        check("撤销生效", "xy" not in ed.text)

        # ── 格式键 (正向 + 反向选区) ──
        select_range("MDPad")
        await pilot.press("ctrl+b")
        await pilot.pause(0.3)
        check("Ctrl+B 正向包裹", "**MDPad**" in ed.text)
        await pilot.press("ctrl+b")
        await pilot.pause(0.3)
        check("Ctrl+B 再按解开",
              "**MDPad**" not in ed.text and "MDPad" in ed.text)

        select_range("官网", backward=True)
        await pilot.press("alt+i")
        await pilot.pause(0.3)
        check("斜体反向选区包裹", "*官网*" in ed.text)
        await pilot.press("alt+i")
        await pilot.pause(0.3)
        check("斜体反向再按解开", "*官网*" not in ed.text)

        select_range("print(1)")
        await pilot.press("ctrl+k")
        await pilot.pause(0.3)
        check("Ctrl+K 代码块(拦截)", "```\nprint(1)\n```" in ed.text)

        select_range("你好")
        await pilot.press("ctrl+l")
        await pilot.pause(0.3)
        check("Ctrl+L 链接", "[你好]()" in ed.text)
        await pilot.press("ctrl+z")
        await pilot.pause(0.2)
        loc = ed.cursor_location
        ed.selection = Selection(loc, loc)
        await pilot.press("ctrl+shift+l")
        await pilot.pause(0.3)
        check("Ctrl+Shift+L 空插图片", "![]()" in ed.text)
        for _ in range(3):
            await pilot.press("ctrl+z")
        await pilot.pause(0.3)
        check("格式撤销干净",
              "```\nprint(1)\n```" not in ed.text and "![]()" not in ed.text)

        # ── 两行状态栏 ──
        top = str(app.query_one("#tb-name").render())
        check("文件名在顶栏左上", "sample.md" in top)
        trow = app.query_one("#title-row")
        brow = app.query_one("#topbar")
        check("顶部两行: 标题行在文件名行之上",
              trow.region.y < brow.region.y)
        check("标题行 VIMD",
              str(app.query_one("#tr-title").render()).strip() == "VIMD")
        check("标题行有圆圈 ⭘",
              str(app.query_one("#tr-icon").render()).strip() == "⭘")
        await pilot.click("#title-row")
        await pilot.pause(0.4)
        check("点标题行打开帮助", isinstance(app.screen, HelpScreen))
        await pilot.press("escape")
        await pilot.pause(0.3)
        meta = str(app.query_one("#meta").render())
        check("模式与行列在底栏右", "edit" in meta and ":" in meta)

        # meta 超长不被裁 (宽度须随 label 增长)
        from textual.widgets import Button as _Btn
        _mb = app.query_one("#meta", _Btn)
        _mb.label = "split 100000:1234567"
        _mb.styles.width = len("split 100000:1234567") + 3
        await pilot.pause(0.3)
        check("meta 超长仍显示", "1234567" in str(_mb.render())
              and _mb.region.width >= 20)
        app.refresh_status()  # 还原真实值
        await pilot.pause(0.2)
        # meta 超长不被裁 (宽度须随 label 增长; 曾锁死在挂载宽度)
        from textual.widgets import Button as _Btn
        _mb = app.query_one("#meta", _Btn)
        _mb.label = "split 100000:1234567"
        _mb.styles.width = len("split 100000:1234567") + 3
        await pilot.pause(0.3)
        check("meta 超长仍显示",
              "1234567" in str(_mb.render()) and _mb.region.width >= 20)
        app.refresh_status()
        await pilot.pause(0.2)

        # ── 查找弹窗 ──
        await pilot.press("ctrl+f")
        await pilot.pause(0.4)
        check("Ctrl+F 弹出查找窗", isinstance(app.screen, FindScreen))
        fs = app.screen
        r = fs.query_one("#find-box").region
        check(
            f"查找窗右下定位 (box={r})",
            r.x + r.width >= app.size.width - 2
            and r.y + r.height >= app.size.height - 4,
        )
        fs.query_one("#find-input", Input).value = "你好"
        await pilot.pause(0.3)
        check("计数 1 处匹配",
              "1 处匹配" in str(fs.query_one("#find-count").render()))
        await pilot.press("enter")
        await pilot.pause(0.3)
        check("Enter 选中匹配", ed.selected_text == "你好")
        # 上一个/下一个: 2026-09-26 由 Shift+Enter 改绑 Alt+Z / Alt+X (Ctrl+X 不绑,
        # 保持 TextArea/Input 原生剪切)。Shift+Enter 在 Windows Terminal 下与 Enter
        # 同字节 (真机探针: 两者 RAW 都是 '\r'), 绑定永远命中不了, 故不再测它。
        fs.query_one("#find-input", Input).value = "这是第"
        await pilot.pause(0.3)
        await pilot.press("enter")
        await pilot.pause(0.3)
        m1 = ed.selection
        await pilot.press("alt+x")
        await pilot.pause(0.3)
        m2 = ed.selection
        check("Alt+X 下一个(选区前移)",
              m2 != m1 and ed.selected_text == "这是第")
        check("Alt+X 不往查找框塞字符",
              fs.query_one("#find-input", Input).value == "这是第")
        await pilot.press("alt+z")
        await pilot.pause(0.3)
        check("Alt+Z 上一个(退回原处)", ed.selection == m1)
        check("Alt+Z 不往查找框塞字符",
              fs.query_one("#find-input", Input).value == "这是第")
        fs.query_one("#find-input", Input).value = "你好"
        await pilot.pause(0.3)
        await pilot.click("#case-check")
        await pilot.pause(0.3)
        check("勾选框开大小写",
              app.find_state.case is True
              and "区分大小写" in str(fs.query_one("#find-count").render()))
        check("开态显示√", "√" in fs.query_one("#case-check")._button.plain)
        await pilot.click("#case-check")
        await pilot.pause(0.3)
        check("再点关闭", app.find_state.case is False)
        check("关态留空(不显示叉)",
              "×" not in fs.query_one("#case-check")._button.plain
              and "√" not in fs.query_one("#case-check")._button.plain)
        await pilot.press("escape")
        await pilot.pause(0.3)
        check("Esc 关弹窗", not isinstance(app.screen, FindScreen))
        await pilot.press("alt+x")
        await pilot.pause(0.3)
        check("关窗后 Alt+X 仍跳转", ed.selected_text == "你好")
        await pilot.press("alt+z")
        await pilot.pause(0.3)
        check("关窗后 Alt+Z 仍跳转(单匹配循环回原处)", ed.selected_text == "你好")
        await pilot.press("ctrl+f")
        await pilot.pause(0.4)
        fs = app.screen
        check("重开回填查询",
              fs.query_one("#find-input", Input).value == "你好")
        fs.query_one("#find-input", Input).value = "print"
        fs.query_one("#replace-input", Input).value = "puts"
        await pilot.pause(0.3)
        await pilot.press("enter")
        await pilot.pause(0.2)
        await pilot.click("#replace-one")
        await pilot.pause(0.3)
        check("替换当前按钮", "puts(1)" in ed.text and "print" not in ed.text)
        check("替换后计数归零",
              "0 处匹配" in str(fs.query_one("#find-count").render()))
        fs.query_one("#find-input", Input).value = "撑高"
        fs.query_one("#replace-input", Input).value = "超高"
        await pilot.pause(0.3)
        await pilot.click("#replace-all")
        await pilot.pause(0.3)
        check("全部替换按钮", "撑高" not in ed.text and "超高" in ed.text)
        await pilot.click(app.screen, offset=(3, 3))
        await pilot.pause(0.3)
        check("点外部关闭弹窗", not isinstance(app.screen, FindScreen))
        await pilot.press("ctrl+f")
        await pilot.pause(0.4)
        await pilot.click("#find-title")
        await pilot.pause(0.3)
        check("点弹窗内部不关闭", isinstance(app.screen, FindScreen))
        await pilot.press("escape")
        await pilot.pause(0.3)

        # ── 通知位置 ──
        racks = [
            w for w in app.screen.walk_children()
            if type(w).__name__ == "ToastRack"
        ]
        if racks:
            tr = racks[0].region
            check("通知架右上角",
                  tr.y <= 1 and tr.x + tr.width >= app.size.width - 2)
        else:
            print("NOTE: ToastRack 未挂载于测试环境, 通知位置留待真机目验")

        # ── 保存 ──
        await pilot.press("ctrl+s")
        await pilot.pause(0.4)
        check("保存落盘", SAMPLE.read_text(encoding="utf-8") == ed.text)
        await pilot.press("end")
        await pilot.press("z")
        await pilot.pause(0.3)
        check("脏标记在顶栏",
              "●" in str(app.query_one("#tb-name").render()))
        await pilot.press("ctrl+z")
        await pilot.pause(0.3)

        # ── 系统对话框回退 ──
        async def fake_fail(*args, **kwargs):
            return (False, "")

        appmod.system_save_file_dialog = fake_fail
        appmod.system_open_file_dialog = fake_fail
        await pilot.press("ctrl+shift+s")
        await pilot.pause(0.4)
        check("另存为回退内置输入", isinstance(app.screen, PathPrompt))
        await pilot.press("escape")
        await pilot.pause(0.3)
        await pilot.press("ctrl+o")
        await pilot.pause(0.4)
        check("打开回退内置输入", isinstance(app.screen, PathPrompt))
        await pilot.press("escape")
        await pilot.pause(0.3)

        # ── 回退另存为必须真正落盘 (修复前 action_save() 裸调用 = 协程从不执行) ──
        saveas1 = SCRATCH / "saveas.md"
        try:
            saveas1.unlink()
        except OSError:
            pass
        await pilot.press("ctrl+shift+s")
        await pilot.pause(0.4)
        check("另存为回退复现", isinstance(app.screen, PathPrompt))
        app.screen.query_one("#path-input", Input).value = str(saveas1)
        await pilot.press("enter")
        await pilot.pause(0.6)
        check("回退另存为落盘", saveas1.exists()
              and saveas1.read_text(encoding="utf-8") == ed.text)
        check("另存为接管路径", app.file_path == saveas1)

        # ── 帮助 = Alt+H ──
        await pilot.press("alt+h")
        await pilot.pause(0.4)
        check("Alt+H 帮助", isinstance(app.screen, HelpScreen))
        svg = app.export_screenshot()
        (SCRATCH / "d-help.svg").write_text(svg, encoding="utf-8")
        # 一行一条 + 键列对齐 + 可滚动
        import re

        from vimd.dialogs import HELP_KEYS, help_keys_text
        lines = help_keys_text().splitlines()
        entries = [(k, d) for _, items in HELP_KEYS for k, d in items]
        parts = [re.match(r"^  (\S.*?)  +(\S.*)$", line)
                 for line in lines if line.startswith("  ")]
        check("帮助: 一行一条「键 + 说明」两段",
              len(parts) == len(entries) and all(parts))
        pairs = {p.group(1): p.group(2) for p in parts if p}
        check("帮助: 键与说明一一对应 (无漏无重)",
              pairs == {k: d for k, d in entries})
        cols = {p.start(2) for p in parts if p}
        check(f"帮助: 说明列对齐 ({cols})", len(cols) == 1)
        help_scroll = app.screen.query_one("#help-scroll", VerticalScroll)
        check("帮助: 内容可滚动", help_scroll.max_scroll_y > 0)
        # 底部说明也得一行一条 (回归: 曾用「·」把几条挤成一行, 与上面键表风格不统一)
        from rich.cells import cell_len as _cell_len
        from vimd.dialogs import HELP_NOTES, help_notes_text
        _notes = help_notes_text()
        note_lines = [ln.replace("[dim]", "").replace("[/dim]", "")
                      for ln in _notes.splitlines() if ln.strip()]
        note_parts = [re.match(r"^  (\S.*?)  +(\S.*)$", ln) for ln in note_lines]
        check("帮助: 底部说明一行一条 (标签 + 说明两段)",
              len(note_parts) == len(HELP_NOTES) and all(note_parts))
        check("帮助: 底部说明与 HELP_NOTES 一一对应",
              {p.group(1): p.group(2) for p in note_parts if p} == dict(HELP_NOTES))
        note_cols = {_cell_len(ln[:p.start(2)])
                     for p, ln in zip(note_parts, note_lines) if p}
        check(f"帮助: 底部各条说明列对齐 (显示列 {note_cols})", len(note_cols) == 1)
        key_cols = set()
        for ln in lines:
            m = re.match(r"^  (\S.*?)  +(\S.*)$", ln)
            if m:
                key_cols.add(_cell_len(ln[:m.start(2)]))
        check(f"帮助: 说明列与上面键表同列 ({note_cols} vs {key_cols})",
              note_cols == key_cols)
        # 80 列窗口: 弹窗 90% 宽 − 说明列 = 每条说明能用的显示列数; 超了就得折行
        desc_col = next(iter(key_cols))
        budget = int(80 * 0.9) - desc_col
        check(f"帮助: 每条说明都放得下一行 (<= {budget} 显示列 @80 列窗口)",
              all(_cell_len(note) <= budget for _, note in HELP_NOTES))
        check("帮助: 底部说明不再用「·」串成一行", "·" not in _notes)
        hint_lines = [ln.strip() for ln in
                      str(app.screen.query_one("#help-hint").render()).splitlines()
                      if ln.strip()]
        check(f"帮助: 底部提示也一行一条 ({hint_lines})",
              len(hint_lines) >= 2 and all("·" not in ln for ln in hint_lines))

        await pilot.press("pagedown")
        await pilot.pause(0.3)
        check("帮助: PgDn 能翻页", help_scroll.scroll_y > 0)
        check("帮助: 提示钉在盒底且行号开关已移到设置 (盒内无 line-check)",
              not app.screen.query("#help-scroll #help-hint")
              and not app.screen.query("#line-check")
              and app.screen.query_one("#help-hint").region.y
              > help_scroll.region.y)
        await pilot.press("escape")
        await pilot.pause(0.3)
        check("帮助: Esc 关闭", not isinstance(app.screen, HelpScreen))

        # ── 退出确认 ──
        await pilot.press("end")
        await pilot.press("q")
        await pilot.pause(0.3)
        await pilot.press("alt+q")
        await pilot.pause(0.4)
        check("退出确认弹出", isinstance(app.screen, QuitConfirm))
        if isinstance(app.screen, QuitConfirm):
            check("退出确认为退出文案",
                  "保存并退出"
                  in str(app.screen.query_one("#save").render()))
        await pilot.press("escape")
        await pilot.pause(0.3)
        check("取消退出", not isinstance(app.screen, QuitConfirm))
        await pilot.press("ctrl+z")
        await pilot.pause(0.3)

        await pilot.press("ctrl+f")
        await pilot.pause(0.4)
        svg = app.export_screenshot()
        (SCRATCH / "d-find.svg").write_text(svg, encoding="utf-8")
        await pilot.press("escape")
        await pilot.pause(0.3)

        settings = Path(os.environ["APPDATA"]) / "VIMD" / "settings.json"
        saved_mode = None
        if settings.exists():
            saved_mode = json.loads(settings.read_text(encoding="utf-8")).get(
                "mode"
            )
        check(f"模式持久化 (读到 {saved_mode!r})", saved_mode == "edit")

        svg = app.export_screenshot()
        (SCRATCH / "tui.svg").write_text(svg, encoding="utf-8")

        # ── 软换行 + 视觉行号 (最后做, 不影响前面断言) ──
        orig = ed.text
        ed.text = "W" * 200 + "\n第二行\n" + orig
        await pilot.pause(0.5)
        check("软换行: 视觉行数 > 逻辑行数",
              ed.wrapped_document.height > ed.document.line_count)
        ed.scroll_to(y=0, animate=False)
        await pilot.pause(0.5)
        seg = list(ed.render_line(1))[0]
        check(
            f"折行续行也编号(视觉行号) (sy={ed.scroll_y}, seg={seg.text!r})",
            ed.scroll_y == 0 and seg.text.strip() == "2",
        )
        ed.move_cursor((1, 0))
        await pilot.pause(0.3)
        meta = str(app.query_one("#meta").render())
        check("状态行号=视觉行(第二逻辑行显示3)", " 3:" in meta)
        # ── 设置窗: 行号 + 光标样式 (从帮助提取; 芯片常驻, 帮助右边) ──
        hint_h = app.query_one("#hint-h")
        hint_set = app.query_one("#hint-set")
        hint_4 = app.query_one("#hint-4")
        bot_sep = app.query_one("#bot-sep")
        check("底栏顺序: 三模式 → 分割线 → 帮助 → 设置 (同一行)",
              hint_set.display
              and hint_4.region.y == hint_h.region.y == hint_set.region.y
              and bot_sep.region.x > hint_4.region.x
              and hint_h.region.x > bot_sep.region.x
              and hint_set.region.x > hint_h.region.x)
        check("模式芯片文案不带 F 几 (编辑/预览/分屏)",
              str(app.query_one("#hint-2").render()).strip() == "编辑"
              and str(app.query_one("#hint-3").render()).strip() == "预览"
              and str(app.query_one("#hint-4").render()).strip() == "分屏")
        await pilot.click("#hint-set")
        await pilot.pause(0.4)
        check("设置窗弹出: 行号开关/提示都在盒内",
              isinstance(app.screen, SettingsScreen)
              and bool(app.screen.query("#settings-box #line-check"))
              and bool(app.screen.query("#settings-box #settings-hint")))
        # 行号开关 (设置窗勾选框, 与大小写同款 UX)
        # 回归: 关行号必须**重折行** — 曾把原生 watcher 顶掉只清缓存,
        # wrap_width 变宽但文档没重折, 每行末尾短一截, 右边拼出一条空白带。
        # 113 字长行: 开行号 (wrap≈111) 必折 → 关行号 (wrap≈115) 应并回一行
        ed.text = "W" * 113 + "\n第二行"
        await pilot.pause(0.4)
        h_on = ed.wrapped_document.height
        await pilot.click("#line-check")
        await pilot.pause(0.3)
        h_off = ed.wrapped_document.height
        check(f"行号开关关 + 正文重折行 ({h_on}→{h_off})",
              ed.show_line_numbers is False
              and app.show_line_numbers is False
              and h_off < h_on)
        await pilot.click("#line-check")
        await pilot.pause(0.3)
        check(f"行号开关开 + 折回去 ({h_off}→{ed.wrapped_document.height})",
              ed.show_line_numbers is True
              and ed.wrapped_document.height > h_off)
        await pilot.press("escape")
        await pilot.pause(0.3)
        check("设置窗 Esc 关闭", not isinstance(app.screen, SettingsScreen))

        # 持久化: line_numbers 键落盘 (mode 前面已断言)
        data = json.loads(settings.read_text(encoding="utf-8"))
        check(f"设置持久化 (line_numbers=True, 读到 {data})",
              data.get("line_numbers") is True)

        # Alt+L 键盘开关 (与设置窗同一个开关, set_line_numbers 落盘)
        await pilot.press("alt+l")
        await pilot.pause(0.3)
        check(f"Alt+L 关行号 (show={app.show_line_numbers})",
              app.show_line_numbers is False
              and ed.show_line_numbers is False)
        await pilot.press("alt+l")
        await pilot.pause(0.3)
        check(f"Alt+L 开行号 (show={app.show_line_numbers})",
              app.show_line_numbers is True
              and ed.show_line_numbers is True)

        # ── 空文档越界行: 不得编号、行宽不得塌 (回归: 曾把整行砍成3字符) ──
        ed.text = ""
        await pilot.pause(0.5)
        ed.scroll_to(y=0, animate=False)
        await pilot.pause(0.5)
        check("空文档 wrapped.height=1", ed.wrapped_document.height == 1)
        row0 = list(ed.render_line(0))
        check(
            f"空文档首行编号1 (sy={ed.scroll_y}, seg={row0[0].text!r})",
            ed.scroll_y == 0 and row0 and row0[0].text.strip() == "1",
        )
        row1 = list(ed.render_line(1))
        check("越界行不编号且保持整宽",
              row1 and row1[0].text.strip() == ""
              and len(row1[0].text) >= 40)
        # ── 控制台标题 = VIMD (title 命令同款 API, WT 标签跟随) ──
        import ctypes

        buf = ctypes.create_unicode_buffer(256)
        ctypes.windll.kernel32.GetConsoleTitleW(buf, 256)
        check("控制台标题=VIMD", buf.value == "VIMD")

        # 行号随滚动走 (y=屏幕行, 行号须 = y+scroll_y+start)
        ed.text = "\n".join(f"L{i:03d} 行" for i in range(1, 121))
        await pilot.pause(0.5)
        ed.scroll_y = 10
        await pilot.pause(0.4)
        top_num = list(ed.render_line(0))[0].text.strip()
        check("行号随滚动递增", top_num == str(int(ed.scroll_y) + 1))
        # 行号列与正文之间必须隔开 (回归: 曾贴死, 用户嫌挤) —
        # gutter 段仍须占满 gutter_width, 否则正文会左移压到行号上
        row = list(ed.render_line(0))
        gutter_text = row[0].text
        gap = len(gutter_text) - len(gutter_text.rstrip(" "))
        body_text = "".join(seg.text for seg in row[1:])
        check(
            f"行号列总宽=max(位数+1,4), 间隔≥1 不紧贴 (动态间隔) "
            f"(gutter={gutter_text!r}, body={body_text[:8]!r})",
            len(gutter_text) == ed.gutter_width
            == max(ed._gutter_digits() + Editor.GUTTER_GAP_MIN,
                   Editor.GUTTER_DIGIT_ROOM)
            and gutter_text.strip() == top_num
            and gap == ed.gutter_width - ed._gutter_digits()
            and gap >= Editor.GUTTER_GAP_MIN
            and body_text.startswith(f"L{int(ed.scroll_y) + 1:03d}"),
        )

        # 选区字数 (含标点与空格; 折行 \n 不算字) 显示在模式左边
        ed.selection = Selection((0, 0), (2, 5))
        await pilot.pause(0.3)
        meta_now = str(app.query_one("#meta", Button).label)
        want = sum(1 for c in ed.selected_text if c not in "\r\n")
        check(
            f"选区字数在模式左边 (选中{want}字, meta={meta_now!r})",
            f"选中 {want}字" in meta_now
            and meta_now.find("选中") < meta_now.find(str(app._mode)),
        )
        ed.selection = Selection((0, 0), (0, 0))
        await pilot.pause(0.3)
        check(
            "取消选区后字数消失",
            "选中" not in str(app.query_one("#meta", Button).label),
        )

        # 恢复日志: 脏态写文件 -> 保存清文件
        ed.text = "绝不能丢的内容 QQQ"
        await pilot.pause(1.0)  # 恢复同步防抖 0.5s
        check("脏态写出恢复文件", RECOVERY.exists())
        await pilot.press("ctrl+s")
        await pilot.pause(0.5)
        # 诊断: 保存只清"当前文件"的键, 其它文件的草稿必须还在
        _rk = json.loads(RECOVERY.read_text(encoding="utf-8")) \
            if RECOVERY.exists() else {}
        print("DEBUG 保存后 recovery:", json.dumps(
            {k: (v.get("content", "")[:20] if isinstance(v, dict) else v)
             for k, v in _rk.get("entries", _rk).items()},
            ensure_ascii=False))
        check("保存清除恢复文件", not RECOVERY.exists())

        # ── Ctrl+N 新建: 干净态直接新建 ──
        await pilot.press("ctrl+n")
        await pilot.pause(0.4)
        check("Ctrl+N 干净态直接新建",
              ed.text == "" and app.file_path is None)
        nm = str(app.query_one("#tb-name").render())
        check("新建后顶栏未命名无脏点", "未命名" in nm and "●" not in nm)

        # ── Ctrl+N 脏态 -> 确认框 (new 文案) ──
        await pilot.press("end")
        await pilot.press("w")
        await pilot.pause(0.4)
        await pilot.press("ctrl+n")
        await pilot.pause(0.4)
        check("Ctrl+N 脏态弹确认", isinstance(app.screen, QuitConfirm))
        if isinstance(app.screen, QuitConfirm):
            save_lbl = str(app.screen.query_one("#save").render())
            disc_lbl = str(app.screen.query_one("#discard").render())
            check("确认框为新建文案",
                  "保存并新建" in save_lbl and "不保存新建" in disc_lbl)
            await pilot.press("escape")
            await pilot.pause(0.3)
        check("取消新建保留内容",
              "w" in ed.text and not isinstance(app.screen, QuitConfirm))

        # 不保存 -> 直接新建
        await pilot.press("ctrl+n")
        await pilot.pause(0.4)
        if isinstance(app.screen, QuitConfirm):
            app.screen.query_one("#discard").press()
            await pilot.pause(0.4)
        check("不保存新建为空",
              ed.text == "" and app.file_path is None)

        # ── 保存并新建 (file_path 已知): 先落盘再清空 ──
        await pilot.press("ctrl+o")
        await pilot.pause(0.4)
        if isinstance(app.screen, PathPrompt):
            app.screen.query_one("#path-input", Input).value = str(SAMPLE)
            await pilot.press("enter")
        await pilot.pause(0.5)
        check("重建样例打开",
              app.file_path == Path(str(SAMPLE)) and ed.text != "")
        await pilot.press("end")
        await pilot.press("v")
        await pilot.pause(0.4)
        before = ed.text
        await pilot.press("ctrl+n")
        await pilot.pause(0.4)
        if isinstance(app.screen, QuitConfirm):
            app.screen.query_one("#save").press()
        await pilot.pause(0.6)
        check("保存并新建先落盘",
              Path(str(SAMPLE)).read_text(encoding="utf-8") == before)
        check("保存并新建后为空",
              ed.text == "" and app.file_path is None)

        # ── 保存并新建 (未命名 -> 系统对话框回退另存为链) ──
        await pilot.press("end")
        await pilot.press("n")
        await pilot.pause(0.4)
        await pilot.press("ctrl+n")
        await pilot.pause(0.4)
        if isinstance(app.screen, QuitConfirm):
            app.screen.query_one("#save").press()
        await pilot.pause(0.4)
        check("未命名新建转回退另存为", isinstance(app.screen, PathPrompt))
        saveas2 = SCRATCH / "saveas2.md"
        try:
            saveas2.unlink()
        except OSError:
            pass
        if isinstance(app.screen, PathPrompt):
            app.screen.query_one("#path-input", Input).value = str(saveas2)
            await pilot.press("enter")
        await pilot.pause(0.8)
        check("未命名保存链先落盘",
              saveas2.exists() and saveas2.read_text(encoding="utf-8") == "n")
        check("未命名保存链后为空",
              ed.text == "" and app.file_path is None)
        ed.scroll_y = 0
        await pilot.pause(0.2)

    # ── 模拟被窗口×强杀: 恢复框弹出与恢复 ──
    import json as _json
    RECOVERY.parent.mkdir(parents=True, exist_ok=True)
    RECOVERY.write_text(
        _json.dumps({"path": str(SAMPLE), "content": "上次没保存的残稿QQQ"},
                    ensure_ascii=False),
        encoding="utf-8",
    )

    async def recovery_round():
        from vimd.dialogs import RecoveryPrompt as RP
        app2 = VIMDApp(path=str(SAMPLE))
        async with app2.run_test(size=(100, 30)) as p2:
            await p2.pause(0.7)
            check("强杀后重启弹恢复框", isinstance(app2.screen, RP))
            ed2 = app2.query_one(Editor)
            # 恢复框: 上下左右居中 + 紧凑刚好包裹内容
            rb = app2.screen.query_one("#recover-box").region
            check(
                f"恢复框居中 (box={rb})",
                abs(rb.x * 2 + rb.width - 100) <= 2
                and abs(rb.y * 2 + rb.height - 30) <= 2,
            )
            check(f"恢复框紧凑且不塌 (w={rb.width})", 20 <= rb.width < 76)
            # 稍后 -> 底栏出现"恢复"按钮 -> 点开重弹
            await p2.click("#later")
            await p2.pause(0.4)
            check("稍后关框", not isinstance(app2.screen, RP))
            chip = app2.query_one("#hint-recover")
            check("稍后底栏出现恢复按钮", bool(chip.display))
            await p2.click("#hint-recover")
            await p2.pause(0.4)
            check("点恢复按钮重弹恢复框", isinstance(app2.screen, RP))
            await p2.click("#restore")
            await p2.pause(0.4)
            check("恢复后内容回来", "残稿QQQ" in ed2.text)
            check("恢复后为脏态", ed2.text != app2._saved_text)
            check("恢复后按钮消失", not chip.display)

    await recovery_round()
    try:
        RECOVERY.unlink()
    except OSError:
        pass

    # ── 系统对话框管道 (不弹真窗) ──
    from vimd.sysdialog import _FILTER, _HEAD, _run

    ok, val = await _run(
        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "
        r"'C:\测试\中文名.md'"
    )
    check("PS 管道 UTF-8 中文往返", ok and val == r"C:\测试\中文名.md")
    ok2, val2 = await _run(
        _HEAD
        + "$d = New-Object Microsoft.Win32.SaveFileDialog; "
        + f"$d.Filter = '{_FILTER}'; $d.Filter"
    )
    check("WPF 对话框可构造", ok2 and val2 == _FILTER)

    # ── 带空格路径识别 ──
    from urllib.parse import quote, unquote

    from vimd.preview import normalize_dests

    check("空格路径包尖括号",
          normalize_dests(r"![](C:\a b.png)") == r"![](<C:\a b.png>)")
    check("无空格不动",
          normalize_dests(r"![](C:\ab.png)") == r"![](C:\ab.png)")
    check("已包裹不动",
          normalize_dests(r"![](<C:\a b.png>)") == r"![](<C:\a b.png>)")
    check("代码围栏内不动",
          normalize_dests("```\n[](a b)\n```") == "```\n[](a b)\n```")
    from markdown_it import MarkdownIt

    md_src = normalize_dests(
        r"![屏](C:\Users\x\屏幕截图 2026-09-21 230041.png)"
    )
    flat = []
    for t in MarkdownIt().parse(md_src):
        flat.append(t)
        flat.extend(t.children or [])
    srcs = [t.attrGet("src") for t in flat if t.type == "image"]
    check("markdown-it 解析 + unquote 还原",
          len(srcs) == 1
          and unquote(srcs[0])
          == r"C:\Users\x\屏幕截图 2026-09-21 230041.png")

    # ── 链接分发单测 ──
    import vimd.preview as pvmod

    opened = []
    notified = []
    pvmod.webbrowser.open = lambda u: opened.append(("web", u))

    def fake_startfile(p):
        p = str(p)
        opened.append(("file", p))
        if not Path(p).exists():
            raise OSError(p)

    pvmod.os.startfile = fake_startfile
    pvmod.open_href("https://example.com", Path.cwd(), notified.append)
    pvmod.open_href(str(SAMPLE), Path.cwd(), notified.append)
    pvmod.open_href("missing.png", Path.cwd(), notified.append)
    pvmod.open_href("#锚点", Path.cwd(), notified.append)
    check("web 分流",
          any(k == "web" and u == "https://example.com" for k, u in opened))
    check("file 分流",
          any(k == "file" and u.endswith("sample.md") for k, u in opened))
    check("相对缺失提示", any("打不开" in n for n in notified))
    check("锚点提示", any("锚点" in n for n in notified))
    real = SCRATCH / "屏 截.png"
    real.write_bytes(b"x")
    pvmod.open_href(quote(str(real)), Path.cwd(), notified.append)
    check("编码 href 还原打开", ("file", str(real)) in opened)

    # ── 拖放去引号 (WT 拖入文件带引号) ──
    from vimd.io import unquote_dropped_path as uq

    check("拖放去引号: 盘符路径",
          uq(r'"C:\Users\x\My Docs\a.md"') == r"C:\Users\x\My Docs\a.md")
    check("拖放去引号: 多文件",
          uq(r'"C:\a.md" "C:\b c\d.md"') == r"C:\a.md C:\b c\d.md")
    check("拖放去引号: 非路径引号不动", uq('"他说你好"') == '"他说你好"')
    check("拖放去引号: 无引号不动", uq(r"C:\a.md") == r"C:\a.md")

    # 事件级: Paste 只落一次 + 落地前已去引号 (自建实例, 主 app 上下文已退)
    from textual.events import Paste

    async def paste_round():
        # 走驱动那条路: 驱动把 Paste post 给 App (xterm 解析器 on_token → app.post_message),
        # App 再转发给焦点控件。旧代码覆写 `_on_paste` 又调 super() → Textual 按 MRO
        # 逐类派发 = 插两遍, 叠上 App 对非转发粘贴的二次转发, 一个路径长 4 份。
        app5 = VIMDApp(path=str(SAMPLE))
        async with app5.run_test(size=(100, 30)) as p5:
            await p5.pause(0.6)
            ed5 = app5.query_one(Editor)
            ed5.text = ""
            ed5.focus()
            await p5.pause(0.2)
            app5.post_message(Paste(r'"C:\drop\target file.md"'))
            await p5.pause(0.8)
            check("粘贴路径只落一次且已去引号",
                  ed5.text == r"C:\drop\target file.md")
            ed5.text = ""
            app5.post_message(Paste('"C:\\a b" "C:\\c d"'))
            await p5.pause(0.8)
            check("多文件拖入只落一次", ed5.text == "C:\\a b C:\\c d")
            # Ctrl+A = 全选 (TextArea 原生把 ctrl+a 绑成"到行首", 全选挂在 F7 上)
            ed5.text = "abc\ndef\nghi"
            ed5.cursor_location = (1, 1)
            await p5.pause(0.2)
            await p5.press("ctrl+a")
            await p5.pause(0.3)
            check("Ctrl+A 全选", ed5.selected_text == "abc\ndef\nghi")
            await p5.press("z")
            await p5.pause(0.3)
            check("Ctrl+A 后打字整体替换", ed5.text == "z")
            await p5.press("end")
            await p5.press("home")
            await p5.pause(0.3)
            check("Home 仍是行首", ed5.cursor_location[1] == 0)
            await p5.pause(0.3)

        # 打开/另存为的路径输入框同样只落一次
        app6 = VIMDApp()
        async with app6.run_test(size=(100, 30)) as p6:
            await p6.pause(0.6)
            app6.push_screen(PathPrompt("打开文件", "C:/"))
            await p6.pause(0.3)
            inp = app6.screen.query_one(Input)
            inp.focus()
            await p6.pause(0.2)
            app6.post_message(Paste(r'"C:\a b\x.md"'))
            await p6.pause(0.8)
            check("路径输入框粘贴只落一次",
                  inp.value.count(r"C:\a b\x.md") == 1 and '"' not in inp.value)

    await paste_round()

    # ── 预览增量重建: 结果必须与全量渲染逐块一致 ──
    INCR = SCRATCH / "incr.md"
    INCR.write_text("# 头\n\n段落一\n\n- a\n- b\n\n```py\nx = 1\n```\n\n尾段\n",
                    encoding="utf-8")

    async def incr_round():
        app8 = VIMDApp(path=str(INCR))
        async with app8.run_test(size=(100, 30)) as p8:
            await p8.pause(0.7)
            prev8 = app8.query_one(Preview)
            ed8 = app8.query_one(Editor)

            def snap():
                return [(type(b).__name__, b.source_range,
                         prev8._markdown[s:e].strip())
                        for b in prev8.children for s, e in [b.source_range]]

            def snapdiff(a, b):
                if a == b:
                    return ""
                for i in range(max(len(a), len(b))):
                    x = a[i] if i < len(a) else None
                    y = b[i] if i < len(b) else None
                    if x != y:
                        return (f" 第{i}块 增量={x} 全量={y}"
                                f" (块数 {len(a)} vs {len(b)})")
                return f" (块数 {len(a)} vs {len(b)})"

            for label, mutate in (
                ("末尾追加", lambda t: t + "\n新尾段\n"),
                ("中间插行", lambda t: t.replace("段落一", "插入行\n\n段落一", 1)),
                ("加列表项", lambda t: t.replace("- b", "- b\n- c", 1)),
                ("改围栏", lambda t: t.replace("x = 1", "x = 2\ny = 3", 1)),
                ("删尾段", lambda t: t.replace("尾段", "", 1)),
            ):
                # 关掉 app 自己的防抖: 靠 pause 等它渲完是会取样到"挂了一半"的中间态
                # (实测 5 块 vs 6 块就是这么来的), 这里自己 await 到挂载完成
                ed8.text = mutate(ed8.text)
                await p8.pause(0.05)
                saved_debounce = appmod.PREVIEW_DEBOUNCE
                appmod.PREVIEW_DEBOUNCE = 10_000
                try:
                    prev8._source = None          # 绕开同文去重, 显式跑增量那一路
                    await prev8.update(ed8.text)
                    incremental = snap()
                    prev8._body, prev8._source = None, None  # 强制全量重渲做对照
                    await prev8.update(ed8.text)
                    full = snap()
                finally:
                    appmod.PREVIEW_DEBOUNCE = saved_debounce
                check(f"增量渲染 == 全量 ({label}){snapdiff(incremental, full)}",
                      incremental == full)

    await incr_round()

    # ── []()/![]() 本地文件统一 emoji, 网址不动 ──
    from vimd.preview import linkify_local_dests as lfd

    check("网址链接不动",
          lfd("[官网](https://example.com)", Path.cwd())
          == "[官网](https://example.com)")
    check("本地 pdf 变 emoji", lfd("[x](C:\\a\\b.pdf)", Path.cwd())
          == "[📕](C:\\a\\b.pdf)")
    check("图片语法变 emoji",
          lfd("![图](C:\\pics\\a.png)", Path.cwd()) == "[🖼️](C:\\pics\\a.png)")
    check("目录变 📁",
          lfd("[目录](<C:/My Dir/>)", Path.cwd()) == "[📁](<C:/My Dir/>)")
    check("锚点/邮件不动", lfd("[锚](#s)[m](mailto:a@b.c)", Path.cwd())
          == "[锚](#s)[m](mailto:a@b.c)")
    check("emoji 规则围栏内不动",
          lfd("```\n[x](C:/a.zip)\n```", Path.cwd())
          == "```\n[x](C:/a.zip)\n```")

    # ── T0 回归: A 的草稿绝不能在打开 B 时弹出/被清 ──
    import json as _json

    key_a = __import__("os").path.normcase(
        str(SCRATCH / "other-file.md"))
    RECOVERY.parent.mkdir(parents=True, exist_ok=True)
    RECOVERY.write_text(_json.dumps({"entries": {
        key_a: {"path": str(SCRATCH / "other-file.md"),
                "content": "A 的未保存草稿"},
    }}, ensure_ascii=False), encoding="utf-8")

    async def t0_round():
        app3 = VIMDApp(path=str(SAMPLE))
        async with app3.run_test(size=(100, 30)) as p3:
            await p3.pause(0.7)
            ed3 = app3.query_one(Editor)
            check("T0: 打开 B 不弹 A 的恢复框",
                  not isinstance(app3.screen, RecoveryPrompt))
            check("T0: B 内容未被 A 草稿覆盖", "MDPad TUI" in ed3.text)
            check("T0: 底栏无恢复按钮",
                  not app3.query_one("#hint-recover").display)
            await p3.press("ctrl+s")
            await p3.pause(0.5)

    await t0_round()
    after = _json.loads(RECOVERY.read_text(encoding="utf-8"))
    check("T0: A 的草稿仍在盘上",
          key_a in after.get("entries", {})
          and after["entries"][key_a]["content"] == "A 的未保存草稿")
    # 再打开 A 自己 -> 应弹恢复框
    async def t0_round2():
        app4 = VIMDApp(path=str(SCRATCH / "other-file.md"))
        SCRATCH.joinpath("other-file.md").write_text(
            "磁盘上的 B 内容", encoding="utf-8")
        async with app4.run_test(size=(100, 30)) as p4:
            await p4.pause(0.7)
            check("T0: 打开 A 本人弹自己的恢复框",
                  isinstance(app4.screen, RecoveryPrompt))
            await p4.press("escape")

    await t0_round2()
    try:
        RECOVERY.unlink()
    except OSError:
        pass

    # ── Alt+S 重新加载本文件 (外部程序改过盘上文件时手动同步) ──
    async def reload_round():
        target = SCRATCH / "reload.md"
        target.write_text("盘上第一版\n", encoding="utf-8")
        app7 = VIMDApp(path=str(target))
        async with app7.run_test(size=(100, 30)) as p7:
            await p7.pause(0.6)
            ed7 = app7.query_one(Editor)
            check("重载: 先看到盘上第一版", "盘上第一版" in ed7.text)
            # 外部 (agent) 直接改盘上的文件, VIMD 这边没跟上
            target.write_text("盘上第二版\n", encoding="utf-8")
            await p7.press("alt+s")
            await p7.pause(0.4)
            check("Alt+S 拉回盘上新内容", "盘上第二版" in ed7.text)
            check("重载后回到干净态", ed7.text == app7._saved_text)
            check("重载后无恢复按钮",
                  not app7.query_one("#hint-recover").display)
            # 脏态: 先问, 取消则什么都不动
            ed7.text = ed7.text + "本地没保存的改动\n"
            await p7.pause(0.3)
            await p7.press("alt+s")
            await p7.pause(0.3)
            check("脏态 Alt+S 弹确认", isinstance(app7.screen, QuitConfirm))
            check("确认文案是重载",
                  "重载" in str(app7.screen.query_one("#save").label))
            await p7.press("escape")
            await p7.pause(0.3)
            check("取消重载保留本地改动", "本地没保存的改动" in ed7.text)
            # 不保存重载: 丢改动, 回盘上内容
            await p7.press("alt+s")
            await p7.pause(0.3)
            await p7.click("#discard")
            await p7.pause(0.4)
            check("不保存重载: 丢本地改动、回盘上内容、干净",
                  "本地没保存的改动" not in ed7.text
                  and "盘上第二版" in ed7.text
                  and ed7.text == app7._saved_text)

        # 盘上还没有这个文件 (路径参数给了个新名字): 只提示, 不弹确认不崩
        app8 = VIMDApp(path=str(SCRATCH / "not-there.md"))
        async with app8.run_test(size=(100, 30)) as p8:
            await p8.pause(0.5)
            await p8.press("alt+s")
            await p8.pause(0.3)
            check("文件不在盘上: 不弹确认也不崩",
                  not isinstance(app8.screen, QuitConfirm)
                  and p8.app.query_one(Editor).text == "")

    await reload_round()

    # ── 预览跟随滚动 (只向下: 光标被推到预览底边才跟着走) ──
    LONG = SCRATCH / "follow.md"
    LONG.write_text(
        "# 跟随\n\n"
        + "\n\n".join(f"第 {i} 段: 中文内容, 用来把预览撑得比屏幕高。" for i in range(1, 61))
        + "\n",
        encoding="utf-8",
    )

    async def scroll_round():
        app5 = VIMDApp(path=str(LONG))
        async with app5.run_test(size=(120, 40)) as p5:
            await p5.pause(0.7)
            ed5 = app5.query_one(Editor)
            scr5 = app5.query_one("#preview-scroll")
            prev5 = app5.query_one(Preview)
            await p5.press("alt+3")
            await p5.pause(0.3)
            bottom = scr5.scrollable_content_region.bottom - 1  # 必须分屏后再取

            def anchor_y(line: int) -> int:
                hit = prev5.block_for_line(line)
                if hit is None:
                    return -999
                block, frac = hit
                return round(block.region.y + frac * (block.region.height - 1))

            await p5.press("alt+3")
            await p5.pause(0.3)
            check("跟随: 分屏就位", bool(ed5.display) and bool(scr5.display))
            check("跟随: 预览比屏幕高", scr5.max_scroll_y > 0)
            ed5.cursor_location = (0, 0)
            await p5.pause(0.3)
            check("跟随: 光标在头部时预览不动", scr5.scroll_y == 0)
            mid = len(ed5.text.splitlines()) // 2
            ed5.cursor_location = (mid, 0)
            await p5.pause(0.3)
            check(f"跟随: 光标下移即下滚 (scroll={scr5.scroll_y:.0f})",
                  scr5.scroll_y > 0)
            check("跟随: 光标行贴在预览底边", anchor_y(mid) == bottom)
            # 同一帧重复同步不得照着旧位置再滚一遍 (region/scroll_y 滞后一帧的坑)
            y_mid = scr5.scroll_y
            scr5.sync_caret()
            scr5.sync_caret()
            await p5.pause(0.3)
            check("跟随: 同帧重复调用幂等", scr5.scroll_y == y_mid)
            check("跟随: 重复调用后光标行仍贴底边", anchor_y(mid) == bottom)
            last = len(ed5.text.splitlines()) - 1
            ed5.cursor_location = (last, 0)
            await p5.pause(0.3)
            check("跟随: 不越界", scr5.scroll_y <= scr5.max_scroll_y)
            check("跟随: 末尾光标仍贴底边", anchor_y(last) == bottom)
            y_last = scr5.scroll_y
            ed5.cursor_location = (mid, 0)
            await p5.pause(0.3)
            check("跟随: 光标上移不回滚", scr5.scroll_y == y_last)
            check("跟随: 上移后光标行仍在可视区内", anchor_y(mid) <= bottom)
            ed5.cursor_location = (last, 0)
            await p5.pause(0.2)
            await p5.press("enter", "跟", "随")
            await p5.pause(0.7)
            check("跟随: 打字后光标行仍贴底边",
                  anchor_y(ed5.cursor_location[0]) == bottom)
            # 手动滚开 -> 停跟: 同样的打字不再拽预览, 光标行落到可视区外
            scr5.scroll_to(y=0, animate=False)
            await p5.pause(0.2)
            check("停跟: 滚离底部即停跟", scr5.follow is False)
            await p5.press("enter", "停", "住")
            await p5.pause(0.7)
            check("停跟: 打字不再拽预览", scr5.scroll_y == 0)
            check("停跟: 光标行落到可视区外",
                  anchor_y(ed5.cursor_location[0]) > bottom)
            # 滚回底部 -> 恢复跟随: 同样是打字, 光标行重新落回可视区内
            scr5.scroll_to(y=scr5.max_scroll_y, animate=False)
            await p5.pause(0.2)
            check("恢复: 滚到底部重新跟随", scr5.follow is True)
            ed5.cursor_location = (last, 0)
            await p5.pause(0.3)
            check("恢复: 光标行回到可视区内",
                  anchor_y(last) <= bottom)
            await p5.press("enter", "跟", "上")
            await p5.pause(0.7)
            check("恢复: 打字后光标行仍在可视区内",
                  anchor_y(ed5.cursor_location[0]) <= bottom)
            # 换文件 -> 预览回顶部 + 跟随重新武装 (停跟状态不跨文件)
            scr5.scroll_to(y=0, animate=False)
            await p5.pause(0.2)
            app5._open_file(SAMPLE)
            await p5.pause(0.7)
            check("换文件: 预览回顶部且重新跟随",
                  scr5.scroll_y == 0 and scr5.follow is True)
            await p5.press("alt+2")
            await p5.pause(0.2)
            check("纯预览 (Alt+2) 不接管滚动", scr5.follow in (True, False))

    await scroll_round()

    # ── 源码行 -> 渲染块的映射 (嵌套 / 空行 / 围栏 / 表格 / 截断偏移) ──
    NEST = SCRATCH / "nest.md"
    NEST.write_text(
        "# 标题\n\n"
        "- 列表项一\n"
        "  - 嵌套项 a\n"
        "  - 嵌套项 b\n\n"
        "| a | b |\n|---|---|\n| 1 | 2 |\n\n"
        "```py\nx = 1\ny = 2\n```\n",
        encoding="utf-8",
    )

    async def map_round():
        app6 = VIMDApp(path=str(NEST))
        async with app6.run_test(size=(100, 30)) as p6:
            await p6.pause(0.7)
            prev6 = app6.query_one(Preview)
            hit = prev6.block_for_line(3)  # 嵌套项 a
            check("映射: 嵌套行取最深的块",
                  hit is not None
                  and type(hit[0]).__name__ == "MarkdownParagraph"
                  and hit[0].source_range == (3, 4)
                  and hit[0].parent is not prev6)
            check("映射: 空行退到最近块 (不丢锚点)",
                  prev6.block_for_line(5) is not None)
            fence = [prev6.block_for_line(i) for i in (10, 11, 12)]
            check("映射: 围栏内同一块且比例递增",
                  all(h is not None and type(h[0]).__name__ == "MarkdownFence"
                      for h in fence)
                  and fence[0][0] is fence[2][0]
                  and fence[0][1] < fence[2][1])
            table = [prev6.block_for_line(i) for i in (6, 7, 8)]
            check("映射: 表格行比例递增",
                  all(h is not None for h in table)
                  and table[0][1] < table[2][1])

    await map_round()

    async def trunc_round():
        import vimd.preview as prevmod
        saved_max = prevmod.PREVIEW_MAX
        prevmod.PREVIEW_MAX = 200  # 手工调小截断阈值, 免得为测试造 26 万字符
        BIG = SCRATCH / "trunc.md"
        BIG.write_text(
            "# 头\n\n" + "\n\n".join(f"第{i}段 " + "x" * 40 for i in range(40)),
            encoding="utf-8",
        )
        try:
            app7 = VIMDApp(path=str(BIG))
            async with app7.run_test(size=(100, 30)) as p7:
                await p7.pause(0.7)
                prev7 = app7.query_one(Preview)
                check("截断: 提示占 2 行 -> 偏移记 2", prev7._line_offset == 2)
                check("截断: 偏移后的正文行仍能定位块",
                      prev7.block_for_line(2) is not None)
                check("截断: 截断点之外的尾行退到最近块",
                      prev7.block_for_line(500) is not None)
        finally:
            prevmod.PREVIEW_MAX = saved_max

    async def singleton_round():
        """单例 (按文件): 键归一 / 独占与可见性 / 换文件 / 启动器判定 / app 侧登记。"""
        import time

        from vimd import launcher as lc
        from vimd import singleton as sg

        k_sample = sg.singleton_key(SAMPLE)
        check("单例: 相对/绝对/大小写/.. 归一到同一个键",
              k_sample == sg.singleton_key(str(SAMPLE).replace("\\", "/"))
              and k_sample == sg.singleton_key(f"{SCRATCH.as_posix()}/sample.md")
              and k_sample == sg.singleton_key(
                  f"{SCRATCH.as_posix()}/x/../sample.md"))
        check("单例: 未命名与文件不是同一个键",
              sg.singleton_key(None) == "" and sg.singleton_key("") == ""
              and "" != k_sample)

        first = sg.DocClaim()
        check("单例: 首个实例拿得到登记", first.claim(k_sample) and first.held)
        mine = sg.peek(k_sample)
        check("单例: 登记里写着自己的进程号",
              mine is not None and mine.pid == os.getpid())
        check("单例: 无控制台时窗口句柄明确为 0 (不是脏数据)",
              mine.hwnd == sg.own_console_hwnd())
        second = sg.DocClaim()
        check("单例: 同一个文件拿不到第二次", not second.claim(k_sample))
        check("单例: 拿不到的人不留下 key", second.key is None)

        other = SCRATCH / "singleton-other.md"
        other.write_text("# 另一个\n\n正文\n", encoding="utf-8")
        k_other = sg.singleton_key(other)
        check("单例: 换文件 -> 新键归我、旧键同时放掉",
              first.claim(k_other) and sg.peek(k_sample) is None
              and sg.peek(k_other).pid == os.getpid())
        first.release()
        check("单例: 释放后键从单例表消失", sg.peek(k_other) is None)

        check("单例: 无效句柄一律不谎报唤醒成功",
              sg.wake(0) is False and sg.wake(12345678) is False)
        check("单例: 没有窗口句柄时不误判为已唤醒",
              sg.wake_if_open(k_sample) is False)
        check("单例: 没有自己的窗口时不乱藏窗口", sg.hide_own_window() is False)

        t0 = time.monotonic()
        check("启动器: 没人开着 -> 立刻决定开窗口 (不空等)",
              lc._peek_retry(k_sample) is None
              and time.monotonic() - t0 < 0.2)
        check("启动器: 同目录能找到 TUI 入口", lc._tui_target() is not None)

        holder = sg.DocClaim()
        holder.claim(k_sample)
        spawned = []
        real_spawn, real_wake = lc._spawn_tui, lc.wake
        lc._spawn_tui = lambda argv: (spawned.append(list(argv)), 0)[1]
        try:
            lc.wake = lambda hwnd: True
            check("启动器: 已有窗口且抬起来了 -> 不再开窗口",
                  lc.main([str(SAMPLE)]) == 0 and spawned == [])
            lc.wake = lambda hwnd: False
            check("启动器: 抬不动也照常开窗口 (不把用户晾着)",
                  lc.main([str(SAMPLE)]) == 0
                  and spawned and spawned[0] == [str(SAMPLE)])
        finally:
            lc._spawn_tui, lc.wake = real_spawn, real_wake
        holder.release()
        before = len(spawned)
        lc._spawn_tui = lambda argv: (spawned.append(list(argv)), 0)[1]
        try:
            lc.main([str(SAMPLE)])
        finally:
            lc._spawn_tui = real_spawn
        check("启动器: 没人开着 -> 开窗口", len(spawned) == before + 1)

        doc = SCRATCH / "singleton-doc.md"
        doc.write_text("# 单例\n\n正文\n", encoding="utf-8")
        k_doc = sg.singleton_key(doc)
        app9 = VIMDApp(path=str(doc))
        async with app9.run_test(size=(100, 30)) as p9:
            await p9.pause(0.5)
            check("单例: app 登记的就是当前文件",
                  app9.claim.held and app9.claim.key == k_doc
                  and sg.peek(k_doc).pid == os.getpid())
            app9._open_file(other)
            await p9.pause(0.5)
            check("单例: 换文件后登记跟着走, 旧键放掉",
                  app9.claim.key == k_other and sg.peek(k_doc) is None
                  and sg.peek(k_other).pid == os.getpid())
            await p9.press("ctrl+n")
            await p9.pause(0.5)
            check("单例: 新建后登记换成未命名键 (键 \"\")",
                  app9.claim.key == "" and sg.peek("").pid == os.getpid())
        check("单例: app 退出后登记归还",
              sg.peek("") is None and sg.peek(k_other) is None)

        squatter = sg.DocClaim()
        squatter.claim(k_doc)
        notes = []
        app10 = VIMDApp(path=str(doc))
        app10.notify = lambda *a, **k: notes.append((a, k))
        async with app10.run_test(size=(100, 30)) as p10:
            await p10.pause(0.5)
            check("单例: 别人开着时不抢登记 (不把对面顶掉)",
                  not app10.claim.held and squatter.held)
            check("单例: 别人开着也照常打开文件",
                  "单例" in app10.query_one(Editor).text)
            check("单例: 别人开着时提醒一句会互相覆盖",
                  any("已在另一个 VIMD 窗口打开" in str(a)
                      and k.get("severity") == "warning" for a, k in notes))
            check("单例: 提醒后仍然不登记", not app10.claim.held)
        squatter.release()
        check("单例: 对面退出后这个文件重新可登记", sg.peek(k_doc) is None)

    await singleton_round()

    await trunc_round()

    # WrappedDocument.height 的 O(1) 补丁 (vimd/editor.py::_patch_wrapped_height)
    # 必须与朴素 sum 逐值恒等 —— 它被拿去算 virtual_size 与 out_of_bounds, 不等就把
    # 渲染搞崩。三种状态各钉一次: 首次折行 / 单行增量 / 关折行。
    from textual.document._document import Document as _Doc
    from textual.document._wrapped_document import WrappedDocument as _WD

    def _naive_height(w):
        return sum(len(o) + 1 for o in w._wrap_offsets)

    _d = _Doc("很长的一行" * 40 + "\n第二行\n\n" + "x " * 90)
    _w = _WD(_d, 0)
    _w.wrap(20)
    check("height 补丁: 首折与朴素 sum 恒等",
          _w.height == _naive_height(_w) == len(_w._offset_to_line_info))
    _inserted = "插一段很长的文字" * 10
    _d.replace_range((0, 0), (0, 0), _inserted)
    _w.wrap_range((0, 0), (0, 0), (0, len(_inserted)))
    check("height 补丁: 增量折行后仍恒等",
          _w.height == _naive_height(_w) == len(_w._offset_to_line_info))
    _w.wrap(0)
    check("height 补丁: 关折行 (width=0) 仍恒等",
          _w.height == _naive_height(_w) == len(_w._offset_to_line_info))

    print("=" * 40)
    if failures:
        print("FAILURES:", failures)
        sys.exit(1)
    print("全部通过")


asyncio.run(main())
