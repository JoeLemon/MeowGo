# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：
    python -m PyInstaller --noconfirm "MeowGo.spec"

生成：dist/MeowGo/MeowGo.exe（单文件夹，启动更快）
"""

block_cipher = None

a = Analysis(
    ['start-app.py'],
    pathex=['.'],
    binaries=[],
    datas=[('main.png', '.')],
    hiddenimports=[
        'rpa', 'rpa.gui', 'rpa.engine', 'rpa.assets', 'rpa.uikit', 'rpa.lock',
        'pyautogui', 'pyperclip', 'PIL', 'PIL.ImageGrab', 'PIL.ImageTk',
        'cv2', 'xlrd', 'openpyxl',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['matplotlib', 'numpy.testing', 'scipy', 'pandas'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='MeowGo',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,          # 无黑色命令行窗口
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['main.png'],
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='MeowGo',
)
