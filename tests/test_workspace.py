from pathlib import Path

import pytest

from firik_agent.projects.workspace import WorkspaceSnapshot, capture_workspace, compare_workspaces, workspace_path


def test_run_changes_include_added_modified_deleted_without_preexisting_edits(tmp_path):
    (tmp_path / "main.py").write_text("print('already edited')\n")
    (tmp_path / "deleted.py").write_text("obsolete = True\n")
    (tmp_path / "unchanged.py").write_text("prior_edit = True\n")
    before = capture_workspace(tmp_path)
    (tmp_path / "main.py").write_text("print('agent edit')\n")
    (tmp_path / "deleted.py").unlink()
    (tmp_path / "new file.py").write_text("created = True\n")
    (tmp_path / "empty.py").touch()
    changes = {c["path"]: c for c in compare_workspaces(before, capture_workspace(tmp_path))}
    assert set(changes) == {"main.py", "deleted.py", "new file.py", "empty.py"}
    assert changes["main.py"]["status"] == "modified"
    assert "-print('already edited')" in changes["main.py"]["diff"]
    assert "+print('agent edit')" in changes["main.py"]["diff"]
    assert changes["main.py"]["added"] == changes["main.py"]["removed"] == 1
    assert changes["deleted.py"]["status"] == "deleted"
    assert changes["new file.py"]["status"] == "added"
    assert changes["empty.py"]["diff"] == "Boş dosya eklendi."


def test_snapshots_skip_sensitive_binary_dependencies_and_external_symlinks(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "safe.py").write_text("ok = True")
    (root / ".env").write_text("PASSWORD=secret")
    (root / "credentials.json").write_text("secret")
    (root / "binary.dat").write_bytes(b"\x00binary")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "dependency.js").write_text("dependency")
    outside = tmp_path / "outside.py"
    outside.write_text("external secret")
    (root / "linked.py").symlink_to(outside)
    (root / "linked_dir").symlink_to(tmp_path, target_is_directory=True)
    assert capture_workspace(root).files == {"safe.py": "ok = True"}
    with pytest.raises(ValueError, match="dışında"):
        workspace_path(root, "linked.py")
    with pytest.raises(ValueError, match="dışında"):
        workspace_path(root, "../outside.py")


def test_partial_scans_never_report_missing_files_as_deleted_or_added(tmp_path):
    (tmp_path / "a.py").write_text("old\n")
    (tmp_path / "b.py").write_text("unchanged\n")
    before = capture_workspace(tmp_path)
    (tmp_path / "a.py").write_text("new\n")
    after = capture_workspace(tmp_path, max_files=1)
    assert after.limited
    changes = compare_workspaces(before, after)
    assert [c["path"] for c in changes] == ["a.py"]
    assert compare_workspaces(after, before)[0]["path"] == "a.py"
    skipped = capture_workspace(tmp_path, max_bytes=1)
    assert skipped.limited
    assert compare_workspaces(before, skipped) == []


def test_newline_only_changes_are_visible():
    before = WorkspaceSnapshot(files={"main.py": "pass"})
    after = WorkspaceSnapshot(files={"main.py": "pass\n"})
    change = compare_workspaces(before, after)[0]
    assert change["added"] == change["removed"] == 1
    assert "No newline at end of file" in change["diff"]


def test_source_lines_starting_with_plus_or_minus_count_as_content():
    before = WorkspaceSnapshot(files={"notes.txt": "--old\n"})
    after = WorkspaceSnapshot(files={"notes.txt": "++new\n"})
    change = compare_workspaces(before, after)[0]
    assert change["added"] == change["removed"] == 1


def test_missing_workspace_reports_error(tmp_path):
    with pytest.raises(ValueError, match="bulunamadı"):
        capture_workspace(tmp_path / "missing")


@pytest.mark.parametrize("missing", [False, True])
def test_inspection_runs_on_worker_and_dispatches_gui_callback(tmp_path, missing):
    import threading
    from types import SimpleNamespace
    from firik_agent.ui.service import FirikService

    (tmp_path / "main.py").write_bytes(b"pass\n")
    before = capture_workspace(tmp_path)
    (tmp_path / "main.py").write_bytes(b"value = True\n")
    dispatched = []
    ready = threading.Event()
    gui_thread = threading.get_ident()

    def dispatch(callback, result):
        dispatched.append((callback, result, threading.get_ident()))
        ready.set()

    success, error = lambda _: None, lambda _: None
    FirikService.inspect_workspace_async(SimpleNamespace(_dispatch=dispatch), tmp_path / "missing" if missing else tmp_path, success, error, before=before)
    assert ready.wait(5)
    callback, result, thread = dispatched[0]
    assert thread != gui_thread
    assert callback is (error if missing else success)
    if not missing:
        assert result.files == {"main.py": "value = True\n"}
        assert result.changes[0]["path"] == "main.py"
