from __future__ import annotations

from pathlib import Path
import os

import pytest

from evren_agent.projects.context_builder import CONTEXT_POLICY, ProjectContextBuilder, approx_tokens
from evren_agent.projects.coding_service import ProjectCodingService
from evren_agent.projects.db import ProjectDatabase
from evren_agent.projects.explorer import ProjectExplorer
from evren_agent.projects.indexer import CodebaseIndexer, is_sensitive_file, read_project_text, safe_project_path, sanitize_git_diff, sanitize_secrets
from evren_agent.projects.model_capabilities import context_budget, model_capabilities
from evren_agent.projects.models import (
    ArchitectureDecision, CodingSession, Project, ProjectInstruction, ProjectMemory,
    ProjectSettings, Task,
)
from evren_agent.projects.skill_selection import select_skill_instructions, skill_catalogue
from evren_agent.projects.workspace import capture_workspace


def builder(root: Path, budget=1500, **kwargs):
    project = Project(id="p", name="Türkçe Proje", local_path=str(root))
    settings = ProjectSettings(project_id="p", token_budget={"code": 100000, "instructions": 100000})
    return ProjectContextBuilder(project, settings, instructions=kwargs.pop("instructions", []),
                                 memories=kwargs.pop("memories", []), total_token_budget=budget, **kwargs)


def test_context_budget_normalizes_and_counts_policy_headers_and_role(tmp_path):
    (tmp_path / "main.py").write_text("print('çay')")
    context = builder(tmp_path, budget=300, relevant_files={"main.py": "ç" * 20000},
                      instructions=[ProjectInstruction(id="i", project_id="p", title="Kural", content="x" * 20000)])
    prompt = context.assemble_system_prompt("rol" * 100)
    assert prompt.startswith(CONTEXT_POLICY)
    assert len(prompt) <= 300 * 4
    assert approx_tokens(prompt) <= 300
    assert sum(item["token_budget"] for item in context.get_visibility_report()) <= 300
    assert any(item["truncated"] for item in context.get_visibility_report())
    assert all(item["estimate_only"] for item in context.get_visibility_report())


def test_tiny_context_budget_cannot_discard_safety(tmp_path):
    with pytest.raises(ValueError, match="güvenlik"):
        builder(tmp_path, budget=1).assemble_system_prompt("rol")


def test_only_same_project_approved_memory_and_accepted_adrs_enter_context(tmp_path):
    memories = [ProjectMemory(id="a", project_id="p", content="ONAYLI"),
                ProjectMemory(id="b", project_id="p", content="ADAY", status="candidate"),
                ProjectMemory(id="c", project_id="other", content="BAŞKA_PROJE")]
    adrs = [ArchitectureDecision(id="a", project_id="p", adr_number="1", title="Doğru", decision="KABUL"),
            ArchitectureDecision(id="b", project_id="p", adr_number="2", title="Öneri", decision="ÖNERİ", status="proposed")]
    context = builder(tmp_path, memories=memories, adrs=adrs)
    prompt = context.assemble_system_prompt("Türkçe rol")
    assert "ONAYLI" in prompt and "KABUL" in prompt
    assert "ADAY" not in prompt and "BAŞKA_PROJE" not in prompt and "ÖNERİ" not in prompt
    assert "analiz edilecek veri" in prompt


def test_session_carry_preserves_unfinished_decisions_and_real_tests(tmp_path):
    previous = CodingSession(id="s", project_id="p", title="Önceki", summary="Eksik görev",
                             plan="Testi tekrar çalıştır", result="Kesildi",
                             tests_executed=[{"command": "pytest", "exit_code": 1}],
                             agent_actions=[{"decision": "Kullanıcı dağıtımı reddetti"}])
    context = builder(tmp_path, last_session=previous,
                      session_carry={"pending_steps": ["Türkçe arayüzü doğrula"], "decisions": ["Kapsam sabit"]})
    prompt = context.assemble_system_prompt("Rol")
    assert "Eksik görev" in prompt and "Türkçe arayüzü doğrula" in prompt
    assert '"exit_code": 1' in prompt and "dağıtımı reddetti" in prompt


def test_index_read_search_and_context_deny_external_symlinks_and_secrets(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("class ExternalSecret: pass\n")
    (project / "escape.py").symlink_to(outside)
    (project / "source.py").write_text("class TürkçeSınıf: pass\n")
    (project / ".env").write_text("API_KEY=SHOULD_NOT_ENTER_CONTEXT\n")
    indexer = CodebaseIndexer(project)
    assert "escape.py" not in [path.name for path in indexer.scan_files()]
    assert indexer.index_file(project / "escape.py") is None
    assert indexer.index_file(outside) is None
    for path in ("../outside.py", "escape.py", ".env"):
        with pytest.raises(ValueError):
            read_project_text(project, path)
    matches = indexer.search_codebase("ExternalSecret", [{"file_path": "../outside.py", "symbols": ["ExternalSecret"]}])
    assert matches == []
    context = builder(project, relevant_files={"../outside.py": "ExternalSecret", ".env": "bad", "source.py": "good"})
    assert list(context.relevant_files) == ["source.py"]


def test_secret_sanitizing_private_key_bearer_environment_and_diff_paths():
    raw = 'API_KEY=plain-secret\nAuthorization: Bearer private-token\npassword="small"\n-----BEGIN PRIVATE KEY-----\nPRIVATE_BODY\n-----END PRIVATE KEY-----'
    clean = sanitize_secrets(raw)
    assert all(secret not in clean for secret in ("plain-secret", "private-token", "small", "PRIVATE_BODY"))
    diff = 'diff --git a/.env b/.env\n--- a/.env\n+++ b/.env\n+SAFE_LOOKING_BUT_SECRET\ndiff --git a/main.py b/main.py\n--- a/main.py\n+++ b/main.py\n+api_key="abc123"\n+print("çay")\n'
    clean = sanitize_git_diff(diff)
    assert "SAFE_LOOKING_BUT_SECRET" not in clean and "abc123" not in clean
    assert 'print("çay")' in clean


def test_indexer_preserves_ast_and_hash_without_disclosing_credential_todos(tmp_path):
    path = tmp_path / "source.py"
    path.write_text('class Login:\n    def call(self, password: str):\n        # TODO: API_KEY=sample-secret\n        return password\n')
    item = CodebaseIndexer(tmp_path).index_file(path)
    assert "Login" in item["symbols"] and "call" in item["symbols"]
    assert "sample-secret" not in item["todos"][0]["text"]


def test_context_read_rejects_external_hardlink_under_an_innocent_source_name(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "private.txt"
    outside.write_text("PRIVATE_CONTENT")
    os.link(outside, project / "source.py")
    with pytest.raises(ValueError, match="bağımsız"):
        read_project_text(project, "source.py")


@pytest.mark.skipif(not hasattr(os, "O_NOFOLLOW") or os.open not in os.supports_dir_fd,
                    reason="İşletim sistemi no-follow klasör okumasını desteklemiyor")
def test_context_read_rejects_directory_symlink_swap_between_check_and_open(tmp_path, monkeypatch):
    project = tmp_path / "project"
    source = project / "src"
    source.mkdir(parents=True)
    (source / "source.py").write_text("TRUSTED")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "source.py").write_text("PRIVATE_CONTENT")
    original_open = os.open
    swapped = []
    def swapping_open(path, flags, mode=0o777, *, dir_fd=None):
        if path == "src" and dir_fd is not None and not swapped:
            source.rename(project / "original_src")
            source.symlink_to(outside, target_is_directory=True)
            swapped.append(True)
        return original_open(path, flags, mode, dir_fd=dir_fd)
    monkeypatch.setattr(os, "open", swapping_open)
    monkeypatch.setattr(os, "supports_dir_fd", {swapping_open})
    with pytest.raises(OSError):
        read_project_text(project, "src/source.py")
    assert swapped == [True]


def test_explorer_is_lazy_and_shows_index_and_git_without_opening_source(tmp_path, monkeypatch):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('çay')\n")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / ".hidden").write_text("hidden")
    (tmp_path / ".env").write_text("secret")
    (tmp_path / "readme.md").write_text("merhaba")
    explorer = ProjectExplorer(tmp_path, indexed_data=[{"file_path": "src/main.py", "symbols": ["Türkçe"]}],
                               git_status={"src/main.py": "M"})
    monkeypatch.setattr(Path, "read_text", lambda *args, **kwargs: pytest.fail("Lazy children read a file"))
    assert [item["path"] for item in explorer.children()] == ["src", "readme.md"]
    child = explorer.children("src")[0]
    assert child["indexed"] and child["git_status"] == "M" and child["symbols"] == ["Türkçe"]
    customized = ProjectExplorer(tmp_path, ignored_dirs=[], show_hidden=True)
    paths = [item["path"] for item in customized.children()]
    assert "node_modules" in paths and ".hidden" in paths and ".env" not in paths


def test_explorer_bounded_content_search_returns_real_lines_and_filters_secrets(tmp_path):
    (tmp_path / "source.py").write_text('name = "merhaba"\nprint("Türkçe çay")\nAPI_KEY="secret-matches"\n')
    explorer = ProjectExplorer(tmp_path)
    results = explorer.search("Türkçe çay", content=True)
    assert results[0]["line"] == 2 and "Türkçe çay" in results[0]["preview"]
    assert explorer.search("secret-matches", content=True) == []
    assert explorer.search("source")[0]["path"] == "source.py"


def test_model_capacity_and_price_are_unknown_without_explicit_verified_provider_data():
    guessed = model_capabilities({"context_window": 128000, "supports_tools": True})
    assert guessed["context_window"] is None and guessed["supports_tools"] is None
    assert context_budget(guessed)["capacity_known"] is False
    actual = model_capabilities({"source": "provider", "context_window": 10000,
                                 "supports_tools": False, "supports_streaming": True,
                                 "input_cost_per_million": 0.5})
    assert actual["supports_tools"] is False and actual["context_window"] == 10000
    assert context_budget(actual, local_budget=9000, reserved_tokens=4000)["token_budget"] == 6000
    assert actual["output_cost_per_million"] is None


def test_skill_catalogue_does_not_load_instruction_bodies_and_only_selection_loads():
    loaded = []
    skills = [{"name": "test_skill", "description": "Türkçe test", "trigger": "Test isteği",
               "instructions": lambda: loaded.append(True) or "Yalnız seçildiğinde yükle"},
              {"name": "other", "description": "Diğer", "instructions": "OTHER_BODY"}]
    catalogue = skill_catalogue(skills)
    assert loaded == [] and "instructions" not in catalogue[0]
    selected = select_skill_instructions(skills, ["test-skill"])
    assert loaded == [True] and len(selected) == 1
    assert selected[0]["instructions"] == "Yalnız seçildiğinde yükle"


def test_prepare_coding_prompt_refreshes_task_rules_and_files_and_rejects_cross_project(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "main.py").write_text("class AuthService: pass\n")
    db = ProjectDatabase(tmp_path / "projects.db")
    db.create_project(Project(id="p", name="Proje", local_path=str(root)))
    db.create_project(Project(id="other", name="Başka", local_path=str(root)))
    task = Task(id="t", project_id="p", title="AuthService", acceptance_criteria=["Gerçek test"], related_files=["main.py"])
    db.save_task(task)
    db.save_task(Task(id="other_task", project_id="other", title="Başka görev"))
    coding = ProjectCodingService(db)
    first = coding.prepare_coding_prompt("p", "AuthService açıkla", task_id="t")
    assert "Gerçek test" in first["system_prompt"] and "AuthService" in first["system_prompt"]
    assert first["context_budget"]["context_window"] is None
    (root / "main.py").write_text("class UpdatedAuthService: pass\n")
    db.save_instruction(ProjectInstruction(id="i", project_id="p", title="Güncel kural", content="Türkçe yeni kural"))
    second = coding.prepare_coding_prompt("p", "AuthService açıkla", task_id="t")
    assert "UpdatedAuthService" in second["system_prompt"] and "Güncel kural" in second["system_prompt"]
    assert db.get_task("t").status == task.status  # Preparing/code generation never completes a task.
    with pytest.raises(ValueError, match="projeye"):
        coding.prepare_task_agent_prompt("p", "other_task")
    with pytest.raises(ValueError, match="projeye"):
        coding.prepare_coding_prompt("p", "devam", session_carry={"project_id": "other"})
    with pytest.raises(ValueError, match="göreve"):
        coding.prepare_coding_prompt("p", "devam", task_id="t", session_carry={"task_id": "other_task"})


def test_denied_file_reads_do_not_run_content_search_or_read_diff(tmp_path, monkeypatch):
    db = ProjectDatabase(tmp_path / "projects.db")
    db.create_project(Project(id="p", name="Proje", local_path=str(tmp_path)))
    settings = db.get_settings("p")
    settings.permissions["read_files"] = "never"
    db.save_settings(settings)
    monkeypatch.setattr(CodebaseIndexer, "search_content", lambda *a, **kw: pytest.fail("Denied read performed search"))
    result = ProjectCodingService(db).prepare_coding_prompt("p", "dosyaları incele", relevant_paths=["main.py"])
    assert result["relevant_files"] == []
    settings.permissions["read_files"] = "allow"
    db.save_settings(settings)
    result = ProjectCodingService(db).prepare_coding_prompt("p", "dosyaları incele",
                                                           permissions={"allow_reads": False})
    assert result["relevant_files"] == []


@pytest.mark.parametrize("container", [".codex", ".agents"])
def test_managed_checkout_ancestors_are_not_project_local_sensitive_folders(tmp_path, container):
    project = tmp_path / container / "worktrees" / "managed" / "repo"
    project.mkdir(parents=True)
    source = project / "main.py"
    source.write_text("class ManagedSource: pass\n")
    for hidden in (".codex", ".agents", ".aws"):
        (project / hidden).mkdir()
        (project / hidden / "config.txt").write_text("PRIVATE_CONTENT")
    assert not is_sensitive_file(source, project_root=project)
    assert read_project_text(project, "main.py") == "class ManagedSource: pass\n"
    indexer = CodebaseIndexer(project)
    assert [path.name for path in indexer.scan_files()] == ["main.py"]
    indexed = indexer.index_file(source)
    assert "ManagedSource" in indexed["symbols"]
    assert indexer.search_codebase("ManagedSource", [indexed])[0]["file_path"] == "main.py"
    assert list(capture_workspace(project).files) == ["main.py"]
    explorer = ProjectExplorer(project, show_hidden=True)
    assert [item["path"] for item in explorer.children()] == ["main.py"]
    for protected in (".codex/config.txt", ".agents/config.txt", ".aws/config.txt"):
        assert is_sensitive_file(project / protected, project_root=project)
        with pytest.raises(ValueError, match="Hassas"):
            read_project_text(project, protected)


def test_protected_container_credentials_and_filesystem_root_cannot_become_project_roots(tmp_path):
    protected = tmp_path / ".codex"
    credentials = tmp_path / ".aws" / "checkout"
    protected.mkdir()
    credentials.mkdir(parents=True)
    for root in (protected, credentials):
        with pytest.raises(ValueError, match="proje kökü"):
            safe_project_path(root, "source.py")
    with pytest.raises(ValueError, match="Dosya sistemi kökü"):
        safe_project_path(Path(tmp_path.anchor), "source.py")


def test_selected_folder_loads_bounded_ranked_sources_and_reports_unavailable_paths(tmp_path):
    project = tmp_path / ".codex" / "worktrees" / "managed" / "repo"
    source = project / "src"
    source.mkdir(parents=True)
    (source / "empty").mkdir()
    for number in range(32):
        (source / f"source_{number:02}.py").write_text(f"value_{number} = {number}\n")
    desired = source / "zzz_relevant.py"
    desired.write_text("class TargetFeature: pass\n")
    (source / ".env").write_text("PRIVATE_CONTENT")
    outside = tmp_path / "outside.py"
    outside.write_text("PRIVATE_CONTENT")
    (source / "external.py").symlink_to(outside)
    db = ProjectDatabase(tmp_path / "projects.db")
    db.create_project(Project(id="p", name="Managed", local_path=str(project)))
    indexed = CodebaseIndexer(project).index_file(desired)
    db.save_code_index("p", **indexed)
    result = ProjectCodingService(db).prepare_coding_prompt("p", "TargetFeature görevi", relevant_paths=["src", "src/empty", "../outside.py"])
    assert result["relevant_files"][0] == "src/zzz_relevant.py"
    assert len(result["relevant_files"]) == 12
    assert "TargetFeature" in result["system_prompt"]
    assert "PRIVATE_CONTENT" not in result["system_prompt"]
    assert "src/external.py" not in result["relevant_files"]
    reports = {item["path"]: item["reason"] for item in result["unavailable_report"]}
    assert "12 dosya" in reports["src"]
    assert "güvenli kaynak" in reports["src/empty"]
    assert "dışında" in reports["../outside.py"]
