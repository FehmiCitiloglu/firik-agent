"""Validate optional audio and frozen microphone permission boundaries."""
from pathlib import Path
import plistlib
import runpy
import subprocess
import sys
from types import ModuleType, SimpleNamespace

import pytest
import yaml

import install as installer


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(("platform", "build_platform", "expected_library"), [
    ("darwin", "macosx-14.0-arm64", "libportaudio.dylib"),
    ("win32", "win32", "libportaudio32bit.dll"),
    ("win32", "win-amd64", "libportaudio64bit.dll"),
    ("win32", "win-arm64", "libportaudioarm64.dll"),
    ("linux", "linux-x86_64", "libportaudio.so.2"),
])
@pytest.mark.parametrize("audio_installed", [False, True])
def test_frozen_voice_collects_only_the_target_portaudio(monkeypatch, platform, build_platform,
                                                        expected_library, audio_installed):
    """Execute the real spec against build stubs without accessing audio devices."""
    import importlib.metadata
    import importlib.util
    import sysconfig

    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(sysconfig, "get_platform", lambda: build_platform)
    monkeypatch.setattr(importlib.metadata, "version", lambda name: "0.2.34")
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object()
                        if audio_installed and name in {"sounddevice", "soundfile", "webrtcvad"} else None)
    hooks = ModuleType("PyInstaller.utils.hooks")
    hooks.collect_all = lambda name: ([], [], [])
    metadata_calls = []

    def metadata(name, recursive=False):
        metadata_calls.append((name, recursive))
        return [("licenses", "licenses")]

    hooks.copy_metadata = metadata
    monkeypatch.setitem(sys.modules, "PyInstaller.utils.hooks", hooks)
    builds = {}

    def analysis(*args, **kwargs):
        builds["analysis"] = kwargs
        wheel_libraries = [
            "libportaudio.dylib", "libportaudio32bit.dll", "libportaudio64bit.dll",
            "libportaudioarm64.dll", "libportaudio64bit-asio.dll",
        ]
        native_binaries = [
            (f"_sounddevice_data/portaudio-binaries/{name}", name, "BINARY")
            for name in wheel_libraries
        ] if audio_installed else []
        if audio_installed and platform == "linux":
            native_binaries.append(("libportaudio.so.2", "libportaudio.so.2", "BINARY"))
        return SimpleNamespace(
            pure=[], scripts=[], datas=[*kwargs["datas"],
                ("_sounddevice_data/portaudio-binaries/libportaudio32bit-asio.dll", "asio-data.dll", "DATA"),
                ("firik_agent/ui/assets/voice-third-party-notices.txt", "notices.txt", "DATA")],
            binaries=native_binaries,
        )

    def executable(*args, **kwargs):
        builds["exe"] = kwargs
        return object()

    def bundle(*args, **kwargs):
        builds["bundle"] = kwargs

    state = runpy.run_path(str(ROOT / "firik-agent.spec"), init_globals={
        "Analysis": analysis, "PYZ": lambda *a: object(), "EXE": executable,
        "COLLECT": lambda *a, **kw: object(), "BUNDLE": bundle,
    })
    assert ("sounddevice" in builds["analysis"]["hiddenimports"]) is audio_installed
    assert ("soundfile" in builds["analysis"]["hiddenimports"]) is audio_installed
    assert ("webrtcvad" in builds["analysis"]["hiddenimports"]) is audio_installed
    assert metadata_calls == ([("sounddevice", True), ("soundfile", True), ("webrtcvad-wheels", True)] if audio_installed else [])
    assert [Path(entry[0]).name for entry in state["a"].binaries] == (
        [expected_library] if audio_installed else []
    )
    assert not any(entry[0].lower().endswith("-asio.dll") for entry in state["a"].datas)
    assert any(entry[0].endswith("voice-third-party-notices.txt") for entry in state["a"].datas)
    assert (("licenses", "licenses") in state["a"].datas) is audio_installed
    if platform == "darwin":
        entitlements = ROOT / builds["exe"]["entitlements_file"]
        with entitlements.open("rb") as stream:
            assert plistlib.load(stream) == {"com.apple.security.device.audio-input": True}
        assert builds["bundle"]["info_plist"]["NSMicrophoneUsageDescription"]
    else:
        assert builds["exe"]["entitlements_file"] is None


@pytest.mark.parametrize("uv", [None, "/tools/uv"])
def test_desktop_installer_installs_voice_in_its_selected_environment(tmp_path, monkeypatch, uv):
    """A standard source installer must not produce an audio-less desktop bundle."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        if "PyInstaller" in args:
            dist = Path(args[args.index("--distpath") + 1])
            dist.mkdir()
            (dist / "firik-agent").write_bytes(b"desktop")
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(installer.subprocess, "run", run)
    python = tmp_path / "selected-venv/bin/python"
    installer.install_desktop(tmp_path, python, uv, "linux")
    expected_prefix = ([uv, "pip", "install", "--python", str(python)] if uv else
                       [str(python), "-m", "pip", "install"])
    assert calls[1] == expected_prefix + ["-e", f"{tmp_path}[desktop-build]"]


def test_release_build_has_linux_audio_runtime_and_desktop_build_extra():
    workflow = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())
    steps = workflow["jobs"]["build-desktop"]["steps"]
    python_install = next(s["run"] for s in steps if s.get("name") == "Install Python dependencies")
    linux_install = next(s["run"] for s in steps if s.get("name") == "Install Linux build and display dependencies")
    assert ".[dev,desktop-build]" in python_install
    assert "libportaudio2" in linux_install


@pytest.mark.parametrize("backend_available", [False, True])
def test_packaged_smoke_probes_portaudio_without_opening_streams(monkeypatch, backend_available):
    from firik_agent.ui import app as module
    from firik_agent.voice import audio_devices
    from firik_agent.voice.errors import VoiceAudioError

    calls = []

    class DeviceBackend:
        default = SimpleNamespace(device=(-1, -1))

        def query_hostapis(self):
            return []

        def query_devices(self):
            calls.append("enumerate")
            return []

        def RawInputStream(self, *args, **kwargs):
            pytest.fail("Smoke test opened a microphone stream")

        def RawOutputStream(self, *args, **kwargs):
            pytest.fail("Smoke test opened a speaker stream")

    class App:
        def __init__(self, *, config_path):
            calls.append("app")

        def after(self, delay, callback):
            callback()

        def destroy(self):
            calls.append("close")

        def mainloop(self):
            pass

    def backend():
        if not backend_available:
            raise VoiceAudioError("Missing PortAudio library")
        return DeviceBackend()

    monkeypatch.setattr(module, "FirikApp", App)
    monkeypatch.setattr(audio_devices, "load_backend", backend)
    monkeypatch.setattr(sys, "argv", ["firik-gui", "--smoke-test", "--check-voice-backend"])

    assert module.main() == (0 if backend_available else 1)
    assert calls == (["enumerate", "enumerate", "app", "close"] if backend_available else [])
