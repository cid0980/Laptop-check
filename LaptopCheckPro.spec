# -*- mode: python ; coding: utf-8 -*-
"""
LaptopCheckPro.spec -- PyInstaller recipe for the single-file .exe.

Build it on any Windows PC with:      tools\build.bat        (one click)
or by hand:                           python -m PyInstaller LaptopCheckPro.spec --noconfirm

Everything the app needs is Python standard library, so there is NOTHING to
bundle besides the code itself: no OpenCV, no numpy, no DLLs, no data files.
The result is one .exe that runs on a clean Windows 10/11 machine.
"""

import os

block_cipher = None

a = Analysis(
    ['run.py'],
    pathex=[os.path.abspath('.')],
    binaries=[],
    datas=[],
    hiddenimports=[
        'app', 'app.core', 'app.png', 'app.dshow', 'app.qr', 'app.grading',
        'app.valuation', 'app.branding', 'app.scan', 'app.report', 'app.demo',
        'app.gui',
        'collectors', 'collectors.system', 'collectors.components', 'collectors.stress',
        'guided', 'guided.screen', 'guided.widgets', 'guided.audio', 'guided.camera',
        'guided.keyboard', 'guided.misc',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # deliberately not used anywhere in the project - keep the exe lean
        'numpy', 'scipy', 'pandas', 'matplotlib', 'PIL', 'cv2', 'pytest',
        'tkinter.test', 'unittest', 'pydoc',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='LaptopCheckPro',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                       # UPX on onefile exes causes antivirus false positives
    upx_exclude=[],
    runtime_tmpdir=None,             # default onefile temp extraction
    console=False,                   # no black console window for the shop
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/icon.ico',
    uac_admin=True,                  # Windows shows the UAC shield and asks to elevate
    uac_uiaccess=False,
    version=None,
)
