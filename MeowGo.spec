# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（跨平台：Windows / macOS / Linux）

直接打包（保持旧行为，Windows 下产物为 dist/MeowGo/MeowGo.exe）：
    python -m PyInstaller --noconfirm "MeowGo.spec"

推荐由构建脚本驱动（自动处理虚拟环境、依赖、平台图标、单/多文件模式）：
    python build.py

build.py 会设置以下环境变量，本文件据此调整：
    MEOWGO_APP_NAME    产物名称（默认 MeowGo）
    MEOWGO_BUILD_MODE  onedir（默认，启动更快）/ onefile（单文件）
    MEOWGO_ICON        预生成的平台图标绝对路径（.ico / .icns）
    MEOWGO_NO_APP      置 1 时，macOS 不额外产出 .app bundle
"""

import os
import sys

IS_WINDOWS = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"

APP_NAME = os.environ.get("MEOWGO_APP_NAME") or "MeowGo"
ONEFILE = os.environ.get("MEOWGO_BUILD_MODE", "").lower() == "onefile"
NO_APP = os.environ.get("MEOWGO_NO_APP") == "1"
# 由 build.py 生成的 Windows 版本资源；其它平台不使用
VERSION_FILE = os.environ.get("MEOWGO_VERSION_FILE")
if not IS_WINDOWS or not VERSION_FILE or not os.path.exists(VERSION_FILE):
    VERSION_FILE = None

block_cipher = None


def _resolve_icon():
    """按平台挑选图标：Windows 用 .ico，macOS 的 .app 用 .icns，Linux 不嵌入。"""
    env_icon = os.environ.get("MEOWGO_ICON")
    if env_icon and os.path.exists(env_icon):
        return env_icon
    if IS_WINDOWS:
        for cand in ("build_assets/MeowGo.ico", "build_assets/main.ico"):
            if os.path.exists(cand):
                return cand
        # PyInstaller 6 可把 PNG 直接转成 ico，兜底保持旧行为
        return "main.png" if os.path.exists("main.png") else None
    if IS_MAC:
        for cand in ("build_assets/MeowGo.icns", "build_assets/main.icns"):
            if os.path.exists(cand):
                return cand
    return None


ICON = _resolve_icon()

a = Analysis(
    ['start-app.py'],
    pathex=['.'],
    binaries=[],
    datas=[('main.png', '.'), ('meowgo_logo_transparent.png', '.')],
    hiddenimports=[
        'rpa', 'rpa.gui', 'rpa.engine', 'rpa.assets', 'rpa.uikit', 'rpa.i18n',
        'rpa.lock', 'rpa.dialogs', 'rpa.recorder', 'rpa.logs', 'rpa.theme',
        'rpa.tray', 'rpa.autostart', 'rpa.ocr', 'rpa.uia', 'rpa.pixel_cat',
        'pyautogui', 'pyperclip', 'PIL', 'PIL.ImageGrab', 'PIL.ImageTk',
        'cv2', 'xlrd', 'openpyxl',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['matplotlib', 'numpy.testing', 'scipy', 'pandas',
              'IPython', 'jupyter', 'notebook', 'pytest', 'pydoc_data',
              'tkinter.test', 'PIL.ImageQt', 'PIL.ImageShow'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

if ONEFILE:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.zipfiles,
        a.datas,
        name=APP_NAME,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        console=False,          # 无黑色命令行窗口
        disable_windowed_traceback=False,
        argv_emulation=IS_MAC,  # macOS 下透传 AppleEvent（双击打开等）
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon=ICON,
        version=VERSION_FILE,
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name=APP_NAME,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        console=False,          # 无黑色命令行窗口
        disable_windowed_traceback=False,
        argv_emulation=IS_MAC,  # macOS 下透传 AppleEvent（双击打开等）
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon=ICON,
        version=VERSION_FILE,
    )

    coll = COLLECT(
        exe,
        a.binaries,
        a.zipfiles,
        a.datas,
        strip=False,
        upx=True,
        upx_exclude=[],
        name=APP_NAME,
    )

# macOS 额外产出 .app bundle（Finder 双击运行的标准形态；可用 MEOWGO_NO_APP=1 关闭）
if IS_MAC and not NO_APP:
    app = BUNDLE(
        coll if not ONEFILE else exe,
        *([] if ONEFILE else (a.binaries, a.zipfiles, a.datas)),
        name=APP_NAME + '.app',
        icon=ICON,
        bundle_identifier=None,
    )
