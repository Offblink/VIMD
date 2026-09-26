"""编辑区 — TextArea 封装。

markdown 语法高亮需要对应 tree-sitter 语法包；装了就用，没装就纯文本
（与 GUI 版持平：QTextEdit 本来也没有编辑区高亮）。
撤销/重做由 TextArea 内置绑定提供 (ctrl+z / ctrl+y, _text_area.py:287)。
"""

from __future__ import annotations

from rich.segment import Segment

from textual.binding import Binding
from textual.events import Paste
from textual.strip import Strip
from textual.widgets import TextArea
from textual.widgets._text_area import LanguageDoesNotExist

from . import formatting
from .io import unquote_dropped_path


def _markdown_language_or_none() -> str | None:
    """探测 markdown 语法包是否可用；不可用返回 None 而非报错。"""
    try:
        TextArea(language="markdown")
    except LanguageDoesNotExist:
        return None
    return "markdown"


class Editor(TextArea):
    """Markdown 文档编辑区。"""

    #: 行号列总宽(列) — 恒定: 位数进位 (100/1000) 时压缩行号与正文的间隔,
    #: 不加宽整列, 正文不整体右移、右边框不会被顶出屏幕 (用户定的方案)
    GUTTER_TOTAL = 7
    #: 间隔下限 — 原生 gutter 段按 margin=2 分段 (_text_area.py:1434),
    #: 低于 2 连原生段自己都会比 gutter_width 宽, 所以底线必须是 2
    GUTTER_GAP_MIN = 2

    BINDINGS = [
        # TextArea 原生把 ctrl+a 绑成"到行首"+ 全选挂在 F7 (_text_area.py:226/259),
        # 跟编辑器通用直觉不符 → ctrl+a 改绑全选 (home 仍是行首, F7 也照旧能用)
        Binding("ctrl+a", "select_all", "全选", show=False),
    ]

    def __init__(self, **kwargs) -> None:
        super().__init__(
            language=_markdown_language_or_none(),
            show_line_numbers=True,
            soft_wrap=True,
            **kwargs,
        )

    async def on_event(self, event) -> None:
        """拖入 WT 的路径带引号 -> 落编辑器前去掉 (见 io.unquote_dropped_path)。

        只改 event.text, 插入交给 TextArea 自己做 — 千万别覆写 `_on_paste` 再
        `super()._on_paste()`: Textual 派发消息时按 **MRO 逐类**取 `_on_paste`
        (message_pump.py:780), 基类那份本来就会被单独调一次, 子类再调一次 = 插两遍;
        再叠上 App 对非转发粘贴的二次转发, 一个拖放路径会长出 4 份。
        on_event 在派发之前跑 (message_pump.py:802), 改完走原逻辑正好插一次。
        """
        if isinstance(event, Paste):
            event.text = unquote_dropped_path(event.text)
        await super().on_event(event)

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

    def _gutter_digits(self) -> int:
        """行号位数 — 必须按**视觉行总数**算, 不能用逻辑行数。

        `render_line` 显示的是视觉行号 (y + scroll_y + start), 软换行后视觉行数 >
        逻辑行数: 按逻辑行数留位会差 1 位 → 行号段实际比 `gutter_width` 宽 1 格,
        而 Strip.cell_length 仍按旧值声明 → 那一整行渲染宽出 1 格 → 控件最右一列
        (聚焦时变蓝的 tall 右边框) 被顶出屏幕 (2026-09-26 实证: 行号到 100 起右边框消失)。
        """
        total = max(self.document.line_count, self.wrapped_document.height, 1)
        return len(str(total - 1 + self.line_number_start))

    @property
    def gutter_gap(self) -> int:
        """行号与正文的间隔 — 动态: 位数少间隔大, 位数多间隔小 (总宽尽量恒定)。"""
        return max(self.GUTTER_GAP_MIN, self.GUTTER_TOTAL - self._gutter_digits())

    @property
    def gutter_width(self) -> int:
        """行号列宽 = 位数 + 动态间隔; 总宽锁在 `GUTTER_TOTAL`, 位数进位由间隔吸收。

        覆写 Textual 的原生实现, 它把间距写死成 margin=2 (_text_area.py:1762)。
        改这个属性而不是在 render_line 里塞空格: wrap_width、虚拟尺寸、鼠标命中
        换算 (_text_area.py:1749)、正文左移都读它, 在一处加宽即全局一致。
        """
        if not self.show_line_numbers:
            return 0
        return self._gutter_digits() + self.gutter_gap

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
                # 行号右对齐占满「位数」列, 其余留给动态间隔 (总宽恒定)
                gap = self.gutter_gap
                width = self.gutter_width - gap  # == 位数
                text = (
                    f"{str(y + int(self.scroll_y) + self.line_number_start):>{width}}"
                    f"{' ' * gap}"
                )
                # 防御: 行号比预留位数还长时 f-string 不截断 → 整行会宽出
                # gutter_width, 右边框被顶出屏幕 → 宁可保留原生段 (恒等宽)
                if len(text) == self.gutter_width:
                    segments[0] = Segment(text, first.style, first.control)
                    strip = Strip(segments, strip.cell_length)
        return strip
