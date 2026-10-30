"""Headless recovery surfaces use real SQLite and never construct a provider."""
import json
import sys
from types import SimpleNamespace

import pytest

from firik_agent.projects.coding_sessions import CodingSessionStore
from firik_agent.projects.db import ProjectDatabase
from firik_agent.projects.models import Project
from firik_agent.projects.resume import DurableResumeController
from firik_agent.ui.coding_recovery import recovery_summary


@pytest.fixture
def saved_run(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    (root / "main.py").write_text("value = 1\n")
    db = ProjectDatabase(tmp_path / "projects.db")
    db.create_project(Project(id="p", name="Example", local_path=str(root)))
    store = CodingSessionStore(db)
    from firik_agent.projects.recovery_cli import _permissions
    run = store.create_run("p", session_id="session", run_id="source_run", permissions=_permissions(db, "p"))
    DurableResumeController(db).record_workspace(run.id, root)
    store.finish_run(run.id, "interrupted")
    return db, store, run, root


def test_runs_dispatch_precedes_live_agent_and_credentials(monkeypatch, capsys, saved_run):
    from firik_agent import cli
    db, store, run, root = saved_run
    def forbidden(*args, **kwargs):
        pytest.fail("Recovery CLI constructed a paid/live agent")
    monkeypatch.setattr(cli, "Agent", forbidden)
    monkeypatch.setattr(cli, "ensure_provider_key", forbidden)
    monkeypatch.setattr(sys, "argv", ["firik-agent", "runs", "--db", str(db.db_path),
                                    "inspect", run.id, "--project", "p"])
    with pytest.raises(SystemExit) as result:
        cli.main()
    assert result.value.code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["run_id"] == run.id and report["resume_eligible"]
    assert store.get_run(run.id).status == "interrupted"


def test_cli_resume_requires_approval_and_only_prepares_once(saved_run, capsys):
    from firik_agent.projects.recovery_cli import main
    db, store, run, root = saved_run
    args = ["--db", str(db.db_path), "resume", run.id, "--project", "p"]
    assert main(args) == 2
    assert len(store.list_runs("p")) == 1
    capsys.readouterr()
    assert main(args + ["--approve"]) == 0
    prepared = json.loads(capsys.readouterr().out)
    assert prepared["execution_started"] is False and prepared["status"] == "queued"
    assert main(args + ["--approve"]) == 0
    repeated = json.loads(capsys.readouterr().out)
    assert repeated["run_id"] == prepared["run_id"]
    assert len(store.list_runs("p")) == 2
    assert (root / "main.py").read_text() == "value = 1\n"


def test_cli_workspace_reconciliation_preserves_changes_and_requires_approval(saved_run, capsys):
    from firik_agent.projects.recovery_cli import main
    db, store, run, root = saved_run
    (root / "main.py").write_text("user = 2\n")
    args = ["--db", str(db.db_path), "reconcile", run.id, "--project", "p",
            "--workspace", "--evidence", "Reviewed the user's main.py change"]
    assert main(args) == 2
    capsys.readouterr()
    assert main(args + ["--approve"]) == 0
    assert (root / "main.py").read_text() == "user = 2\n"
    assert DurableResumeController(db).inspect(run.id, project_id="p")["resume_eligible"]


def test_report_export_sanitizes_and_does_not_overwrite(saved_run, tmp_path, capsys):
    from firik_agent.projects.recovery_cli import main
    db, store, run, root = saved_run
    store.record_decision(run.id, {"api_key": "TOP_SECRET_123456789", "token": "another-secret"})
    path = tmp_path / "report.json"
    args = ["--db", str(db.db_path), "export", run.id, "--project", "p", "--output", str(path)]
    assert main(args) == 0
    payload = path.read_text()
    assert "TOP_SECRET" not in payload and "another-secret" not in payload
    assert main(args) == 2
    assert path.read_text() == payload


def test_ui_report_never_calls_uncertain_or_conflicted_run_eligible():
    report = {"run_id": "r", "status": "interrupted", "resume_eligible": True,
              "uncertain_operations": [{"attempt_id": "shell"}], "workspace_conflicts": []}
    assert "Devam engellendi" in recovery_summary(report)
    report.update(uncertain_operations=[], workspace_conflicts=[{"path": "main.py"}])
    assert "Devam engellendi" in recovery_summary(report)
    report.update(workspace_conflicts=[])
    assert "Devam hazırlığı uygun" in recovery_summary(report)


def test_wrong_project_is_rejected_without_export(saved_run, tmp_path, capsys):
    from firik_agent.projects.recovery_cli import main
    db, store, run, root = saved_run
    path = tmp_path / "denied.json"
    assert main(["--db", str(db.db_path), "export", run.id, "--project", "wrong", "--output", str(path)]) == 2
    assert not path.exists()


@pytest.mark.parametrize("conflicted", [False, True])
def test_desktop_recovery_is_passive_and_requires_explicit_approval(monkeypatch, conflicted):
    from firik_agent.ui import coding_recovery as ui
    widgets, starts, loads = [], [], []
    class Widget:
        def __init__(self, *args, **kwargs):
            self.options, self.exists = kwargs, True
            widgets.append(self)
        def __getattr__(self, name):
            return lambda *args, **kwargs: None
        def configure(self, **kwargs):
            self.options.update(kwargs)
        def winfo_exists(self):
            return self.exists
        def destroy(self):
            self.exists = False
    for name in ("CTkToplevel", "CTkFrame", "CTkLabel", "CTkTextbox", "CTkButton"):
        monkeypatch.setattr(ui.ctk, name, Widget)
    monkeypatch.setattr(ui.ctk, "CTkFont", lambda **kwargs: None)
    approval = {"allowed": False}
    monkeypatch.setattr(ui.messagebox, "askyesno", lambda *args, **kwargs: approval["allowed"])
    report = {"run_id": "r", "status": "interrupted", "resume_eligible": True,
              "workspace_conflicts": [{"path": "main.py"}] if conflicted else [], "uncertain_operations": []}
    coding = {"record": SimpleNamespace(id="r", status="interrupted", resumed_from=None)}
    service = SimpleNamespace(inspect_coding_recovery_async=lambda p, r, done, fail: done(report))
    view = SimpleNamespace(current_project_id="p", service=service, winfo_toplevel=lambda: None,
        _coding_state=lambda: coding, _load_coding_history=lambda run: loads.append(run) or True,
        _run_coding_prompt=starts.append)
    ui.show_recovery_dialog(view, "r")
    assert not starts and not loads
    button = next(widget for widget in widgets if widget.options.get("text") == "Onayla ve Devam Et")
    assert button.options["state"] == ("disabled" if conflicted else "normal")
    button.options["command"]()
    assert not starts
    approval["allowed"] = True
    button.options["command"]()
    if conflicted:
        assert not starts and not loads
    else:
        assert len(starts) == 1 and loads == ["r"]
        assert coding["resume_source_run_id"] == "r" and coding["resume_approved"] is True
