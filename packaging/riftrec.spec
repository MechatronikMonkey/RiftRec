# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for RiftRec (EW-89).

Produces `dist/RiftRec/` containing RiftRec.exe plus a private CPython runtime,
which packaging/riftrec.iss then wraps into one setup file. The point of the
whole exercise: the zipped source version never started on Bicas' PC because it
needed Python and pip - a participant must not have to install either.

One-folder, not one-file, on purpose. A one-file build unpacks itself into %TEMP%
on every launch; with an on-access antivirus shield that is both slow and exactly
the write pattern already suspected in the stuttering of EW-51. The folder lives
inside the installer, so a participant still only ever sees one file.

Build:  pyinstaller --noconfirm --clean packaging/riftrec.spec
"""

from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent          # noqa: F821 - injected by PyInstaller

# schema.sql is read from disk at runtime (it is the contract to RiftLab), and
# PyInstaller bundles no non-Python files by itself. `riftrec selfcheck` fails
# the build if this ever goes missing.
datas = [(str(ROOT / "riftrec" / "storage" / "schema.sql"), "riftrec/storage")]

# bleak reaches its Windows backend and the winrt projections through dynamic
# imports, so a static analysis does not see them; pystray and PIL pick their
# platform/toolkit backend the same way. Verified against the modules bleak
# actually loads on this machine (see README, "Building the installer").
hiddenimports = [
    "pystray._win32",
    "PIL._tkinter_finder",
    "bleak.backends.winrt.client",
    "bleak.backends.winrt.scanner",
    "bleak.backends.winrt.util",
    "winrt.runtime",
    "winrt.system",
    "winrt.windows.devices.bluetooth",
    "winrt.windows.devices.bluetooth.advertisement",
    "winrt.windows.devices.bluetooth.genericattributeprofile",
    "winrt.windows.devices.enumeration",
    "winrt.windows.devices.radios",
    "winrt.windows.foundation",
    "winrt.windows.foundation.collections",
    "winrt.windows.storage.streams",
]

# Spike-only and analysis-only dependencies. They are not in
# requirements-recorder.txt either, but excluding them keeps the installer small
# even when someone builds from a full dev environment.
excludes = [
    "bleakheart", "bumble", "numpy", "pandas", "scipy", "matplotlib",
    "pytest", "IPython", "notebook", "riftlab",
]

_icon = ROOT / "packaging" / "riftrec.ico"

a = Analysis(                                   # noqa: F821
    [str(ROOT / "packaging" / "riftrec_launcher.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)                               # noqa: F821

exe = EXE(                                      # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="RiftRec",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # UPX-packed binaries are a favourite antivirus trigger
    console=False,      # tray app: no console window (output goes to riftrec.log)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(_icon) if _icon.exists() else None,
)

coll = COLLECT(                                 # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="RiftRec",
)
