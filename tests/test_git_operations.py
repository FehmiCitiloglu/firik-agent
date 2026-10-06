"""Real Git repositories exercise the workbench's previews and operation safety."""
from __future__ import annotations

import os
import shlex
import subprocess
import zipfile
from pathlib import Path

import pytest

from evren_agent.projects.git_operations import GitOperationsMixin


Git = GitOperationsMixin


def git(repo: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True,
                            check=check, timeout=15)
    return result.stdout.strip()


def commit(repo: Path, message: str) -> str:
    git(repo, "add", "--all")
    git(repo, "commit", "-m", message)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repository with spaces"
    root.mkdir()
    git(root, "init", "--initial-branch=main")
    git(root, "config", "user.name", "Alice Example")
    git(root, "config", "user.email", "alice@example.test")
    git(root, "config", "commit.gpgsign", "false")
    git(root, "config", "tag.gpgsign", "false")
    git(root, "config", "core.autocrlf", "false")
    (root / "code.py").write_text("value = 1\nprint(value)\n", encoding="utf-8", newline="\n")
    (root / "README.md").write_text("Hello\n", encoding="utf-8", newline="\n")
    (root / ".gitignore").write_text("*.cache\n", encoding="utf-8", newline="\n")
    commit(root, "Initial working version")
    (root / "code.py").write_text("value = 2\nprint(value)\n", encoding="utf-8", newline="\n")
    commit(root, "Improve value")
    return root


def run(repo: Path, name: str, params: dict | None = None, *, confirmed: bool = False) -> dict:
    return Git.execute_tool(repo, name, params, confirmed=confirmed)


def assert_success(result: dict) -> dict:
    assert result["ok"], result
    assert result["returncode"] == 0, result
    assert isinstance(result["conflicts"], list)
    return result


def test_catalogue_is_complete_and_isolated():
    catalogue = Git.get_tool_catalog()
    ids = [tool["id"] for tool in catalogue]
    assert len(ids) >= 50
    assert len(ids) == len(set(ids))
    for tool in catalogue:
        assert tool["title"] and tool["description"] and tool["category"]
        assert tool["mutates"] == (tool["risk"] != "read")
        for field in tool["fields"]:
            assert {"name", "label", "default", "required"} <= field.keys()
    catalogue[0]["title"] = "Changed"
    assert Git.get_tool_catalog()[0]["title"] != "Changed"


def test_previews_do_not_mutate_and_require_confirmation(repo: Path):
    before = git(repo, "status", "--porcelain")
    preview = Git.preview_tool(repo, "branch_create", {"branch": "feature/demo", "ref": "HEAD~1"})
    assert preview["ok"] and preview["requires_confirmation"]
    assert preview["args"][-1] == git(repo, "rev-parse", "HEAD~1")
    assert "repository with spaces'" in preview["command"]
    assert "feature/demo" not in git(repo, "branch", "--format=%(refname:short)")
    result = run(repo, "branch_create", {"branch": "feature/demo"})
    assert not result["ok"] and result["requires_confirmation"]
    assert git(repo, "status", "--porcelain") == before
    # Truthy values are not explicit approval.
    assert not run(repo, "branch_create", {"branch": "feature/demo"}, confirmed="yes")["ok"]


def test_stage_unstage_and_commit_only_selected_content(repo: Path):
    (repo / "code.py").write_text("value = 3\nprint(value)\n", encoding="utf-8", newline="\n")
    (repo / "README.md").write_text("Local change remains\n", encoding="utf-8", newline="\n")
    assert_success(run(repo, "stage", {"paths": ["code.py"]}, confirmed=True))
    assert git(repo, "diff", "--cached", "--name-only") == "code.py"
    assert_success(run(repo, "unstage", {"paths": "code.py"}, confirmed=True))
    assert git(repo, "diff", "--cached", "--name-only") == ""
    assert "value = 3" in (repo / "code.py").read_text(encoding="utf-8")
    empty = run(repo, "commit", {"message": "Empty is rejected"}, confirmed=True)
    assert not empty["ok"] and "hazırlanan" in empty["stderr"]
    assert_success(run(repo, "stage", {"paths": "code.py"}, confirmed=True))
    assert_success(run(repo, "commit", {"message": "One selected change\n\nExplanation"}, confirmed=True))
    assert git(repo, "show", "HEAD:README.md") == "Hello"
    assert "Local change remains" in (repo / "README.md").read_text(encoding="utf-8")
    assert git(repo, "log", "-1", "--format=%s") == "One selected change"


def test_unstage_in_a_repository_without_first_commit(tmp_path: Path):
    git(tmp_path, "init", "--initial-branch=main")
    (tmp_path / "new.txt").write_text("content\n", encoding="utf-8", newline="\n")
    assert_success(run(tmp_path, "stage", {"paths": ["new.txt"]}, confirmed=True))
    assert_success(run(tmp_path, "unstage", {"paths": ["new.txt"]}, confirmed=True))
    assert (tmp_path / "new.txt").read_text(encoding="utf-8") == "content\n"
    assert git(tmp_path, "ls-files") == ""


@pytest.mark.parametrize("tool,params", [
    ("stage", {"paths": ["../outside.txt"]}),
    ("stage", {"paths": [".git/config"]}),
    ("stage", {"paths": ["link/outside.txt"]}),
    ("restore_file", {"path": "."}),
    ("branch_create", {"branch": "--track"}),
    ("branch_create", {"branch": "@{-1}"}),
    ("tag_create", {"tag": "--force", "message": "Unsafe"}),
    ("commit_show", {"ref": "--output=/tmp/evil"}),
    ("fetch", {"remote": "https://arbitrary.example.test/repo"}),
    ("stash_show", {"stash": "--patch"}),
    ("range_diff", {"old_range": "--help", "new_range": "HEAD~1..HEAD"}),
    ("archive_zip", {"output": "../outside.zip"}),
    ("commit", {"message": "\x00bad"}),
    ("diff", {"unknown": "--output=/tmp/evil"}),
    ("line_history", {"path": "code.py", "start": 10, "end": 1}),
])
def test_unsafe_or_invalid_input_is_rejected(repo: Path, tmp_path: Path, tool: str, params: dict):
    if params.get("paths") == ["link/outside.txt"]:
        try:
            (repo / "link").symlink_to(tmp_path, target_is_directory=True)
        except OSError as error:
            pytest.skip(f"Filesystem symlink creation unavailable: {error}")
    preview = Git.preview_tool(repo, tool, params)
    assert not preview["ok"], preview
    assert not run(repo, tool, params, confirmed=True)["ok"]


def test_literal_pathspecs_and_shell_metacharacters_are_just_file_names(repo: Path):
    filename = "[draft];$(touch PWNED).txt" if os.name == "nt" else ":(glob)*;$(touch PWNED).txt"
    (repo / filename).write_text("literal filename\n", encoding="utf-8", newline="\n")
    (repo / "another.txt").write_text("untouched\n", encoding="utf-8", newline="\n")
    assert_success(run(repo, "stage", {"paths": [filename]}, confirmed=True))
    tracked = git(repo, "diff", "--cached", "--name-only", "-z")
    assert tracked == filename + "\x00"
    assert not (repo / "PWNED").exists()
    assert "another.txt" not in tracked


def test_git_metadata_is_protected_when_dot_git_is_a_symlink(repo: Path):
    metadata = repo / "hidden-metadata"
    (repo / ".git").rename(metadata)
    try:
        (repo / ".git").symlink_to(metadata, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"Filesystem symlink creation unavailable: {error}")
    assert git(repo, "rev-parse", "--show-toplevel")
    result = run(repo, "stage", {"paths": ["hidden-metadata/config"]}, confirmed=True)
    assert not result["ok"] and "yönetim dosyaları" in result["stderr"]


@pytest.mark.parametrize("tool,params,expected", [
    ("status", {}, "main"),
    ("health", {}, "count:"),
    ("fsck", {}, ""),
    ("tracked_files", {}, "code.py"),
    ("commit_show", {}, "Improve value"),
    ("log_search", {"text": "INITIAL"}, "Initial working version"),
    ("pickaxe", {"text": "value = 2", "path": "code.py"}, "Improve value"),
    ("regex_history", {"pattern": "value = [12]", "path": "code.py"}, "Initial working version"),
    ("line_history", {"path": "code.py", "start": 1, "end": 1}, "value = 2"),
    ("file_history", {"path": "code.py"}, "Alice Example"),
    ("blame_moved", {"path": "code.py", "start": 1, "end": 2}, "Alice Example"),
    ("branch_compare", {"base": "HEAD~1", "target": "HEAD"}, "code.py"),
    ("range_diff", {"old_range": "HEAD~1..HEAD", "new_range": "HEAD~1..HEAD"}, "Improve value"),
    ("merge_base", {"base": "HEAD~1", "target": "HEAD"}, ""),
    ("merge_preview", {"base": "HEAD~1", "target": "HEAD"}, "value = 2"),
    ("cherry", {"upstream": "HEAD~1", "ref": "HEAD"}, "Improve value"),
    ("reflog", {}, "commit"),
    ("branch_merged", {}, "main"),
    ("describe", {}, ""),
    ("worktree_list", {}, "refs/heads/main"),
    ("conflict_stages", {}, ""),
    ("attributes", {"path": "code.py"}, ""),
    ("remotes", {}, ""),
    ("submodule_status", {}, ""),
    ("stash_list", {}, ""),
    ("rerere_status", {}, ""),
    ("rerere_remaining", {}, ""),
    ("rerere_diff", {}, ""),
    ("sparse_checkout_status", {}, "sparse checkout"),
])
def test_read_tools_execute_real_git(repo: Path, tool: str, params: dict, expected: str):
    preview = Git.preview_tool(repo, tool, params)
    assert preview["ok"] and not preview["requires_confirmation"], preview
    result = assert_success(run(repo, tool, params))
    assert expected in result["stdout"]


def test_file_history_follows_renames(repo: Path):
    git(repo, "mv", "code.py", "renamed.py")
    commit(repo, "Rename the code")
    result = assert_success(run(repo, "file_history", {"path": "renamed.py"}))
    assert "Initial working version" in result["stdout"]
    assert "Rename the code" in result["stdout"]


def test_blame_does_not_execute_repository_textconv_helper(repo: Path, tmp_path: Path):
    marker = tmp_path / "textconv-ran"
    helper = tmp_path / "textconv-helper"
    helper.write_text(f"#!/bin/sh\nprintf invoked > {shlex.quote(str(marker))}\ncat \"$1\"\n", encoding="utf-8", newline="\n")
    helper.chmod(0o700)
    (repo / ".gitattributes").write_text("*.py diff=special\n", encoding="utf-8", newline="\n")
    commit(repo, "Configure a file attribute")
    git(repo, "config", "diff.special.textconv", shlex.quote(str(helper)))
    result = assert_success(run(repo, "blame_moved", {"path": "code.py"}))
    assert "value = 2" in result["stdout"]
    assert not marker.exists()


def test_diff_word_movement_whitespace_and_ignore(repo: Path):
    (repo / "code.py").write_text("value = 3  \nprint(value)\n", encoding="utf-8", newline="\n")
    assert "value = 3" in assert_success(run(repo, "diff", {"path": "code.py"}))["stdout"]
    assert "{+3+}" in assert_success(run(repo, "word_diff", {"path": "code.py"}))["stdout"]
    assert "\x1b[" in assert_success(run(repo, "moved_diff", {"path": "code.py"}))["stdout"]
    result = run(repo, "diff_check")
    assert not result["ok"] and "trailing whitespace" in result["stdout"]
    assert "*.cache" in assert_success(run(repo, "check_ignore", {"path": "build.cache"}))["stdout"]
    no_match = run(repo, "check_ignore", {"path": "code.py"})
    assert no_match["ok"] and no_match["returncode"] == 1 and "eşleşmiyor" in no_match["stdout"]
    (repo / "temporary.txt").write_text("keep this\n", encoding="utf-8", newline="\n")
    preview = assert_success(run(repo, "clean_preview"))
    assert "temporary.txt" in preview["stdout"]
    assert (repo / "temporary.txt").exists()


def test_branch_tag_rescue_and_worktree(repo: Path):
    previous = git(repo, "rev-parse", "HEAD~1")
    assert_success(run(repo, "branch_create", {"branch": "feature/demo", "ref": "HEAD~1"}, confirmed=True))
    assert git(repo, "symbolic-ref", "--short", "HEAD") == "main"
    assert_success(run(repo, "branch_switch", {"branch": "feature/demo"}, confirmed=True))
    assert git(repo, "rev-parse", "HEAD") == previous
    assert_success(run(repo, "branch_switch", {"branch": "main"}, confirmed=True))
    assert_success(run(repo, "tag_create", {"tag": "v1.0", "message": "Release one"}, confirmed=True))
    assert "v1.0" in assert_success(run(repo, "describe"))["stdout"]
    assert_success(run(repo, "rescue_branch", {"branch": "rescued", "ref": "HEAD@{1}"}, confirmed=True))
    assert git(repo, "rev-parse", "rescued") == previous
    assert_success(run(repo, "worktree_add", {"directory": "parallel tree", "branch": "parallel"}, confirmed=True))
    assert (repo / "parallel tree" / ".git").is_file()
    assert git(repo, "symbolic-ref", "--short", "HEAD") == "main"
    assert "parallel" in assert_success(run(repo, "worktree_list"))["stdout"]


def test_stash_save_show_apply_and_pop(repo: Path):
    (repo / "code.py").write_text("value = 99\nprint(value)\n", encoding="utf-8", newline="\n")
    (repo / "new.txt").write_text("new saved file\n", encoding="utf-8", newline="\n")
    assert_success(run(repo, "stash_save", {"message": "Named work"}, confirmed=True))
    assert git(repo, "status", "--porcelain") == ""
    assert "Named work" in assert_success(run(repo, "stash_list"))["stdout"]
    shown = assert_success(run(repo, "stash_show"))["stdout"]
    assert "value = 99" in shown and "new saved file" in shown
    assert_success(run(repo, "stash_apply", confirmed=True))
    assert git(repo, "stash", "list")
    assert (repo / "new.txt").exists()
    assert not run(repo, "stash_pop", confirmed=True)["ok"]
    assert_success(run(repo, "stage", {"paths": ["code.py", "new.txt"]}, confirmed=True))
    assert_success(run(repo, "commit", {"message": "Save restored work"}, confirmed=True))
    # Drop the first stash through a successful pop on its original baseline.
    assert_success(run(repo, "branch_create", {"branch": "stash-baseline", "ref": "HEAD~1"}, confirmed=True))
    assert_success(run(repo, "branch_switch", {"branch": "stash-baseline"}, confirmed=True))
    assert_success(run(repo, "stash_pop", confirmed=True))
    assert git(repo, "stash", "list") == ""


def test_confirmed_stash_preview_rejects_shifted_reflog_selector(repo: Path):
    (repo / "code.py").write_text("value = 10\n", encoding="utf-8", newline="\n")
    assert_success(run(repo, "stash_save", {"message": "First stash"}, confirmed=True))
    preview = Git.preview_tool(repo, "stash_pop")
    assert preview["ok"] and preview["targets"]["stash"]
    (repo / "code.py").write_text("value = 20\n", encoding="utf-8", newline="\n")
    assert_success(run(repo, "stash_save", {"message": "Newer stash"}, confirmed=True))
    result = Git.execute_tool(repo, "stash_pop", confirmed=True,
                              expected_command=preview["command"], expected_targets=preview["targets"])
    assert not result["ok"] and "hedef değişti" in result["stderr"]
    assert len(git(repo, "stash", "list").splitlines()) == 2
    assert git(repo, "status", "--porcelain") == ""


def test_restore_is_explicit_and_preserves_index(repo: Path):
    (repo / "code.py").write_text("value = 10\n", encoding="utf-8", newline="\n")
    git(repo, "add", "code.py")
    (repo / "code.py").write_text("value = 20\n", encoding="utf-8", newline="\n")
    refused = run(repo, "restore_file", {"path": "code.py"})
    assert not refused["ok"] and (repo / "code.py").read_text(encoding="utf-8") == "value = 20\n"
    assert_success(run(repo, "restore_file", {"path": "code.py"}, confirmed=True))
    assert (repo / "code.py").read_text(encoding="utf-8") == "value = 2\nprint(value)\n"
    assert git(repo, "show", ":code.py") == "value = 10"


def test_confirmed_preview_is_rejected_if_head_changes(repo: Path):
    preview = Git.preview_tool(repo, "restore_file", {"path": "code.py"})
    assert preview["ok"]
    (repo / "code.py").write_text("value = 50\n", encoding="utf-8", newline="\n")
    commit(repo, "Change target after preview")
    (repo / "code.py").write_text("unsaved content must remain\n", encoding="utf-8", newline="\n")
    result = Git.execute_tool(repo, "restore_file", {"path": "code.py"}, confirmed=True,
                              expected_command=preview["command"])
    assert not result["ok"] and "komut değişti" in result["stderr"]
    assert (repo / "code.py").read_text(encoding="utf-8") == "unsaved content must remain\n"


def test_revert_clean_requirement_and_success(repo: Path):
    tip = git(repo, "rev-parse", "HEAD")
    (repo / "README.md").write_text("unsaved\n", encoding="utf-8", newline="\n")
    refused = run(repo, "revert", {"ref": tip}, confirmed=True)
    assert not refused["ok"] and "temiz değil" in refused["stderr"]
    assert git(repo, "rev-parse", "HEAD") == tip
    assert_success(run(repo, "restore_file", {"path": "README.md"}, confirmed=True))
    assert_success(run(repo, "revert", {"ref": tip}, confirmed=True))
    assert "value = 1" in (repo / "code.py").read_text(encoding="utf-8")
    assert git(repo, "rev-list", "--count", "HEAD") == "3"


def test_cherry_pick_conflicts_are_reported_and_abort_restores_start(repo: Path):
    git(repo, "switch", "-c", "other", "HEAD~1")
    (repo / "code.py").write_text("value = 55\nprint(value)\n", encoding="utf-8", newline="\n")
    conflicting = commit(repo, "Other conflicting value")
    git(repo, "switch", "main")
    original = git(repo, "rev-parse", "HEAD")
    result = run(repo, "cherry_pick", {"ref": conflicting}, confirmed=True)
    assert not result["ok"] and result["conflicts"] == ["code.py"]
    assert "CONFLICT" in result["stdout"]
    stages = assert_success(run(repo, "conflict_stages"))["stdout"]
    assert stages.count("code.py") == 3
    refused = run(repo, "cherry_pick", {"ref": conflicting}, confirmed=True)
    assert not refused["ok"] and refused["conflicts"] == ["code.py"]
    assert_success(run(repo, "cherry_pick_abort", confirmed=True))
    assert git(repo, "rev-parse", "HEAD") == original
    assert git(repo, "status", "--porcelain") == ""


def test_revert_conflict_can_be_aborted(repo: Path):
    git(repo, "switch", "-c", "other", "HEAD~1")
    (repo / "code.py").write_text("value = 55\nprint(value)\n", encoding="utf-8", newline="\n")
    conflicting = commit(repo, "Other conflicting value")
    git(repo, "switch", "main")
    result = run(repo, "revert", {"ref": conflicting}, confirmed=True)
    assert not result["ok"] and result["conflicts"] == ["code.py"]
    assert_success(run(repo, "revert_abort", confirmed=True))
    assert git(repo, "status", "--porcelain") == ""


def test_cherry_pick_success(repo: Path):
    git(repo, "switch", "-c", "source")
    (repo / "feature.txt").write_text("feature\n", encoding="utf-8", newline="\n")
    source = commit(repo, "Useful feature")
    git(repo, "switch", "main")
    assert_success(run(repo, "cherry_pick", {"ref": source}, confirmed=True))
    assert (repo / "feature.txt").read_text(encoding="utf-8") == "feature\n"


def test_merge_conflict_continue_and_abort(repo: Path):
    git(repo, "switch", "-c", "other", "HEAD~1")
    (repo / "code.py").write_text("value = 55\nprint(value)\n", encoding="utf-8", newline="\n")
    commit(repo, "Other conflicting value")
    git(repo, "switch", "main")
    tip = git(repo, "rev-parse", "HEAD")
    result = run(repo, "merge", {"ref": "other"}, confirmed=True)
    assert not result["ok"] and result["conflicts"] == ["code.py"]
    refused = run(repo, "merge_continue", confirmed=True)
    assert not refused["ok"] and refused["conflicts"] == ["code.py"]
    assert_success(run(repo, "merge_abort", confirmed=True))
    assert git(repo, "rev-parse", "HEAD") == tip
    result = run(repo, "merge", {"ref": "other"}, confirmed=True)
    assert not result["ok"] and result["conflicts"] == ["code.py"]
    (repo / "code.py").write_text("value = 100\nprint(value)\n", encoding="utf-8", newline="\n")
    assert_success(run(repo, "stage", {"paths": ["code.py"]}, confirmed=True))
    assert_success(run(repo, "merge_continue", confirmed=True))
    assert len(git(repo, "rev-list", "--parents", "-n", "1", "HEAD").split()) == 3
    assert git(repo, "status", "--porcelain") == ""


def test_rebase_conflict_abort_skip_and_continue(repo: Path):
    git(repo, "switch", "-c", "topic", "HEAD~1")
    (repo / "code.py").write_text("value = 55\nprint(value)\n", encoding="utf-8", newline="\n")
    topic_tip = commit(repo, "Topic conflicting value")
    result = run(repo, "rebase", {"ref": "main"}, confirmed=True)
    assert not result["ok"] and result["conflicts"] == ["code.py"]
    assert_success(run(repo, "rebase_abort", confirmed=True))
    assert git(repo, "rev-parse", "HEAD") == topic_tip
    assert git(repo, "symbolic-ref", "--short", "HEAD") == "topic"
    result = run(repo, "rebase", {"ref": "main"}, confirmed=True)
    assert not result["ok"] and result["conflicts"] == ["code.py"]
    (repo / "code.py").write_text("value = 100\nprint(value)\n", encoding="utf-8", newline="\n")
    assert_success(run(repo, "stage", {"paths": ["code.py"]}, confirmed=True))
    assert_success(run(repo, "rebase_continue", confirmed=True))
    assert git(repo, "symbolic-ref", "--short", "HEAD") == "topic"
    assert git(repo, "rev-parse", "HEAD") != topic_tip
    git(repo, "switch", "-c", "skipped", topic_tip)
    result = run(repo, "rebase", {"ref": "main"}, confirmed=True)
    assert not result["ok"] and result["conflicts"] == ["code.py"]
    assert_success(run(repo, "rebase_skip", confirmed=True))
    assert git(repo, "rev-parse", "HEAD") == git(repo, "rev-parse", "main")


@pytest.mark.parametrize("operation", ["cherry_pick", "revert"])
def test_commit_operations_can_continue_after_resolving_conflicts(repo: Path, operation: str):
    git(repo, "switch", "-c", "other", "HEAD~1")
    (repo / "code.py").write_text("value = 55\nprint(value)\n", encoding="utf-8", newline="\n")
    conflicting = commit(repo, "Other conflicting value")
    git(repo, "switch", "main")
    result = run(repo, operation, {"ref": conflicting}, confirmed=True)
    assert not result["ok"] and result["conflicts"] == ["code.py"]
    (repo / "code.py").write_text("value = 100\nprint(value)\n", encoding="utf-8", newline="\n")
    assert_success(run(repo, "stage", {"paths": ["code.py"]}, confirmed=True))
    assert_success(run(repo, operation + "_continue", confirmed=True))
    assert git(repo, "status", "--porcelain") == ""


def test_branch_delete_refuses_unmerged_and_current_branches(repo: Path):
    assert_success(run(repo, "branch_create", {"branch": "merged"}, confirmed=True))
    assert_success(run(repo, "branch_delete", {"branch": "merged"}, confirmed=True))
    assert "merged" not in git(repo, "branch", "--format=%(refname:short)").splitlines()
    assert not run(repo, "branch_delete", {"branch": "main"}, confirmed=True)["ok"]
    git(repo, "switch", "-c", "unmerged")
    (repo / "new-topic.txt").write_text("topic\n", encoding="utf-8", newline="\n")
    commit(repo, "Unmerged work")
    git(repo, "switch", "main")
    result = run(repo, "branch_delete", {"branch": "unmerged"}, confirmed=True)
    assert not result["ok"] and "not fully merged" in result["stderr"]
    assert "unmerged" in git(repo, "branch", "--format=%(refname:short)").splitlines()


def test_bisect_search_and_reset(repo: Path):
    good = git(repo, "rev-parse", "HEAD~1")
    for number in range(3, 6):
        (repo / "code.py").write_text(f"value = {number}\nprint(value)\n", encoding="utf-8", newline="\n")
        commit(repo, f"Value {number}")
    bad = git(repo, "rev-parse", "HEAD")
    assert_success(run(repo, "bisect_start", {"good": good, "bad": bad}, confirmed=True))
    assert "git bisect" in assert_success(run(repo, "bisect_status"))["stdout"]
    assert_success(run(repo, "bisect_bad", confirmed=True))
    assert_success(run(repo, "bisect_good", confirmed=True))
    assert_success(run(repo, "bisect_reset", confirmed=True))
    assert git(repo, "symbolic-ref", "--short", "HEAD") == "main"
    assert git(repo, "rev-parse", "HEAD") == bad


def test_bundle_and_zip_exports_are_real_and_do_not_overwrite(repo: Path):
    assert_success(run(repo, "bundle_create", {"output": "copy.bundle"}, confirmed=True))
    git(repo, "bundle", "verify", "copy.bundle")
    result = run(repo, "bundle_create", {"output": "copy.bundle"}, confirmed=True)
    assert not result["ok"] and "üzerine yazılmaz" in result["stderr"]
    assert_success(run(repo, "archive_zip", {"output": "sources.zip"}, confirmed=True))
    committed_source = subprocess.run(["git", "show", "HEAD:code.py"], cwd=repo,
                                      check=True, capture_output=True).stdout
    with zipfile.ZipFile(repo / "sources.zip") as archive:
        assert "code.py" in archive.namelist()
        assert ".git/config" not in archive.namelist()
        assert archive.read("code.py") == committed_source


def test_remote_operations_use_local_remote_and_fast_forward_only(repo: Path, tmp_path: Path):
    remote = tmp_path / "remote.git"
    remote.mkdir()
    git(remote, "init", "--bare", "--initial-branch=main")
    git(repo, "remote", "add", "origin", str(remote))
    assert_success(run(repo, "push", {"branch": "main"}, confirmed=True))
    assert_success(run(repo, "fetch", confirmed=True))
    assert "origin" in assert_success(run(repo, "remotes"))["stdout"]
    peer = tmp_path / "peer"
    subprocess.run(["git", "clone", str(remote), str(peer)], check=True, capture_output=True, timeout=15)
    git(peer, "config", "user.name", "Peer")
    git(peer, "config", "user.email", "peer@example.test")
    (peer / "remote.txt").write_text("remote content\n", encoding="utf-8", newline="\n")
    commit(peer, "Remote update")
    git(peer, "push", "origin", "main")
    assert_success(run(repo, "pull_ff", {"branch": "main"}, confirmed=True))
    assert (repo / "remote.txt").read_text(encoding="utf-8") == "remote content\n"
    (repo / "local.txt").write_text("local content\n", encoding="utf-8", newline="\n")
    local_tip = commit(repo, "Local divergence")
    (peer / "another.txt").write_text("other content\n", encoding="utf-8", newline="\n")
    commit(peer, "Remote divergence")
    git(peer, "push", "origin", "main")
    refused = run(repo, "pull_ff", {"branch": "main"}, confirmed=True)
    assert not refused["ok"] and "fast-forward" in refused["stderr"].lower()
    assert git(repo, "rev-parse", "HEAD") == local_tip


def test_run_ignores_environment_redirects_and_bounds_output(repo: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GIT_DIR", "/definitely/not/our/repository")
    assert_success(run(repo, "status"))
    monkeypatch.setattr(Git, "_OPS_OUTPUT_LIMIT", 40)
    output = run(repo, "commit_show")
    assert output["ok"] and output["truncated"]
    assert len(output["stdout"].encode()) <= 40


def test_remote_credentials_are_redacted_without_network_access(repo: Path):
    token = "ghp_1234567890abcdefghijklmnopqrstuv"
    git(repo, "remote", "add", "origin", f"https://alice:{token}@example.test/repository.git")
    output = assert_success(run(repo, "remotes"))["stdout"]
    assert token not in output and "alice:" not in output
    assert "https://***@example.test/repository.git" in output
    (repo / "README.md").write_text(f"Token in commit message fixture {token}\n", encoding="utf-8", newline="\n")
    commit(repo, f"Token leak fixture {token}")
    shown = assert_success(run(repo, "commit_show"))["stdout"]
    assert token not in shown and "[REDACTED_TOKEN]" in shown


def test_bad_repositories_and_unknown_tools_return_structured_errors(tmp_path: Path):
    invalid = run(tmp_path, "status")
    assert not invalid["ok"] and invalid["stderr"]
    unknown = Git.preview_tool(tmp_path, "reset_hard")
    assert not unknown["ok"] and "Bilinmeyen" in unknown["error"]
