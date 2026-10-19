# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置。

    python -m PyInstaller --clean --noconfirm desktop/app.spec

产物：dist/Sanitized_Data/（onedir，启动快，不会每次解压）
"""
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH).resolve().parent        # Sanitized_Data/
APP = ROOT / "app"
ENTRY = ROOT / "desktop" / "main.py"

datas = [
    (str(APP / "web"), "web"),                                   # 前端
    (str(APP / "server" / "yinzhan" / "rules"), "yinzhan/rules"),  # 规则包
]
binaries = []
hiddenimports = collect_submodules("yinzhan")                    # 动态加载的解析器/检测器

# 这两个包用动态导入，静态分析会漏，必须 collect_all
for pkg in ("rapidocr", "onnxruntime"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# 窗口外壳
hiddenimports += [
    "webview", "webview.platforms.edgechromium", "webview.platforms.cocoa",
    "clr_loader", "pythonnet", "clr",
]

block_cipher = None

a = Analysis(
    [str(ENTRY)],
    pathex=[str(APP / "server"), str(ROOT / "desktop")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "pytest", "IPython", "notebook"],
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="Sanitized_Data",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe, a.binaries, a.zipfiles, a.datas,
    strip=False, upx=False, upx_exclude=[],
    name="Sanitized_Data",
)

# macOS: 生成 .app 包（需要 BUNDLE）
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="Sanitized_Data.app",
        icon=None,
        bundle_identifier="com.blctek.sanitizeddata",
        info_plist={
            "CFBundleName": "数据隐站",
            "CFBundleDisplayName": "数据隐站",
            "CFBundleShortVersionString": "1.0.0",
            "CFBundleVersion": "1.0.0",
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
            "NSRequiresAquaSystemAppearance": False,
        },
    )
