# -*- mode: python ; coding: utf-8 -*-
# VIMD 发版打包: onedir + 两个 exe (独立构建 venv 见 .venv-build/)
#   VIMD.exe     窗口化启动器 — 判单例 / 唤醒已有窗口, 自己不开任何窗口。
#                必须是无控制台的窗口化进程: 控制台由系统在代码跑起来之前就建好了,
#                控制台 exe 做判重必然先闪一个 WT 窗口 (见 vimd/launcher.py)。
#   VIMD-tui.exe 控制台 TUI — 编辑器本体, 由启动器拉起 (或直接跑)。
tui = Analysis(
    ["vimd_tui.py"],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter"],
    noarchive=False,
)
launcher = Analysis(
    ["vimd.py"],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # 启动器是窗口化进程, 冻结构建下永远走 launcher 那条路 (vimd.py 里的
    # `from vimd.tui import main` 只在源码运行时执行) — 不排除的话 PYZ 里会
    # 白白再塞一份 Textual/markdown, 整个包大 8MB
    excludes=["tkinter", "vimd.tui"],
    noarchive=False,
)


def _unique(entries):
    """两个 Analysis 会扫出同一批依赖: 按目标名去重, 免得 COLLECT 里打架。"""
    seen = set()
    merged = []
    for entry in entries:
        if entry[0] not in seen:
            seen.add(entry[0])
            merged.append(entry)
    return merged


tui_pyz = PYZ(tui.pure)
launcher_pyz = PYZ(launcher.pure)

exe_tui = EXE(
    tui_pyz,
    tui.scripts,
    [],
    exclude_binaries=True,
    name="VIMD-tui",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=["icon.ico"],
)
exe_launcher = EXE(
    launcher_pyz,
    launcher.scripts,
    [],
    exclude_binaries=True,
    name="VIMD",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=["icon.ico"],
)
coll = COLLECT(
    exe_tui,
    exe_launcher,
    _unique(tui.binaries + launcher.binaries),
    _unique(tui.datas + launcher.datas),
    strip=False,
    upx=False,
    name="VIMD",
)
