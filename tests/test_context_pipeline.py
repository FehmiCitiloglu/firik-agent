"""Deterministic relevance, freshness and exclusion integration evidence."""
from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

import pytest

from firik_agent.projects.coding_service import ProjectCodingService
from firik_agent.projects.db import ProjectDatabase
from firik_agent.projects.indexer import CodebaseIndexer, read_project_text, safe_project_path, sanitize_git_diff
from firik_agent.projects.git_service import GitService
from firik_agent.projects.models import ArchitectureDecision, Project, ProjectMemory
from firik_agent.projects.retrieval import ContextRetrievalPipeline, STAGES, dependency_graph


def write(root, relative, content):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content.encode("utf-8"))
    return path


def pipeline(root, query, **kwargs):
    indexer = CodebaseIndexer(root)
    indexed = indexer.refresh_index()
    return ContextRetrievalPipeline(indexer, indexed).run(query, **kwargs)


def test_relevance_scores_are_inspectable_and_do_not_load_unrelated_sources(tmp_path):
    write(tmp_path, "auth.py", "class AuthService:\n    def authenticate(self): pass\n")
    write(tmp_path, "colors.py", "class ColorPalette: pass\n")
    result = pipeline(tmp_path, "Fix AuthService.authenticate")
    assert [row["file_path"] for row in result["candidates"]] == ["auth.py"]
    row = result["candidates"][0]
    assert row["score"] == sum(factor["weight"] for factor in row["factors"])
    assert {factor["factor"] for factor in row["factors"]} >= {"symbol_overlap", "lexical_content"}
    assert result["report"]["stages"] == list(STAGES)
    assert result["report"]["semantic_index_available"] is False


def test_user_pins_are_first_even_when_other_file_matches_many_symbols(tmp_path):
    write(tmp_path, "selected.py", "value = 1\n")
    write(tmp_path, "auth.py", "class AuthService:\n    def auth(self): pass\n")
    result = pipeline(tmp_path, "AuthService auth", pinned=["selected.py"])
    assert result["candidates"][0]["file_path"] == "selected.py"
    assert result["candidates"][0]["source"] == "pinned"


def test_hash_refresh_detects_same_size_same_mtime_edits_deleted_and_new_files(tmp_path):
    source = write(tmp_path, "source.py", "class Before: pass\n")
    write(tmp_path, "gone.py", "class Gone: pass\n")
    indexer = CodebaseIndexer(tmp_path)
    original = indexer.refresh_index()
    before = source.stat()
    source.write_text("class After_: pass\n", encoding="utf-8")
    os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))
    (tmp_path / "gone.py").unlink()
    write(tmp_path, "new.py", "class NewlyAdded: pass\n")
    refreshed = indexer.refresh_index(original)
    lookup = {row["file_path"]: row for row in refreshed}
    assert "After_" in lookup["source.py"]["symbols"]
    assert "gone.py" not in lookup and "new.py" in lookup
    assert indexer.last_refresh["invalidated"] == 1
    revision = indexer.last_refresh["workspace_revision"]
    assert revision and indexer.refresh_index(refreshed) == refreshed
    assert indexer.last_refresh["reused"] == 2 and indexer.last_refresh["updated"] == 0


def test_exclusions_and_repository_ignores_apply_before_reads(tmp_path, monkeypatch):
    write(tmp_path, ".gitignore", "generated/\n*.local.py\n!allowed.local.py\n")
    write(tmp_path, "generated/hidden.py", "class HiddenAuth: pass\n")
    write(tmp_path, "blocked/secret.py", "class HiddenAuth: pass\n")
    write(tmp_path, "credentials/.env", "API_KEY=hidden\n")
    write(tmp_path, "ordinary.local.py", "class HiddenAuth: pass\n")
    write(tmp_path, "allowed.local.py", "class AllowedAuth: pass\n")
    from firik_agent.projects import indexer as module
    original = module.read_project_text
    reads = []
    def guarded(root, path, *args, **kwargs):
        relative = str(path).removeprefix(str(tmp_path) + "/")
        assert not relative.startswith(("blocked/", "generated/", "credentials/"))
        assert relative != "ordinary.local.py"
        reads.append(relative)
        return original(root, path, *args, **kwargs)
    monkeypatch.setattr(module, "read_project_text", guarded)
    indexer = CodebaseIndexer(tmp_path)
    indexed = indexer.refresh_index(excluded=lambda path: path == "blocked" or path.startswith("blocked/"))
    assert "allowed.local.py" in {row["file_path"] for row in indexed}
    assert reads


def test_import_graph_resolves_python_relative_and_ts_and_bounds_cycles(tmp_path):
    write(tmp_path, "pkg/auth.py", "from .helper import work\nclass AuthService: pass\n")
    write(tmp_path, "pkg/helper.py", "from .auth import AuthService\ndef work(): pass\n")
    write(tmp_path, "web/page.ts", "import { thing } from './helper';\nexport const Page = () => thing();\n")
    write(tmp_path, "web/helper.ts", "export const thing = () => 1;\n")
    py = pipeline(tmp_path, "AuthService")
    assert {row["file_path"] for row in py["candidates"]} == {"pkg/auth.py", "pkg/helper.py"}
    ts = pipeline(tmp_path, "Page")
    assert "web/helper.ts" in {row["file_path"] for row in ts["candidates"]}
    assert ts["dependency_graph"]["web/page.ts"] == ["web/helper.ts"]
    write(tmp_path, "pkg/entry.py", "from . import helper\n")
    indexed = CodebaseIndexer(tmp_path).refresh_index()
    assert "pkg/helper.py" in dependency_graph(indexed)["pkg/entry.py"]


def test_matching_tests_and_symbol_references_are_candidates(tmp_path):
    write(tmp_path, "auth.py", "class AuthService: pass\n")
    write(tmp_path, "tests/test_auth.py", "def test_login(): pass\n")
    write(tmp_path, "usage.py", "from auth import AuthService\nvalue = AuthService()\n")
    result = pipeline(tmp_path, "AuthService")
    rows = {row["file_path"]: row for row in result["candidates"]}
    assert any(factor["factor"] == "matching_test" for factor in rows["tests/test_auth.py"]["factors"])
    assert any(factor["factor"] == "symbol_reference" for factor in rows["usage.py"]["factors"])


def test_recent_edits_need_current_hash_and_existing_relevance(tmp_path):
    source = "class AuthService: pass\n"
    write(tmp_path, "auth.py", source)
    write(tmp_path, "other.py", "value = 1\n")
    result = pipeline(tmp_path, "AuthService", verified_edits=[{"path": "auth.py", "after_hash": hashlib.sha256(source.encode()).hexdigest()}, {"path": "other.py", "after_hash": "wrong"}])
    assert len(result["candidates"]) == 1
    assert any(factor["factor"] == "recent_verified_edit" for factor in result["candidates"][0]["factors"])


def test_large_index_and_source_reads_are_bounded_and_report_incompleteness(tmp_path):
    for number in range(50):
        write(tmp_path, f"source_{number:03}.py", "x = 1\n" * 100)
    indexer = CodebaseIndexer(tmp_path)
    indexed = indexer.refresh_index(max_files=12, max_bytes=1500)
    assert len(indexed) <= 2 and indexer.last_refresh["bytes_hashed"] <= 1500
    report = ContextRetrievalPipeline(indexer, indexed).run("source")["report"]
    assert report["warnings"] and indexer.last_refresh["limited"]


def test_incremental_service_discovers_new_sources_after_persisted_index(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    write(root, "old.py", "class Previous: pass\n")
    db = ProjectDatabase(tmp_path / "projects.db")
    db.create_project(Project(id="p", name="Proje", local_path=str(root)))
    service = ProjectCodingService(db)
    service.prepare_coding_prompt("p", "Previous")
    write(root, "new.py", "class NewDiscovery: pass\n")
    result = service.prepare_coding_prompt("p", "NewDiscovery")
    assert result["relevant_files"] == ["new.py"]
    assert result["index_status"]["updated"] == 1 and result["index_status"]["reused"] == 1
    assert result["context_pipeline"]["estimated_retrieved_tokens"] > 0
    (root / "old.py").unlink()
    service.prepare_coding_prompt("p", "NewDiscovery")
    assert "old.py" not in {item["file_path"] for item in db.get_all_code_indexes("p")}
    assert "old.py" not in {item["id"] for item in service.suggest_composer("p", "@")}


def test_session_selection_cannot_remove_durable_exclusions_and_explicit_ignore_can_be_pinned(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    write(root, ".gitignore", "ignored.py\n")
    write(root, "ignored.py", "class ExplicitlyPinned: pass\n")
    write(root, "blocked.py", "class HiddenContent: pass\n")
    db = ProjectDatabase(tmp_path / "projects.db")
    db.create_project(Project(id="p", name="Proje", local_path=str(root)))
    settings = db.get_settings("p")
    settings.coding_preferences["excluded_files"] = ["blocked.py"]
    db.save_settings(settings)
    result = ProjectCodingService(db).prepare_coding_prompt("p", "HiddenContent", excluded_paths=[],
        pinned_paths=["ignored.py", "blocked.py"])
    assert result["relevant_files"] == ["ignored.py"]
    assert "HiddenContent" not in result["project_context"]
    assert "ExplicitlyPinned" in result["project_context"]


def test_accepted_decisions_prioritize_query_match_while_preserving_pinned_memory(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    db = ProjectDatabase(tmp_path / "projects.db")
    db.create_project(Project(id="p", name="Proje", local_path=str(root)))
    for number in range(8):
        db.save_adr(ArchitectureDecision(id=f"adr{number}", project_id="p", adr_number=str(number),
            title="Unrelated colors", decision="Use blue"))
    db.save_adr(ArchitectureDecision(id="important", project_id="p", adr_number="9", title="AuthService", decision="Use integer keys"))
    db.save_memory(ProjectMemory(id="pinned", project_id="p", content="PINNED_MEMORY", status="pinned"))
    db.save_memory(ProjectMemory(id="approved", project_id="p", content="AuthService baseline", status="approved"))
    result = ProjectCodingService(db).prepare_coding_prompt("p", "AuthService")
    assert result["context_pipeline"]["decision_sources"][0]["id"] == "important"
    assert "AuthService" in result["project_context"] and "PINNED_MEMORY" in result["project_context"]


def test_stale_lsp_diagnostics_do_not_introduce_unrelated_candidates(tmp_path):
    write(tmp_path, "source.py", "class Selected: pass\n")
    write(tmp_path, "old_error.py", "class Unrelated: pass\n")
    result = pipeline(tmp_path, "Selected", diagnostics=[{"path": "old_error.py", "source": "lsp", "severity": 1, "file_hash": "stale"}])
    assert "old_error.py" not in {item["file_path"] for item in result["candidates"]}


def test_diff_retrieval_is_scoped_before_git_reads_excluded_or_unrelated_files(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    write(root, "auth.py", "class AuthService: pass\n")
    write(root, "private.py", "class AuthService: pass\n")
    write(root, "unrelated.py", "class ColorPalette: pass\n")
    db = ProjectDatabase(tmp_path / "projects.db")
    db.create_project(Project(id="p", name="Proje", local_path=str(root)))
    requests = []
    def get_diff(*args, **kwargs):
        requests.append(kwargs["file_paths"])
        return ""
    monkeypatch.setattr("firik_agent.projects.coding_service.GitService.get_diff", get_diff)
    ProjectCodingService(db).prepare_coding_prompt("p", "AuthService", excluded_paths=["private.py"])
    assert requests == [["auth.py"]]


def test_scoped_git_diff_uses_literal_workspace_paths_and_empty_means_none(tmp_path):
    def git(*args):
        return subprocess.run(["git", *args], cwd=tmp_path, check=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    git("init", "-q")
    write(tmp_path, "src/safe[1].py", "value = 1\n")
    write(tmp_path, "src/private.py", "value = 1\n")
    git("add", ".")
    git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "fixture")
    write(tmp_path, "src/safe[1].py", "value = 'SAFE_MARKER'\n")
    write(tmp_path, "src/private.py", "value = 'FORBIDDEN_MARKER'\n")
    diff = GitService.get_diff(tmp_path / "src", file_paths=["safe[1].py"])
    assert "SAFE_MARKER" in diff and "FORBIDDEN_MARKER" not in diff
    assert GitService.get_diff(tmp_path, file_paths=[]) == ""
    with pytest.raises(ValueError, match="inside"):
        GitService.get_diff(tmp_path, file_paths=["../external.py"])


@pytest.mark.parametrize("relative", [".netrc", "_netrc", ".npmrc", ".pypirc", ".git-credentials", ".pgpass",
    ".boto", ".s3cfg", ".dockercfg", "application_default_credentials.json",
    ".kube/config", ".docker/config.json", ".gnupg/private-keys-v1.d/credential"])
def test_hidden_credentials_are_rejected_before_any_index_or_candidate_read(tmp_path, monkeypatch, relative):
    protected = write(tmp_path, relative, "class HiddenCredentialMarker: pass\n")
    write(tmp_path, "safe.py", "class SafeSource: pass\n")
    from firik_agent.projects import indexer as module
    original = module.read_project_text
    def guarded(root, path, *args, **kwargs):
        assert Path(path).name != protected.name
        return original(root, path, *args, **kwargs)
    monkeypatch.setattr(module, "read_project_text", guarded)
    indexer = CodebaseIndexer(tmp_path)
    assert protected not in indexer.scan_files()
    assert indexer.index_file(protected) is None
    assert {item["file_path"] for item in indexer.refresh_index()} == {"safe.py"}
    with pytest.raises(ValueError, match="Hassas"):
        read_project_text(tmp_path, relative)
    result = ContextRetrievalPipeline(indexer, [{"file_path": relative, "symbols": ["HiddenCredentialMarker"]}]).run(
        "HiddenCredentialMarker", pinned=[relative])
    assert result["candidates"] == []


def test_resolved_sensitive_destination_stays_blocked_after_path_check_race(tmp_path, monkeypatch):
    innocent = write(tmp_path, "source.py", "value = 1\n")
    protected = write(tmp_path, ".aws/credentials", "SECRET_CONTENT\n")
    original = Path.resolve
    def swapped_resolution(path, *args, **kwargs):
        return protected if path == innocent else original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "resolve", swapped_resolution)
    with pytest.raises(ValueError, match="Hassas"):
        safe_project_path(tmp_path, "source.py")


def test_excluded_space_paths_and_rename_only_sections_leave_no_diff_context():
    diff = ('diff --git "a/private key.py" "b/private key.py"\n--- "a/private key.py"\n+++ "b/private key.py"\n+HIDDEN_CONTENT\n'
            'diff --git a/old.py b/private.py\nsimilarity index 100%\nrename from old.py\nrename to private.py\n'
            'diff --git a/public.py b/public.py\n--- a/public.py\n+++ b/public.py\n+SAFE\n')
    result = sanitize_git_diff(diff, excluded=lambda path: path in {"private key.py", "private.py"})
    assert "HIDDEN_CONTENT" not in result and "rename to private.py" not in result and "SAFE" in result
