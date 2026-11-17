"""Electron coding tools reuse the scoped project reader and durable settings."""
from __future__ import annotations

import os
import threading

from firik_agent.projects.git_service import GitService

from test_desktop_workspaces import coding_params, eventually, workspace  # noqa: F401


def test_project_browser_is_lazy_scoped_and_hides_protected_sources(workspace):
    _, _, call, _, roots, _ = workspace
    root = roots["p1"]
    (root / "src").mkdir()
    (root / "src" / "feature.py").write_text("feature = 1\n")
    (root / ".env").write_text("PRIVATE_VALUE=never-display\n")
    (root / "credentials.json").write_text('{"private":"never-display"}')
    (root / "image.png").write_bytes(b"\x00image")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "dependency.js").write_text("dependency = true")
    (root / "external").symlink_to(roots["p2"], target_is_directory=True)

    result = call("projects.files", {"project_id": "p1"})["result"]
    assert result["path"] == ""
    assert {row["name"] for row in result["entries"]} == {"main.py", "src"}
    assert next(row for row in result["entries"] if row["name"] == "src")["is_dir"]
    child = call("projects.files", {"project_id": "p1", "path": "src"})["result"]
    assert [row["path"] for row in child["entries"]] == ["src/feature.py"]
    search = call("projects.files", {"project_id": "p1", "query": "feature"})["result"]
    assert [row["path"] for row in search["entries"]] == ["src/feature.py"]
    for path in ("../p2", "external", ".git"):
        result = call("projects.files", {"project_id": "p1", "path": path})
        assert "error" in result or result["result"]["entries"] == []


def test_project_preview_redacts_bounds_and_enforces_exclusions(workspace):
    _, service, call, _, roots, _ = workspace
    root = roots["p1"]
    (root / "source.py").write_text('api_key = "super-private-test-value"\nvalues = ["fixture-only-key"]\n' + "x" * 70000)
    (root / "private.py").write_text("never-display = 1\n")
    (root / "link.py").symlink_to(roots["p2"] / "main.py")
    (root / ".env").write_text("never-display")
    (root / "binary.png").write_bytes(b"\x00binary")
    settings = service.projects.db.get_settings("p1")
    settings.coding_preferences["excluded_files"] = ["private.py"]
    service.projects.db.save_settings(settings)

    call("state.snapshot")
    preview = call("projects.read", {"project_id": "p1", "path": "source.py"})["result"]
    assert "super-private-test-value" not in preview["content"]
    assert "fixture-only-key" not in preview["content"]
    assert preview["truncated"] and len(preview["content"]) < 66000
    for path in ("../p2/main.py", "link.py", ".env", "binary.png", "private.py"):
        result = call("projects.read", {"project_id": "p1", "path": path})
        assert "error" in result
        assert "never-display" not in str(result)
    assert "private.py" not in str(call("projects.files", {"project_id": "p1"})["result"])


def test_preferences_preserve_policies_and_inherited_permissions(workspace):
    _, service, call, _, _, _ = workspace
    settings = service.projects.db.get_settings("p1")
    settings.coding_preferences = {"permissions": {"allow_mcp": True},
        "policy": {"filesystem.write": "deny"}, "pinned_files": ["main.py"]}
    settings.permissions["terminal"] = "never"
    service.projects.db.save_settings(settings)
    requested = {"project_id": "p1", "allow_reads": True, "allow_edits": True,
                 "allow_terminal": True, "allow_tests": True}
    effective = call("coding.preferences.update", requested)["result"]
    assert effective == {"allow_reads": True, "allow_edits": False, "allow_terminal": False, "allow_tests": False}
    saved = service.projects.db.get_settings("p1")
    assert saved.coding_preferences["permissions"]["allow_mcp"] is True
    assert saved.coding_preferences["pinned_files"] == ["main.py"]
    assert saved.coding_preferences["policy"] == {"filesystem.write": "deny"}
    assert saved.permissions["terminal"] == "never"
    assert call("coding.preferences", {"project_id": "p1"})["result"] == effective

    disabled = call("coding.preferences.update", {**requested, "allow_reads": False})["result"]
    assert disabled["allow_reads"] is False
    assert "error" in call("projects.files", {"project_id": "p1"})
    assert "error" in call("projects.read", {"project_id": "p1", "path": "main.py"})

    saved.permissions["terminal"] = "allow"
    saved.coding_preferences["policy"]["shell.safe"] = "deny"
    service.projects.db.save_settings(saved)
    assert call("coding.preferences", {"project_id": "p1"})["result"]["allow_terminal"] is True
    saved.coding_preferences["policy"]["shell.network"] = "deny"
    service.projects.db.save_settings(saved)
    assert call("coding.preferences", {"project_id": "p1"})["result"]["allow_terminal"] is False


def test_explicit_context_is_validated_before_run_and_reaches_preparation(workspace, monkeypatch):
    server, service, call, state, roots, _ = workspace
    root = roots["p1"]
    (root / "selected.py").write_text("selected_marker = 42\n")
    (root / "link.py").symlink_to(roots["p2"] / "main.py")
    (root / ".env").write_text("never-display")
    p = coding_params(call)
    for paths in (["../p2/main.py"], ["link.py"], [".env"], ["absent.py"], ["main.py"] * 25):
        assert "error" in call("coding.start", {**p, "prompt": "İncele", "model": "fixture-model", "context_paths": paths})
        assert not server.workspaces.coding_state(**p).busy
    assert not state["requests"]
    assert not server.workspaces.coding_state(**p).messages

    selections = []
    original = service.projects.coding.prepare_coding_prompt

    def prepare(*args, **kwargs):
        selections.append(kwargs.get("relevant_paths"))
        return original(*args, **kwargs)

    monkeypatch.setattr(service.projects.coding, "prepare_coding_prompt", prepare)
    monkeypatch.setattr(service, "model_effort_levels", lambda model: ["high"])
    result = call("coding.start", {**p, "prompt": "Seçili kaynağı açıkla", "model": "fixture-model", "mode": "ask",
                                  "context_paths": ["selected.py", "selected.py"], "reasoning_effort": "high"})["result"]
    eventually(lambda: not server.workspaces.coding_state(**p).busy)
    assert selections == [["selected.py"]]
    assert result["reasoning_effort"] == "high"
    assert call("coding.get", p)["result"]["reasoning_effort"] == "high"
    assert any("selected_marker = 42" in str(row) for row in state["requests"][0])


def test_busy_preferences_are_rejected_and_git_lists_selectable_paths(workspace, monkeypatch):
    server, _, call, state, roots, _ = workspace
    root = roots["p1"]
    (root / "main.py").write_text("value = 3\n")
    (root / "new file.py").write_text("new = 1\n")
    (root / ".env").write_text("secret=never-display")
    with monkeypatch.context() as git_patch:
        if os.name == "nt":
            # Windows cannot create control characters in filenames. Supply the
            # equivalent Git entry so the overview still validates that path.
            status = GitService.get_status(root)
            status["files"].append({"path": "odd\nname.py", "index": "?",
                                    "worktree": "?", "original_path": ""})
            git_patch.setattr(GitService, "get_status", lambda path: status)
        else:
            (root / "odd\nname.py").write_text("odd = 1\n")
        overview = call("git.overview", {"project_id": "p1"})["result"]
    assert {row["path"] for row in overview["files"]} == {"main.py", "new file.py"}
    assert next(row for row in overview["files"] if row["path"] == "main.py")["worktree"] == "M"

    state["delay"] = .5
    p = coding_params(call)
    run = call("coding.start", {**p, "prompt": "Projeyi incele", "model": "fixture-model", "mode": "ask"})["result"]
    assert "error" in call("coding.preferences.update", {"project_id": "p1", "allow_reads": False,
        "allow_edits": False, "allow_terminal": False, "allow_tests": False})
    call("coding.control", {**p, "run_id": run["run_id"], "action": "cancel"})
    eventually(lambda: not server.workspaces.coding_state(**p).busy)


def test_new_run_preparation_does_not_show_previous_run_evidence(workspace, monkeypatch):
    server, service, call, state, _, _ = workspace
    state["edit"] = True
    p = coding_params(call)
    run = call("coding.start", {**p, "prompt": "main.py değerini değiştir", "model": "fixture-model"})["result"]
    eventually(lambda: bool(server.workspaces.coding_state(**p).approvals))
    approval = server.workspaces.coding_state(**p).approvals[0]
    call("coding.control", {**p, "run_id": run["run_id"], "action": "approve",
         "request_id": approval.request_id, "allowed": True})
    eventually(lambda: not server.workspaces.coding_state(**p).busy)
    assert server.workspaces.coding_state(**p).changes

    entered, release = threading.Event(), threading.Event()
    original = service.projects.coding.prepare_coding_prompt

    def prepare(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(service.projects.coding, "prepare_coding_prompt", prepare)
    call("coding.start", {**p, "prompt": "Sonucu incele", "model": "fixture-model", "mode": "ask"}, wait=False)
    try:
        assert entered.wait(2)
        current = call("coding.get", p)["result"]
        assert current["status"] == "preparing" and current["busy"]
        assert not any(current[key] for key in ("plan", "changes", "tests", "approvals"))
        call("coding.control", {**p, "run_id": current["run_id"], "action": "cancel"})
    finally:
        release.set()
    eventually(lambda: not server.workspaces.coding_state(**p).busy)
