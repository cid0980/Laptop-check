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
import re

block_cipher = None

# ---- Windows file-properties (version resource) ---------------------------
# Right-click -> Properties -> Details must show a real product, company and
# version: that is half of what makes an exe look genuine to a shop tech.
_ver = "1.0.0"
try:
    with open(os.path.join(os.path.abspath("."), "app", "core.py"), encoding="utf-8") as fh:
        _m = re.search(r'APP_VERSION = "([0-9.]+)"', fh.read())
        if _m:
            _ver = _m.group(1)
except Exception:
    pass
_vt = tuple(int(x) for x in (_ver + ".0.0").split(".")[:3]) + (0,)
_version_txt = os.path.join("build", "version_info.txt")
os.makedirs("build", exist_ok=True)
with open(_version_txt, "w", encoding="utf-8") as fh:
    fh.write("""# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(filevers=%s, prodvers=%s, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0,0)),
  kids=[StringFileInfo([StringTable(u'040904B0', [
    StringStruct(u'CompanyName', u'Laptop Check Pro'),
    StringStruct(u'FileDescription', u'Laptop Check Pro - used-laptop inspection, grading and INR valuation'),
    StringStruct(u'FileVersion', u'%s'),
    StringStruct(u'InternalName', u'LaptopCheckPro'),
    StringStruct(u'LegalCopyright', u'Copyright (C) 2026 Laptop Check Pro'),
    StringStruct(u'OriginalFilename', u'LaptopCheckPro.exe'),
    StringStruct(u'ProductName', u'Laptop Check Pro'),
    StringStruct(u'ProductVersion', u'%s')])]),
  VarFileInfo([VarStruct(u'Translation', [1033, 1200])])]
)
""" % (_vt, _vt, _ver + ".0", _ver + ".0"))

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
        'app.fixes', 'app.debug', 'app.camrt',
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
    version=_version_txt,
)
