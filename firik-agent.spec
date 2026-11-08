# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec dosyası - firik-agent masaüstü uygulaması.

macOS (firik-agent.app), Windows (firik-agent.exe) ve Linux (firik-agent)
için tek tıkla çalıştırılabilir ikili (binary) paketler üretir.
"""
from importlib.metadata import version
from importlib.util import find_spec
import os
import sys
import sysconfig
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, copy_metadata

datas = []
binaries = []
hiddenimports = ["keyring.backends.chainer", "PIL._tkinter_finder"]
hiddenimports.append({
    "darwin": "keyring.backends.macOS",
    "win32": "keyring.backends.Windows",
}.get(sys.platform, "keyring.backends.SecretService"))
app_version = version("firik-agent")

# Audio is optional for source installs. The sounddevice hook collects the
# platform PortAudio library; recursive metadata retains dependency licenses.
if find_spec("sounddevice") is not None:
    hiddenimports.append("sounddevice")
    datas += copy_metadata("sounddevice", recursive=True)

# Optional codecs/VAD are collected only when deliberately installed.
for audio_module, audio_distribution in (("soundfile", "soundfile"), ("webrtcvad", "webrtcvad-wheels")):
    if find_spec(audio_module) is not None:
        hiddenimports.append(audio_module)
        datas += copy_metadata(audio_distribution, recursive=True)

# The optional Python driver must travel with a browser-enabled build. Chromium
# is installed explicitly in Playwright's external cache; it is never silently
# downloaded by the app or assumed to exist inside the executable.
browser_driver_included = find_spec("playwright") is not None
if browser_driver_included:
    browser_datas, browser_binaries, browser_hidden = collect_all("playwright")
    datas += browser_datas + copy_metadata("playwright", recursive=True)
    binaries += browser_binaries
    hiddenimports += browser_hidden


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

# Some sounddevice wheels ship libraries for several OSes and architectures.
# Keep the wheel binary used by this interpreter. On Linux the hook collects
# system PortAudio separately, at the root of the frozen library directory.
wheel_portaudio = None
if is_mac:
    wheel_portaudio = "libportaudio.dylib"
elif is_win:
    suffix = {"win32": "32bit", "win-arm64": "arm64"}.get(sysconfig.get_platform(), "64bit")
    wheel_portaudio = f"libportaudio{suffix}.dll"


def keep_portaudio_entry(entry):
    path = entry[0].replace("\\", "/").lower()
    name = path.rsplit("/", 1)[-1]
    if name.endswith("-asio.dll"):
        # The separately licensed ASIO variant is outside this distribution.
        return False
    if path.startswith("_sounddevice_data/portaudio-binaries/libportaudio"):
        return name == wheel_portaudio
    return True


a.binaries = [entry for entry in a.binaries if keep_portaudio_entry(entry)]
a.datas = [entry for entry in a.datas if keep_portaudio_entry(entry)]

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
    entitlements_file="firik_agent/ui/assets/macos-voice.entitlements" if is_mac else None,
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
            "NSMicrophoneUsageDescription": "Bas-konuş düğmesini basılı tuttuğunuzda veya sesli sohbeti açıkça başlattığınızda konuşmanızı yazıya dönüştürmek için mikrofon kullanılır. Dinlemeyi duraklatabilir veya kapatabilirsiniz.",
        },
    )
    if browser_driver_included:
        # BUNDLE's deep signing overwrites Node's vendor JIT entitlement.
        # Restore it only on that driver, then seal the parent without --deep.
        from scripts.macos_browser_signing import sign_playwright_node
        sign_playwright_node(app.name, os.environ.get("FIRIK_CODESIGN_IDENTITY"), required=True)
