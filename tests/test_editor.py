import pytest

from firik_agent.ui import editor


@pytest.mark.parametrize("name,command", [("VS Code", "code"), ("Cursor", "cursor"), ("Zed", "zed")])
def test_open_editor_preserves_path_as_single_argument(tmp_path, monkeypatch, name, command):
    target = tmp_path / "project & special $(echo test)"
    target.mkdir()
    monkeypatch.setattr(editor.sys, "platform", "linux")
    monkeypatch.setattr(editor.shutil, "which", lambda value: f"/usr/bin/{value}")
    launched = []
    monkeypatch.setattr(editor.subprocess, "Popen", lambda args, **kwargs: launched.append((args, kwargs)))
    editor.open_in_editor(name, target)
    assert launched[0][0] == [f"/usr/bin/{command}", str(target)]
    assert launched[0][1]["shell"] is False


def test_editor_not_installed_gives_actionable_error(tmp_path, monkeypatch):
    monkeypatch.setattr(editor.sys, "platform", "linux")
    monkeypatch.setattr(editor.shutil, "which", lambda value: None)
    with pytest.raises(ValueError, match="Özel Editör"):
        editor.open_in_editor("VS Code", tmp_path)


def test_custom_editor_and_deleted_file(tmp_path, monkeypatch):
    executable = tmp_path / "my editor"
    executable.touch()
    target = tmp_path / "source.py"
    target.touch()
    monkeypatch.setattr(editor.sys, "platform", "linux")
    assert editor.editor_command("Özel Editör", target, str(executable)) == [str(executable), str(target)]
    with pytest.raises(ValueError):
        editor.editor_command("Özel Editör", target, "relative-command")
    with pytest.raises(ValueError, match="bulunamadı"):
        editor.open_in_editor("Özel Editör", tmp_path / "deleted.py", str(executable))


def test_macos_application_and_windows_system_default(tmp_path, monkeypatch):
    app = tmp_path / "My Editor.app"
    app.mkdir()
    monkeypatch.setattr(editor.sys, "platform", "darwin")
    assert editor.editor_command("Özel Editör", tmp_path, str(app)) == ["open", "-a", str(app), str(tmp_path)]
    opened = []
    monkeypatch.setattr(editor.sys, "platform", "win32")
    monkeypatch.setattr(editor.os, "startfile", lambda path: opened.append(path), raising=False)
    editor.open_in_editor("Sistem Varsayılanı", tmp_path)
    assert opened == [str(tmp_path)]


def test_windows_finds_editor_without_path_entry(tmp_path, monkeypatch):
    executable = tmp_path / "Programs" / "Microsoft VS Code" / "Code.exe"
    executable.parent.mkdir(parents=True)
    executable.touch()
    monkeypatch.setattr(editor.sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert editor.editor_command("VS Code", tmp_path) == [str(executable), str(tmp_path)]
