"""Git God UI checks against a real temporary repository and real Tk widgets."""
from __future__ import annotations

import queue
import csv
import json
import os
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import customtkinter as ctk
import pytest

from firik_agent.projects.git_service import GitService
from firik_agent.ui.app import _ensure_tk_environment
from firik_agent.ui.views.git_view import GitView


def git(path: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=path, text=True, capture_output=True, check=True)
    return result.stdout.strip()


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-b", "main")
    git(tmp_path, "config", "user.name", "Ada Developer")
    git(tmp_path, "config", "user.email", "ada@example.test")
    git(tmp_path, "config", "commit.gpgsign", "false")
    git(tmp_path, "config", "core.hooksPath", "/dev/null")
    (tmp_path / "source.py").write_text("first = 1\n", encoding="utf-8")
    git(tmp_path, "add", "source.py")
    git(tmp_path, "commit", "-m", "initial source")
    git(tmp_path, "config", "user.name", "Bora Developer")
    git(tmp_path, "config", "user.email", "bora@example.test")
    (tmp_path / "source.py").write_text("first = 1\nsecond = 2\n", encoding="utf-8")
    git(tmp_path, "add", "source.py")
    git(tmp_path, "commit", "-m", "add second line")
    git(tmp_path, "branch", "feature")
    git(tmp_path, "tag", "v1.0.0")
    (tmp_path / "file with spaces.txt").write_text("new work\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def ui(repository: Path):
    callbacks: queue.Queue = queue.Queue()
    _ensure_tk_environment()
    root = ctk.CTk()
    root.geometry("940x820")
    root.grid_columnconfigure(0, weight=1)
    root.grid_rowconfigure(0, weight=1)
    service = SimpleNamespace(_dispatch=lambda callback, *args: callbacks.put((callback, args)))
    view = GitView(root, service, str(repository))
    view.grid(row=0, column=0, sticky="nsew", padx=12, pady=12)

    def pump(predicate=lambda: not view._busy, timeout: float = 10.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            root.update()
            while not callbacks.empty():
                callback, args = callbacks.get_nowait()
                callback(*args)
            root.update_idletasks()
            if predicate():
                return
            time.sleep(0.01)
        pytest.fail(f"Git UI did not finish: {view.status_label.cget('text')}")

    pump()
    yield root, view, pump, callbacks
    view.destroy()
    root.destroy()


def test_git_view_actual_repository_sections_and_authorship(ui, repository: Path):
    root, view, pump, _ = ui
    assert view._dashboard_data["summary"]["commits"] == 2
    assert view._dashboard_data["summary"]["contributors"] == 2
    assert view.metric_values["changes"].cget("text") == "1"
    assert view.activity_canvas.find_all()
    assert view.contributor_canvas.find_all()
    view.show_section("history")
    pump()
    assert len(view.history_table._rows) == 2
    assert len(view.graph_canvas.find_all()) > 4
    view.history_table.tree.selection_set("0")
    view._history_selected(view.history_table)
    pump()
    assert "+second = 2" in view.commit_detail.get("1.0", "end")
    view.open_ownership("source.py")
    pump()
    assert [row["author"] for row in view.blame_table._rows.values()] == ["Ada Developer", "Bora Developer"]
    assert view.file_history_table._rows["0"]["author"] == "Bora Developer"
    view.ownership_ref.insert(0, "HEAD~1")
    view._load_ownership()
    pump()
    assert [row["author"] for row in view.blame_table._rows.values()] == ["Ada Developer"]
    assert view.file_history_table._rows["0"]["author"] == "Ada Developer"
    assert len(view.file_history_table._rows) == 1
    view.show_section("branches")
    pump()
    assert {row["name"] for row in view.branch_table._rows.values()} == {"main", "feature"}
    view._compare_branches()
    pump()
    assert "+second = 2" in view.compare_diff.get("1.0", "end")
    view.show_section("recovery")
    pump()
    assert view.reflog_table._rows
    assert view.tag_table._rows["0"]["name"] == "v1.0.0"
    assert view.worktree_table._rows


def test_git_view_responsive_navigation_and_real_sorting(ui):
    root, view, pump, _ = ui
    for width in (600, 940, 1120, 1600):
        root.geometry(f"{width}x820")
        pump()
        view._resize()
        root.update()
        for button in view.nav_buttons.values():
            assert button.winfo_x() >= 0
            assert button.winfo_x() + button.winfo_width() <= view.nav.winfo_width() + 2
        assert view.body.winfo_width() <= view.winfo_width() + 2
        for card in view.body.winfo_children():
            assert card.winfo_x() + card.winfo_width() <= view.body.winfo_width() + 2
        for table in view._tables:
            assert table.winfo_x() + table.winfo_width() <= table.master.winfo_width() + 2
        assert view.status_label.winfo_width() > 150
    table = view.contributor_table
    table.sort("commits")
    assert len(table.tree.get_children()) == 2
    ctk.set_appearance_mode("Light")
    view._resize()
    assert str(table.tree.tag_configure("current", "foreground")) == "#087c84"
    ctk.set_appearance_mode("Dark")
    view._resize()


def test_git_view_preserves_spaces_in_ownership_and_tool_paths(ui, repository: Path):
    root, view, pump, _ = ui
    filename = " çorba tarifi .py" if os.name == "nt" else " çorba tarifi .py "
    (repository / filename).write_text("tarif = 1\n", encoding="utf-8")
    git(repository, "add", "--", filename)
    git(repository, "commit", "-m", "spaced filename")
    view.open_ownership(filename)
    pump()
    assert len(view.blame_table._rows) == 1
    assert view.file_history_table._rows["0"]["message"] == "spaced filename"
    assert not view._errors
    view.open_tool("stage", {"paths": [filename]})
    pump()
    assert view._tool_params()["paths"] == filename
    assert view._last_preview["ok"]
    assert filename in view._last_preview["args"]


def test_git_view_stage_commit_and_preview_confirmation(ui, repository: Path, monkeypatch):
    root, view, pump, _ = ui
    prompts = []
    monkeypatch.setattr("tkinter.messagebox.askyesno", lambda title, prompt, **kwargs: prompts.append(prompt) or True)
    view.show_section("changes")
    pump()
    iid = next(key for key, row in view.changes_table._rows.items() if row["path"] == "file with spaces.txt")
    view.changes_table.tree.selection_set(iid)
    view._change_action("stage")
    pump(lambda: not view._busy and not view._mutation_busy)
    assert "file with spaces.txt" in git(repository, "diff", "--cached", "--name-only")
    assert "git" in prompts[0] and "file with spaces.txt" in prompts[0]
    view.commit_message.insert(0, "feat: add spaced file")
    view._commit_changes()
    pump(lambda: not view._busy and not view._mutation_busy)
    assert git(repository, "log", "-1", "--format=%s") == "feat: add spaced file"
    assert len(prompts) == 2


def test_git_view_invalid_tool_preview_never_executes(ui, monkeypatch):
    root, view, pump, _ = ui
    view.open_tool("branch_create")
    pump()
    assert view._tool_id == "branch_create"
    assert not view._last_preview
    executed = []
    monkeypatch.setattr(GitService, "execute_tool", lambda *args, **kwargs: executed.append((args, kwargs)))
    view._run_selected_tool()
    pump()
    assert not executed
    assert view._errors
    assert view.tool_preview.get("1.0", "end").strip()


def test_git_view_rename_unstage_restores_both_paths(ui, repository: Path, monkeypatch):
    root, view, pump, _ = ui
    monkeypatch.setattr("tkinter.messagebox.askyesno", lambda *args, **kwargs: True)
    git(repository, "mv", "source.py", "renamed.py")
    view.show_section("changes")
    pump()
    iid = next(key for key, row in view.changes_table._rows.items() if row["path"] == "renamed.py")
    assert view.changes_table._rows[iid]["original_path"] == "source.py"
    view.changes_table.tree.selection_set(iid)
    view._change_action("unstage")
    pump(lambda: not view._busy and not view._mutation_busy)
    assert git(repository, "diff", "--cached", "--name-only") == ""


def test_git_view_exports_actual_dashboard_json_and_contributor_csv(ui, tmp_path: Path, monkeypatch):
    root, view, pump, _ = ui
    data = view._dashboard_data
    json_path = tmp_path / "git-analysis.json"
    monkeypatch.setattr("tkinter.filedialog.asksaveasfilename", lambda **kwargs: str(json_path))
    view.export_dashboard()
    pump()
    exported = json.loads(json_path.read_text(encoding="utf-8"))
    assert exported == data
    assert exported["summary"]["commits"] == 2
    assert exported["scope"]["ref"] == "HEAD"
    csv_path = tmp_path / "contributors.csv"
    monkeypatch.setattr("tkinter.filedialog.asksaveasfilename", lambda **kwargs: str(csv_path))
    view.export_dashboard()
    pump()
    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        contributors = list(csv.DictReader(handle))
    assert {item["author"] for item in contributors} == {"Ada Developer", "Bora Developer"}
    assert sum(int(item["commits"]) for item in contributors) == 2
    assert sum(int(item["added"]) for item in contributors) == data["summary"]["added"]


def test_git_view_agent_handoff_carries_selected_context(ui, repository: Path):
    root, view, pump, _ = ui
    handoffs = []
    calls = []
    view.on_ask_agent = handoffs.append
    view.service.chat_agent_stream_async = lambda **kwargs: calls.append(kwargs)
    view.show_section("history")
    pump()
    view.history_table.tree.selection_set("0")
    commit_hash = view.history_table._rows["0"]["hash"]
    view.ask_agent("commit")
    assert len(handoffs) == 1
    assert str(repository) in handoffs[-1]
    assert commit_hash in handoffs[-1]
    view.open_ownership("source.py")
    pump()
    view.ask_agent()
    assert "source.py" in handoffs[-1]
    view.open_tool("line_history", {"path": "source.py", "start": 1, "end": 2})
    pump()
    view.ask_agent("tool")
    assert "line_history" in handoffs[-1] and "source.py" in handoffs[-1]
    assert not calls


def test_git_view_ignores_late_results_and_keeps_independent_errors(ui):
    root, view, pump, callbacks = ui
    release = threading.Event()
    delivered = []
    view._request("late", lambda: release.wait(3) or "late", delivered.append)
    view.show_section("history")
    release.set()
    pump()
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        while not callbacks.empty():
            callback, args = callbacks.get_nowait()
            callback(*args)
        root.update()
        time.sleep(0.01)
    assert not delivered
    view._request("broken", lambda: (_ for _ in ()).throw(ValueError("blame failed")), lambda result: None)
    pump()
    view._request("other", lambda: "file history ready", lambda result: view._status(result))
    pump()
    assert "blame failed" in view.status_label.cget("text")
    assert "broken" in view._errors


def test_git_view_non_repository_has_explicit_error(tmp_path: Path):
    callbacks: queue.Queue = queue.Queue()
    _ensure_tk_environment()
    root = ctk.CTk()
    service = SimpleNamespace(_dispatch=lambda callback, *args: callbacks.put((callback, args)))
    view = GitView(root, service, str(tmp_path))
    view.pack(fill="both", expand=True)
    deadline = time.monotonic() + 5
    try:
        while view._busy and time.monotonic() < deadline:
            root.update()
            while not callbacks.empty():
                callback, args = callbacks.get_nowait()
                callback(*args)
            time.sleep(0.01)
        assert view._errors
        assert view.metric_values["commits"].cget("text") == "—"
    finally:
        view.destroy()
        root.destroy()
