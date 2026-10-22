# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置。

    python -m PyInstaller --clean --noconfirm desktop/app.spec

产物：dist/Sanitized_Data/（onedir，启动快，不会每次解压）
"""
import re
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

ROOT = Path(SPECPATH).resolve().parent        # Sanitized_Data/
APP = ROOT / "app"
ENTRY = ROOT / "desktop" / "main.py"


def _app_version() -> str:
    """单一版本源：app/server/yinzhan/config.py 的 VERSION（Windows/macOS 共用）。"""
    text = (APP / "server" / "yinzhan" / "config.py").read_text(encoding="utf-8")
    match = re.search(r'^VERSION\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not match:
        raise SystemExit("无法从 config.py 解析 VERSION")
    return match.group(1)


APP_VERSION = _app_version()
print(f"[spec] APP_VERSION = {APP_VERSION}")

# 图标资产：唯一图源 desktop/assets/app.svg，派生 app.ico / app.icns（重建方法见
# docs/research/06-图标适配检查与替换记录.md）。Windows 用 ico，macOS 用 icns。
ASSETS = ROOT / "desktop" / "assets"

# 关键：collect_submodules 在 spec 求值时就执行，必须先把 app/server 放进 sys.path，
# 否则收集结果为空 → 插件模块不会被打进包 → 运行时 0 个解析器。
if str(APP / "server") not in sys.path:
    sys.path.insert(0, str(APP / "server"))

datas = [
    (str(APP / "web"), "web"),                                   # 前端
    (str(APP / "server" / "yinzhan" / "rules"), "yinzhan/rules"),  # 规则包
    # YuNet 人脸检测模型（232KB，OpenCV Zoo MIT；随包离线推理）
    (str(APP / "server" / "yinzhan" / "models"), "yinzhan/models"),
]
binaries = []
hiddenimports = collect_submodules("yinzhan")                    # 动态加载的解析器/检测器
print(f"[spec] 收集到 yinzhan 子模块 {len(hiddenimports)} 个")

# 这几个包用动态导入/二进制扩展，静态分析会漏，必须 collect_all。
# zxingcpp 是条码/二维码识别引擎（Apache-2.0），模型/数据随 wheel 分发、运行时不联网。
# rasterio/pyproj 是正射 TIF 中性化/大图处理的可选依赖（BSD-3 / MIT）。
for pkg in ("rapidocr", "onnxruntime", "zxingcpp", "rasterio", "pyproj"):
    try:
        d, b, h = collect_all(pkg)
    except Exception as exc:      # 开发机未装某引擎时不让整个 spec 崩
        print(f"[spec] 跳过 {pkg}: {exc}")
        continue
    datas += d
    binaries += b
    hiddenimports += h

# OpenCV 的 Haar 级联 XML 是**数据文件**（人脸检测用），PyInstaller 的 hook-cv2
# 不会自动收集；缺了以后打包版人脸检测会报 "Can't open ... haarcascade...xml"
# 并静默退化为 0 命中。显式收集 cv2 包数据（约 1MB）。
try:
    cv2_datas = collect_data_files("cv2")
    datas += cv2_datas
    print(f"[spec] cv2 数据文件 {len(cv2_datas)} 个")
except Exception as exc:          # 开发机未装 opencv 时不让 spec 崩
    print(f"[spec] cv2 数据收集失败: {exc}")

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
    # 这些库本产品完全不用，却会被 RapidOCR / onnxruntime 的可选依赖顺手扫进来。
    # 实测不排除的话：torch 253MB + jaxlib 221MB + llvmlite 102MB → 包体积 1058MB。
    excludes=[
        "tkinter", "matplotlib", "pytest", "IPython", "notebook",
        "torch", "torchvision", "torchaudio", "torch.utils",
        "jax", "jaxlib", "llvmlite", "numba",
        "scipy", "sklearn", "pandas", "sympy",
        "tensorflow", "paddle", "paddleocr", "transformers", "datasets",
        "triton", "cupy", "cuda", "onnx", "onnxruntime.tools",
    ],
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
    icon=str(ASSETS / ("app.icns" if sys.platform == "darwin" else "app.ico")),
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
        icon=str(ASSETS / "app.icns"),
        bundle_identifier="com.blctek.sanitizeddata",
        info_plist={
            "CFBundleName": "数据隐站",
            "CFBundleDisplayName": "数据隐站",
            "CFBundleShortVersionString": APP_VERSION,
            "CFBundleVersion": APP_VERSION,
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
            "NSRequiresAquaSystemAppearance": False,
        },
    )
