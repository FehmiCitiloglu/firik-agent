# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec dosyası - firik-agent masaüstü uygulaması.

macOS (firik-agent.app), Windows (firik-agent.exe) ve Linux (firik-agent)
için tek tıkla çalıştırılabilir ikili (binary) paketler üretir.
"""
from importlib.metadata import version
import os
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
hiddenimports = ["keyring.backends.chainer", "PIL._tkinter_finder"]
hiddenimports.append({
    "darwin": "keyring.backends.macOS",
    "win32": "keyring.backends.Windows",
}.get(sys.platform, "keyring.backends.SecretService"))
app_version = version("firik-agent")


# CustomTkinter varlıkları (fontlar, JSON tema dosyaları, ikonlar)
ctk_datas, ctk_binaries, ctk_hidden = collect_all("customtkinter")
datas += ctk_datas
binaries += ctk_binaries
hiddenimports += ctk_hidden

# Yerel dosya sürükle/bırak için platformun TkDND uzantısını pakete dahil et.
dnd_datas, dnd_binaries, dnd_hidden = collect_all("tkinterdnd2")
datas += dnd_datas
binaries += dnd_binaries
hiddenimports += dnd_hidden

# Certifi SSL kök sertifikaları (HTTPS istekleri için)
cert_datas, cert_binaries, cert_hidden = collect_all("certifi")
datas += cert_datas
binaries += cert_binaries
hiddenimports += cert_hidden

# firik_agent paket verileri (openapi.json, builtin_skills, assets)
firik_datas, firik_binaries, firik_hidden = collect_all("firik_agent")
datas += firik_datas
binaries += firik_binaries
hiddenimports += firik_hidden

# Varsa assets dosyalarını garantiye al
assets_dir = Path("firik_agent/ui/assets")
if assets_dir.exists():
    datas.append((str(assets_dir), "firik_agent/ui/assets"))

is_mac = sys.platform == "darwin"
is_win = sys.platform == "win32"

icon_file = None
if is_mac and Path("firik_agent/ui/assets/icon.png").exists():
    icon_file = "firik_agent/ui/assets/icon.png"
elif is_win and Path("firik_agent/ui/assets/icon.ico").exists():
    icon_file = "firik_agent/ui/assets/icon.ico"
elif Path("firik_agent/ui/assets/icon.png").exists():
    icon_file = "firik_agent/ui/assets/icon.png"

a = Analysis(
    ["firik_agent/ui/__main__.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "_pytest"],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [] if is_mac else a.binaries,
    [] if is_mac else a.datas,
    [],
    exclude_binaries=is_mac,
    name="firik-agent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=os.environ.get("FIRIK_CODESIGN_IDENTITY") if is_mac else None,
    entitlements_file=None,
    icon=icon_file,
)

if is_mac:
    collected = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="firik-agent")
    app = BUNDLE(
        collected,
        name="firik-agent.app",
        icon=icon_file,
        bundle_identifier="ssyz.firik-agent.desktop",
        info_plist={
            "CFBundleName": "firik-agent",
            "CFBundleDisplayName": "firik-agent",
            "CFBundleGetInfoString": "Firik Agent Masaüstü Uygulaması",
            "CFBundleIdentifier": "ssyz.firik-agent.desktop",
            "CFBundleVersion": app_version,
            "CFBundleShortVersionString": app_version,
            "NSHighResolutionCapable": "True",
        },
    )
