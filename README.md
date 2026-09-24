# VIMD

**VIM + MD** — 在终端里编辑 Markdown，基于 [Textual](https://textual.textualize.io/)。

## 安装与运行

```bash
pip install -r requirements.txt
python vimd.py 文档.md
```

## 功能

- 三视图：编辑 `F2` / 预览 `F3` / 分屏 `F4`，首次启动默认分屏，选择会被记住
- 预览面板可滚动：方向键、PgUp、PgDn、Home、End，鼠标滚轮随时可用
- 超大文档的预览只渲染前 26 万字符，打开和打字不卡；编辑、保存不受影响
- 编辑区自动换行：文字到边即折行，没有横向滚动；行号按显示行递增
- 格式快捷键作用于选中内容，再按一次取消：加粗、斜体、代码块、链接、内嵌图片
- 打开和另存为使用系统文件对话框；对话框不可用时回退为内置路径输入
- 查找替换弹窗（右下角）：区分大小写勾选、替换当前、全部替换；关窗后 `Ctrl+G` 继续查找
- 帮助窗口内可切换是否显示行号
- 预览中的链接和图片用系统默认程序打开
- 文件打开期间不可被外部移动、删除或改名；支持 UTF-8、GBK 等多种编码
- 顶栏显示文件名与标题，底栏左侧是键位、右侧行列号
- 退出或新建前对未保存的修改进行确认

## 快捷键

| 分类 | 按键 |
|---|---|
| 文件 | `Ctrl+N` 新建 · `Ctrl+O` 打开 · `Ctrl+S` 保存 · `Ctrl+Shift+S` 另存为 |
| 视图 | `F2` 编辑 · `F3` 预览 · `F4` 分屏 |
| 格式 | `Ctrl+B` 加粗 · `Ctrl+I` 斜体 · `Ctrl+K` 代码块 · `Ctrl+L` 链接 · `Ctrl+Shift+L` 图片 |
| 查找 | `Ctrl+F` 打开 · `Enter` 下一个 · `Shift+Enter` 上一个 · `Ctrl+G` 继续查找 |
| 编辑 | `Ctrl+Z` 撤销 · `Ctrl+Y` 重做 |
| 其他 | `Ctrl+H` 帮助 · `Ctrl+Q` 退出 |

## 标题与图标

- 启动后，终端标签标题显示为 **VIMD**
- 图标文件：`icon.svg`（矢量源）、`icon.png`、`icon.ico`（16–256 多尺寸）
- 图案：深色圆角方块、白色 **V**、绿色块状光标（VIM）、蓝色 **#**（Markdown）
- 在 Windows Terminal 的标签上显示图标：设置 → 配置文件 → 图标 → 选择 `icon.png`
- 打包成 exe 时在 PyInstaller spec 中指定 `icon='icon.ico'`

## 说明

- 斜体用 `Ctrl+I`：需要终端支持 kitty 键盘协议（Windows Terminal 支持）；不支持的终端改用 `Alt+I`
- 中文由终端输入法处理，组词完成后整词上屏
- 编辑区语法高亮需要安装 tree-sitter 的 Markdown 语法包，未安装时为纯文本，不影响预览渲染
- 文件锁定与编码探测逻辑取自 [MDPad](https://github.com/Offblink/MDPad)
