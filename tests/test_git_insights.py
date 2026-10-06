"""Git intelligence integration checks against real temporary repositories."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import subprocess

import pytest

from firik_agent.projects.git_service import GitService


def git(root: Path, *args: str, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True,
        env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1", **(env or {})}, check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def commit(root: Path, message: str, author: str, day: int = 0) -> str:
    stamp = (datetime.now(timezone.utc) - timedelta(days=day)).replace(hour=10, minute=0, second=0).isoformat()
    git(root, "add", "--all")
    git(root, "commit", "-m", message, env={
        "GIT_AUTHOR_NAME": author, "GIT_AUTHOR_EMAIL": f"{author.lower()}@example.test",
        "GIT_COMMITTER_NAME": author, "GIT_COMMITTER_EMAIL": f"{author.lower()}@example.test",
        "GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp,
    })
    return git(root, "rev-parse", "HEAD").strip()


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo with spaces"
    root.mkdir()
    git(root, "init", "-b", "main")
    git(root, "config", "user.name", "Test Author")
    git(root, "config", "user.email", "test@example.test")
    (root / "çay menü.txt").write_text("first line\nsecond line\n", encoding="utf-8")
    (root / "keep.txt").write_text("keep\n", encoding="utf-8")
    first = commit(root, "initial tea", "Alice", day=2)
    (root / "çay menü.txt").write_text("first line\nseasoned line\n", encoding="utf-8")
    (root / "image.bin").write_bytes(b"\x00\xff\x01")
    second = commit(root, "adjust [ready]", "Bob", day=1)
    git(root, "mv", "çay menü.txt", "renamed çay.txt")
    third = commit(root, "rename tea", "Alice")
    return root, (first, second, third)


def test_dashboard_uses_real_numstat_author_activity_and_renames(repo):
    root, hashes = repo
    result = GitService.get_dashboard(root, days=30)
    assert result["repository"]["root"] == str(root)
    assert result["repository"]["branch"] == "main"
    assert result["status"]["clean"] is True
    assert result["summary"] == {
        "contributors": 2, "commits": 3, "added": 4, "removed": 1,
        "files": 4, "days": 30, "merges": 0, "binary_changes": 1,
    }
    assert [(row["author"], row["commits"]) for row in result["contributors"]] == [("Alice", 2), ("Bob", 1)]
    assert sum(row["commits"] for row in result["activity"]) == 3
    assert len(result["activity"]) == 30
    assert {row["path"] for row in result["hotspots"]} == {"çay menü.txt", "renamed çay.txt", "image.bin", "keep.txt"}
    assert result["hotspots"][0]["path"] == "çay menü.txt"
    assert result["hotspots"][0]["last_author"] == "Bob"
    assert result["truncated"] is False
    assert result["scope"]["binary_changes"] == 1
    assert result["scope"]["shallow"] is False
    assert GitService.get_dashboard(root, days=30, limit=1)["truncated"] is True
    # A bad ref is a visible error, never a convincing empty chart.
    with pytest.raises(ValueError):
        GitService.get_dashboard(root, ref="missing-branch")


def test_history_filters_and_file_rename_following(repo):
    root, (first, second, third) = repo
    history = GitService.get_history(root)
    assert [row["hash"] for row in history] == [third, second, first]
    assert history[0]["parents"] == [second]
    assert history[0]["refs"]
    assert [row["hash"] for row in GitService.get_history(root, author="Bob")] == [second]
    assert [row["hash"] for row in GitService.get_history(root, query="[ready]")] == [second]
    assert [row["hash"] for row in GitService.get_history(root, pickaxe="seasoned line")] == [second]
    assert [row["hash"] for row in GitService.get_file_history(root, "renamed çay.txt")] == [third, second, first]
    assert [row["hash"] for row in GitService.get_history(root, file_path="renamed çay.txt")] == [third]
    assert len(GitService.get_history(root, limit=1)) == 1
    assert "rename tea" in GitService.get_graph(root)


def test_file_history_respects_selected_branch_and_follows_its_renames(repo):
    root, (first, second, third) = repo
    git(root, "checkout", "-b", "feature")
    (root / "renamed çay.txt").write_text("first line\nbranch seasoning\n", encoding="utf-8")
    fourth = commit(root, "feature seasoning", "Bob")
    git(root, "mv", "renamed çay.txt", "branch çay.txt")
    fifth = commit(root, "feature rename", "Bob")
    git(root, "checkout", "main")
    assert [row["hash"] for row in GitService.get_file_history(root, "renamed çay.txt")] == [third, second, first]
    assert GitService.get_file_history(root, "branch çay.txt") == []
    feature = GitService.get_file_history(root, "branch çay.txt", ref="feature")
    assert [row["hash"] for row in feature] == [fifth, fourth, third, second, first]
    assert feature[0]["author"] == "Bob"
    assert [row["hash"] for row in GitService.get_file_history(root, "branch çay.txt", 2, ref="feature")] == [fifth, fourth]
    with pytest.raises(ValueError):
        GitService.get_file_history(root, "branch çay.txt", ref="missing")
    with pytest.raises(ValueError):
        GitService.get_file_history(root, "branch çay.txt", ref="--all")


def test_blame_follows_rename_and_marks_local_lines(repo):
    root, (first, second, third) = repo
    rows = GitService.get_blame(root, "renamed çay.txt")
    assert [(row["line"], row["author"], row["text"]) for row in rows] == [
        (1, "Alice", "first line"), (2, "Bob", "seasoned line"),
    ]
    assert rows[0]["hash"] == first
    assert rows[1]["hash"] == second
    assert all(row["date"] and not row["uncommitted"] for row in rows)
    (root / "renamed çay.txt").write_text("local change\nseasoned line\n", encoding="utf-8")
    local = GitService.get_blame(root, "renamed çay.txt", start=1, end=1)
    assert len(local) == 1 and local[0]["uncommitted"]
    committed = GitService.get_blame(root, "renamed çay.txt", start=1, end=1, ref=third)
    assert committed[0]["hash"] == first
    with pytest.raises(ValueError, match="500"):
        GitService.get_blame(root, "renamed çay.txt", start=1, end=501)
    with pytest.raises(RuntimeError):
        GitService.get_blame(root, "renamed çay.txt", start=50)


def test_commit_details_rename_root_and_bounded_patches(repo, monkeypatch):
    root, (first, second, third) = repo
    detail = GitService.get_commit_detail(root, third)
    assert detail["author"] == "Alice" and detail["message"] == "rename tea"
    assert detail["files"] == [{"status": "R100", "path": "renamed çay.txt", "old_path": "çay menü.txt"}]
    assert "rename from" in detail["diff"] and "rename to" in detail["diff"]
    assert detail["diff_base"] == second
    first_detail = GitService.get_commit_detail(root, first)
    assert {row["path"] for row in first_detail["files"]} == {"çay menü.txt", "keep.txt"}
    assert "first line" in first_detail["diff"]
    monkeypatch.setattr(GitService, "_INSIGHTS_PATCH_CHARS", 30)
    clipped = GitService.get_commit_detail(root, second)
    assert clipped["diff_truncated"] is True
    assert "truncated" in clipped["diff"]


def test_branches_compare_tags_reflog_stashes_and_linked_worktree(repo, tmp_path):
    root, (first, second, third) = repo
    git(root, "tag", "v1")
    git(root, "checkout", "-b", "feature")
    (root / "feature file.txt").write_text("feature\n", encoding="utf-8")
    fourth = commit(root, "feature addition", "Bob")
    comparison = GitService.compare_refs(root, "main", "feature")
    assert comparison["merge_base"] == third
    assert (comparison["ahead"], comparison["behind"]) == (1, 0)
    assert [row["hash"] for row in comparison["commits"]] == [fourth]
    assert comparison["files"] == [{"status": "A", "path": "feature file.txt", "old_path": ""}]
    assert "feature file.txt" in comparison["stat"]
    branches = GitService.get_branch_details(root)
    assert {row["name"] for row in branches} == {"main", "feature"}
    assert next(row for row in branches if row["current"])["name"] == "feature"
    assert GitService.get_tags(root)[0]["name"] == "v1"
    assert GitService.get_reflog(root)[0]["hash"] == fourth
    (root / "feature file.txt").write_text("local change\n", encoding="utf-8")
    git(root, "stash", "push", "-m", "recover this")
    stashes = GitService.get_stashes(root)
    assert stashes[0]["ref"] == "stash@{0}" and "recover this" in stashes[0]["message"]
    linked = tmp_path / "linked tree çay"
    git(root, "worktree", "add", str(linked), "main")
    trees = GitService.get_worktrees(root)
    assert {row["path"] for row in trees} == {str(root), str(linked)}
    assert GitService.get_repository_info(linked)["branch"] == "main"
    assert GitService.get_history(linked)[0]["hash"] == third


def test_empty_repo_is_valid_with_empty_queries(tmp_path):
    root = tmp_path / "empty"
    root.mkdir()
    git(root, "init", "-b", "main")
    assert GitService.get_repository_info(root)["unborn"] is True
    assert GitService.get_history(root) == []
    assert GitService.get_graph(root) == ""
    assert GitService.get_reflog(root) == []
    assert GitService.get_tags(root) == []
    assert GitService.get_stashes(root) == []
    assert GitService.list_tracked_files(root) == []
    dashboard = GitService.get_dashboard(root, days=7)
    assert dashboard["summary"]["commits"] == 0
    assert dashboard["status"]["clean"] is True
    assert len(dashboard["activity"]) == 7
    assert GitService.get_file_history(root, "future.txt") == []
    assert GitService.get_blame(root, "future.txt") == []


def test_paths_are_literal_contained_and_machine_formats_preserve_unusual_names(repo):
    root, _ = repo
    names = ["[draft].txt", "-leading.txt", "çorba tarifi.txt"]
    if os.name != "nt":
        names.extend([":(glob)magic.txt", "two\tcolumns.txt", "line\nbreak.txt"])
    for filename in names:
        (root / filename).write_text(f"content of {filename}\n", encoding="utf-8")
    latest = commit(root, "literal names", "Bob")
    assert set(names).issubset(GitService.list_tracked_files(root))
    detail = GitService.get_commit_detail(root, latest)
    assert set(names) == {row["path"] for row in detail["files"]}
    dashboard = GitService.get_dashboard(root)
    assert set(names).issubset({row["path"] for row in dashboard["hotspots"]})
    for name in names:
        assert GitService.get_file_history(root, name)[0]["hash"] == latest
        assert GitService.get_blame(root, name, start=1, end=1)[0]["hash"] == latest
    for outside in ("../outside.txt", str(root.parent / "outside.txt"), ".git/config"):
        with pytest.raises(ValueError):
            GitService.get_file_history(root, outside)
    for bad_ref in ("--all", "-Ssecret", "HEAD\0", "HEAD\n", "missing"):
        with pytest.raises(ValueError):
            GitService.get_history(root, ref=bad_ref)
    for limit in (0, -1, 5001, True, "10"):
        with pytest.raises(ValueError):
            GitService.get_dashboard(root, limit=limit)


def test_status_rename_preserves_spaces_and_worktree_first_character(repo):
    root, _ = repo
    (root / "keep.txt").write_text("changed\n", encoding="utf-8")
    git(root, "mv", "renamed çay.txt", "new tea.txt")
    (root / "untracked çay.txt").write_text("untracked\n", encoding="utf-8")
    status = GitService.get_dashboard(root)["status"]
    assert status["clean"] is False
    assert {row["path"] for row in status["modified"]} == {"keep.txt"}
    assert status["staged"][0]["original_path"] == "renamed çay.txt"
    assert status["staged"][0]["path"] == "new tea.txt"
    assert status["untracked"][0]["path"] == "untracked çay.txt"


def test_external_diff_and_textconv_are_not_executed(repo, tmp_path):
    root, (_, second, third) = repo
    marker = tmp_path / "helper-executed"
    helper = tmp_path / "diff-helper"
    helper.write_text(f"#!/bin/sh\ntouch '{marker}'\n", encoding="utf-8")
    helper.chmod(0o700)
    git(root, "config", "diff.external", str(helper))
    git(root, "config", "diff.special.textconv", str(helper))
    (root / ".gitattributes").write_text("*.txt diff=special\n", encoding="utf-8")
    assert GitService.get_commit_detail(root, second)["diff"]
    assert GitService.compare_refs(root, second, third)["diff"]
    assert GitService.get_history(root, pickaxe="seasoned")
    assert GitService.get_file_history(root, "renamed çay.txt")
    assert GitService.get_blame(root, "renamed çay.txt")
    assert GitService.get_dashboard(root)["summary"]["commits"] == 3
    assert not marker.exists()


def test_remote_secrets_are_never_returned_and_detached_head_is_explicit(repo):
    root, (_, _, third) = repo
    git(root, "remote", "add", "origin", "https://private-user:private-password@example.test/private.git")
    info = GitService.get_repository_info(root)
    assert info["remotes"] == ["origin"]
    assert "private-password" not in str(info)
    git(root, "checkout", "--detach", third)
    info = GitService.get_repository_info(root)
    assert info["detached"] is True and info["branch"] == "Detached HEAD"
    assert not info["unborn"]


def test_shallow_repository_reports_incomplete_scope(repo, tmp_path):
    root, _ = repo
    target = tmp_path / "shallow"
    result = subprocess.run(["git", "clone", "--depth", "1", root.as_uri(), str(target)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    dashboard = GitService.get_dashboard(target)
    assert dashboard["repository"]["shallow"] is True
    assert dashboard["scope"]["shallow"] is True
    assert dashboard["summary"]["commits"] == 1
    assert any("eksik" in note for note in dashboard["scope"]["notes"])


def test_merge_commit_detail_is_first_parent_and_dashboard_counts_merge(repo):
    root, (_, _, third) = repo
    git(root, "checkout", "-b", "side")
    (root / "side.txt").write_text("side\n", encoding="utf-8")
    commit(root, "side addition", "Bob")
    git(root, "checkout", "main")
    git(root, "merge", "--no-ff", "side", "-m", "merge side")
    detail = GitService.get_commit_detail(root, "HEAD")
    assert len(detail["parents"]) == 2
    assert detail["diff_base"] == third
    assert detail["files"] == [{"status": "A", "path": "side.txt", "old_path": ""}]
    assert "+side" in detail["diff"]
    dashboard = GitService.get_dashboard(root)
    assert dashboard["summary"]["commits"] == 5
    assert dashboard["summary"]["merges"] == 1
    assert dashboard["summary"]["added"] == 5  # Merge contributes no duplicated numstat.


def test_remote_tracking_counts_and_symbolic_remote_head(repo):
    root, (first, second, third) = repo
    git(root, "remote", "add", "origin", "https://example.test/repo.git")
    git(root, "update-ref", "refs/remotes/origin/main", second)
    git(root, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    git(root, "config", "branch.main.remote", "origin")
    git(root, "config", "branch.main.merge", "refs/heads/main")
    details = GitService.get_branch_details(root)
    assert {row["name"] for row in details} == {"main", "origin/main"}
    current = next(row for row in details if row["current"])
    assert current["kind"] == "local" and current["remote"] is False
    assert current["upstream"] == "origin/main" and current["tracking_known"]
    assert (current["ahead"], current["behind"]) == (1, 0)
    remote = next(row for row in details if row["remote"])
    assert remote["hash"] == second and remote["current"] is False
    status = GitService.get_dashboard(root)["status"]
    assert (status["ahead"], status["behind"]) == (1, 0)
    git(root, "update-ref", "-d", "refs/remotes/origin/main")
    current = next(row for row in GitService.get_branch_details(root) if row["current"])
    assert current["upstream_gone"] and not current["tracking_known"]


def test_all_refs_includes_other_branches_on_unborn_head(repo):
    root, (_, _, third) = repo
    git(root, "checkout", "--orphan", "new-story")
    assert GitService.get_repository_info(root)["unborn"] is True
    assert GitService.get_history(root) == []
    assert GitService.get_history(root, all_refs=True)[0]["hash"] == third
    assert "rename tea" in GitService.get_graph(root)


def test_bare_repo_can_inspect_history_without_worktree(repo, tmp_path):
    root, (_, _, third) = repo
    target = tmp_path / "bare.git"
    result = subprocess.run(["git", "clone", "--bare", str(root), str(target)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert GitService.get_repository_info(target)["bare"] is True
    assert GitService.get_history(target)[0]["hash"] == third
    assert GitService.get_dashboard(target)["summary"]["commits"] == 3


def test_unrelated_refs_compare_explicitly_has_no_merge_base(repo):
    root, (_, _, third) = repo
    git(root, "checkout", "--orphan", "separate")
    git(root, "rm", "-rf", ".")
    (root / "new.txt").write_text("separate history\n", encoding="utf-8")
    separate = commit(root, "separate root", "Bob")
    comparison = GitService.compare_refs(root, third, separate)
    assert comparison["merge_base"] == ""
    assert comparison["ahead"] == 1 and comparison["behind"] == 3
    assert [row["hash"] for row in comparison["commits"]] == [separate]


def test_dashboard_activity_normalizes_timezone_and_mailmap(repo):
    root, _ = repo
    stamp = datetime.now(timezone.utc).replace(hour=21, minute=30, second=0, microsecond=0)
    local_stamp = stamp.astimezone(timezone(timedelta(hours=3))).isoformat()
    (root / "clock.txt").write_text("time\n", encoding="utf-8")
    git(root, "add", "clock.txt")
    git(root, "commit", "-m", "clock", env={
        "GIT_AUTHOR_NAME": "Bob Alias", "GIT_AUTHOR_EMAIL": "alias@example.test",
        "GIT_AUTHOR_DATE": local_stamp, "GIT_COMMITTER_DATE": local_stamp,
    })
    (root / ".mailmap").write_text("Bob <bob@example.test> Bob Alias <alias@example.test>\n", encoding="utf-8")
    dashboard = GitService.get_dashboard(root, days=3)
    assert dashboard["scope"]["timezone"] == "UTC"
    assert len(dashboard["activity"]) == 3
    assert sum(row["commits"] for row in dashboard["activity"]) == 4
    assert next(row for row in dashboard["contributors"] if row["author"] == "Bob")["commits"] == 2
