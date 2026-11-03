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


@pytest.mark.parametrize("platform", ["darwin", "win32", "linux"])
@pytest.mark.parametrize("audio_installed", [False, True])
def test_frozen_voice_is_optional_and_excludes_asio(monkeypatch, platform, audio_installed):
    """Execute the real spec against build stubs without accessing audio devices."""
    import importlib.metadata
    import importlib.util

    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(importlib.metadata, "version", lambda name: "0.2.34")
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object() if audio_installed else None)
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
        return SimpleNamespace(
            pure=[], scripts=[], datas=[*kwargs["datas"],
                ("_sounddevice_data/portaudio-binaries/libportaudio32bit-asio.dll", "asio-data.dll", "DATA"),
                ("firik_agent/ui/assets/voice-third-party-notices.txt", "notices.txt", "DATA")],
            binaries=[("_sounddevice_data/libportaudio64bit.dll", "portaudio.dll", "BINARY"),
                      ("_sounddevice_data/libportaudio64bit-asio.dll", "asio.dll", "BINARY")],
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
    assert len(state["a"].binaries) == 1
    assert state["a"].binaries[0][0].endswith("libportaudio64bit.dll")
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
    assert calls[1] == expected_prefix + ["pyinstaller>=6.16,<7", "-e", f"{tmp_path}[voice]"]


def test_release_build_has_linux_audio_runtime_and_voice_extra():
    workflow = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())
    steps = workflow["jobs"]["build-desktop"]["steps"]
    python_install = next(s["run"] for s in steps if s.get("name") == "Install Python dependencies")
    linux_install = next(s["run"] for s in steps if s.get("name") == "Install Linux build and display dependencies")
    assert ".[dev,voice]" in python_install
    assert "libportaudio2" in linux_install
