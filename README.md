# VIMD

**VIM + MD** — 终端里的 Markdown 编辑器，基于 [Textual](https://textual.textualize.io/)。

三视图实时预览、格式快捷键包裹选区、系统文件对话框、查找替换弹窗——纯键盘操作，`Ctrl+H` 看全表。

> 前身为 MDPad 的终端版实验，现独立成项。文件锁、多编码探测、链接分流等纯逻辑沿用 MDPad v2 的实现（`vimd/io.py`、`vimd/links.py`，零 Qt 依赖）。

## 安装与运行

```bash
pip install -r requirements.txt
python vimd.py 文档.md
```

## 功能

- 三视图：编辑（F2）/ 预览（F3）/ 分屏（F4），首启默认分屏、模式持久化
- 预览面板可滚动：方向键 / PgUp / PgDn / Home / End（鼠标滚轮随时可用）
- 实时预览防抖（连续输入合并渲染）
- 格式快捷键包裹选区，再按解开：**加粗** / *斜体* / 代码块 / 链接 / 内嵌图片
- 打开 / 另存为弹**系统文件对话框**（PowerShell + WPF 原生窗口，零额外依赖；不可用时回退内置路径输入，取消则静默收场）
- 查找替换右下弹窗：大小写勾选、替换当前 / 全部替换按钮、实时计数；关窗后 `Ctrl+G` 按最后查询继续跳转；点击弹窗外关闭
- 链接与图片点击按 分类分流 浏览器 / 系统默认程序；页内锚点暂不跳转
- 通知居右上角；状态栏显示 文件名 / 脏标记 / 行列
- 文件打开期间锁定（外部不可移动 / 删除 / 重命名，自己的保存不受影响）
- 多编码自动探测（UTF-8 / GBK / GB2312 / GB18030 / Big5）
- 退出前未保存确认（保存并退出 / 不保存退出 / 取消）

## 快捷键

| 分类 | 操作 | 按键 |
|---|---|---|
| 文件 | 保存 / 另存为 / 打开 | Ctrl+S / Ctrl+Shift+S / Ctrl+O |
| 视图 | 编辑 / 预览 / 分屏 | F2 / F3 / F4 |
| 格式 | 加粗 / 斜体 / 代码块 / 链接 / 内嵌图片 | Ctrl+B / Ctrl+I / Ctrl+K / Ctrl+L / Ctrl+Shift+L |
| 查找 | 弹窗 / 下一个 / 上一个 | Ctrl+F / Enter 或 Ctrl+G / Ctrl+Shift+G |
| 编辑 | 撤销 / 重做 | Ctrl+Z / Ctrl+Y |
| 其他 | 帮助 / 退出 | Ctrl+H / Ctrl+Q |

## 说明

- **斜体 = `Ctrl+I`**：textual 启动时启用 kitty 键盘协议（`windows_driver` 发 `\x1b[>1u`），Windows Terminal 1.24 下 Ctrl+I 以独立序列送达、与 Tab 分开；不支持该协议的老终端里 Ctrl+I 退化为 Tab，此时用兜底键 `Alt+I`（已一并绑定）
- **中文输入**：IME 由 Windows Terminal 在终端层处理，组词完成后整词上屏
- **语法高亮**：安装对应 tree-sitter markdown 语法包后编辑区自动启用，未装则纯文本（不影响预览渲染，与 GUI 版持平）
