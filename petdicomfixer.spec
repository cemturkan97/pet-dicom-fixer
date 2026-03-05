# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for macOS

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=['src', 'src.app', 'src.dicom_ops', 'src.radionuclides', 'numpy'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='PET DICOM Fixer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    name='PET DICOM Fixer',
)

app = BUNDLE(
    coll,
    name='PET DICOM Fixer.app',
    bundle_identifier='com.cemturkan97.petdicomfixer',
)
