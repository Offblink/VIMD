"""发布版 TUI 入口 (控制台 exe, 由 VIMD.exe 启动器拉起; 也可以直接跑)。

用法:
    python vimd_tui.py 文档.md
"""
import sys

from vimd.tui import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
