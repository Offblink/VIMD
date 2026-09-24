"""VIMD 入口 — 终端 Markdown 编辑器 (VIM + MD)。

用法:
    python vimd.py [文档.md]
"""
import sys

from vimd.app import VIMDApp


def main() -> None:
    app = VIMDApp(path=sys.argv[1] if len(sys.argv) > 1 else None)
    app.run()
    app.file_guard.release()


if __name__ == "__main__":
    main()
