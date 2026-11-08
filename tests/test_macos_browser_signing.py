"""The packaged Node driver alone receives the JIT runtime exception."""
import importlib.metadata
import importlib.util
from pathlib import Path
import plistlib
import runpy
import subprocess
import sys
from types import ModuleType, SimpleNamespace

import pytest

from scripts import macos_browser_signing as signing


def bundle(tmp_path):
    app = tmp_path / "firik-agent.app"
    node = app / "Contents/Frameworks/playwright/driver/node"
    node.parent.mkdir(parents=True)
    node.write_bytes(b"fixture")
    return app, node


@pytest.mark.parametrize("identity", [None, "Developer ID Application: Fixture (TESTTEAM00)"])
def test_only_known_node_is_signed_before_parent_without_deep(tmp_path, monkeypatch, identity):
    app, node = bundle(tmp_path)
    other_node = app / "Contents/Frameworks/other/node"
    other_node.parent.mkdir(parents=True)
    other_node.write_bytes(b"unrelated")
    commands = []

    def run(args, **kwargs):
        commands.append(args)
        return SimpleNamespace(stdout=plistlib.dumps({"com.apple.security.cs.allow-jit": True}))

    monkeypatch.setattr(signing.subprocess, "run", run)
    assert signing.sign_playwright_node(app, identity, required=True)
    assert commands[0][-1] == str(node)
    assert commands[1][-1] == str(app)
    assert "--options=runtime" in commands[0]
    assert "--all-architectures" in commands[0]
    assert commands[0][commands[0].index("--entitlements") + 1] == str(signing.NODE_ENTITLEMENTS)
    assert commands[1][commands[1].index("--entitlements") + 1] == str(signing.APP_ENTITLEMENTS)
    assert all("--deep" not in command for command in commands[:2])
    assert all(str(other_node) not in command for command in commands)
    assert commands[2] == ["/usr/bin/codesign", "--display", "--entitlements", "-", "--xml", str(node)]
    assert commands[3] == ["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)]
    assert ("--timestamp" in commands[0]) is bool(identity)
    assert ("--timestamp=none" in commands[0]) is not bool(identity)
    with signing.APP_ENTITLEMENTS.open("rb") as stream:
        assert plistlib.load(stream) == {"com.apple.security.device.audio-input": True}


def test_missing_driver_never_selects_another_node_or_runs_codesign(tmp_path, monkeypatch):
    app = tmp_path / "firik-agent.app"
    other = app / "Contents/Resources/node"
    other.parent.mkdir(parents=True)
    other.write_bytes(b"unrelated")
    monkeypatch.setattr(signing.subprocess, "run", lambda *args, **kw: pytest.fail("No driver selected"))
    assert signing.sign_playwright_node(app) is False
    assert signing.verify_playwright_node_entitlements(app) is False
    with pytest.raises(FileNotFoundError, match="Playwright Node is missing"):
        signing.sign_playwright_node(app, required=True)


def test_driver_symlink_cannot_sign_outside_bundle(tmp_path, monkeypatch):
    app, node = bundle(tmp_path)
    external = tmp_path / "external-node"
    external.write_bytes(b"unrelated")
    node.unlink()
    node.symlink_to(external)
    monkeypatch.setattr(signing.subprocess, "run", lambda *args, **kw: pytest.fail("Escaped bundle"))
    with pytest.raises(ValueError, match="inside its app bundle"):
        signing.sign_playwright_node(app)


@pytest.mark.parametrize("entitlements", [b"", b"not a plist", plistlib.dumps({}),
    plistlib.dumps({"com.apple.security.cs.allow-jit": False}),
    plistlib.dumps({"com.apple.security.cs.allow-jit": True,
                   "com.apple.security.cs.disable-library-validation": True})])
def test_invalid_missing_or_overbroad_entitlements_fail_closed(tmp_path, monkeypatch, entitlements):
    app, _ = bundle(tmp_path)
    monkeypatch.setattr(signing.subprocess, "run", lambda *args, **kw: SimpleNamespace(stdout=entitlements))
    with pytest.raises(RuntimeError, match="Playwright Node"):
        signing.verify_playwright_node_entitlements(app, required=True)


def test_node_sign_failure_never_seals_parent(tmp_path, monkeypatch):
    app, _ = bundle(tmp_path)
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        raise subprocess.CalledProcessError(1, args)

    monkeypatch.setattr(signing.subprocess, "run", run)
    with pytest.raises(subprocess.CalledProcessError):
        signing.sign_playwright_node(app)
    assert len(calls) == 1


@pytest.mark.parametrize("platform,installed", [("darwin", True), ("darwin", False), ("linux", True)])
def test_spec_finalizes_only_macos_browser_after_bundle(monkeypatch, platform, installed):
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(importlib.metadata, "version", lambda name: "0.2.50")
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object() if installed and name == "playwright" else None)
    hooks = ModuleType("PyInstaller.utils.hooks")
    hooks.collect_all = lambda name: ([], [], [])
    hooks.copy_metadata = lambda *args, **kwargs: []
    monkeypatch.setitem(sys.modules, "PyInstaller.utils.hooks", hooks)
    monkeypatch.setenv("FIRIK_CODESIGN_IDENTITY", "test-identity")
    actions = []

    def make_bundle(*args, **kwargs):
        actions.append("bundle")
        return SimpleNamespace(name="dist/firik-agent.app")

    monkeypatch.setattr(signing, "sign_playwright_node", lambda app, identity, **kwargs:
        actions.append(("finalize", app, identity, kwargs)))
    root = Path(__file__).resolve().parents[1]
    runpy.run_path(str(root / "firik-agent.spec"), init_globals={
        "Analysis": lambda *args, **kwargs: SimpleNamespace(pure=[], scripts=[], datas=[], binaries=[]),
        "PYZ": lambda *args: object(), "EXE": lambda *args, **kwargs: object(),
        "COLLECT": lambda *args, **kwargs: object(), "BUNDLE": make_bundle,
    })
    assert actions == (["bundle", ("finalize", "dist/firik-agent.app", "test-identity", {"required": True})]
                       if platform == "darwin" and installed else ["bundle"] if platform == "darwin" else [])
