"""VIMD 入口 — 终端 Markdown 编辑器 (VIM + MD)。

用法:
    python vimd.py [文档.md]    源码运行: 有终端, 就地起 TUI
    VIMD.exe  [文档.md]         发布版: 窗口化启动器 -> 单例判定 -> VIMD-tui.exe
"""
import sys


def main() -> None:
    if getattr(sys, "frozen", False):
        # 发布版的 VIMD.exe 是窗口化的启动器: 自己没有控制台, 于是"重复打开
        # 同一个文件"能在任何窗口出现之前就被判掉 (见 vimd/launcher.py)
        from vimd.launcher import main as launch

        sys.exit(launch(sys.argv[1:]))
    from vimd.tui import main as run_tui

    sys.exit(run_tui(sys.argv[1:]))


if __name__ == "__main__":
    main()
