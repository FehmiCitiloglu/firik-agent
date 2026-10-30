"""Real temporary Git repositories verify isolation and data-preserving failures."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess

import pytest

from firik_agent.projects.isolated_workspaces import (IsolatedWorkspaceManager,
                                                     WorkspaceConflict, WorkspaceError)


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True,
                          text=True, timeout=15).stdout.strip()


def commit(root, message="update"):
    git(root, "add", "--all")
    git(root, "-c", "commit.gpgsign=false", "commit", "-m", message)
    return git(root, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "project with spaces"
    root.mkdir()
    git(root, "init", "--initial-branch=main")
    git(root, "config", "user.name", "Test")
    git(root, "config", "user.email", "test@example.invalid")
    (root / "a.txt").write_text("alpha\nmiddle\nomega\n")
    (root / "b.txt").write_text("original\n")
    (root / ".gitignore").write_text("*.cache\n")
    commit(root, "initial")
    return root.resolve()


def create(manager, worker="worker", **options):
    record = manager.create_workspace(worker, **options)
    return record, Path(record["path"])


def reviewed(manager, identifier):
    proposal = manager.propose_merge(identifier)
    manager.mark_reviewed(proposal["proposal_id"], "trusted_reviewer")
    return proposal


def test_worktree_is_detached_unique_persistent_and_independent(repo):
    manager = IsolatedWorkspaceManager(repo)
    record, child = create(manager)
    other, other_child = create(manager, "tester")
    assert record["workspace_id"] != other["workspace_id"]
    assert git(child, "rev-parse", "HEAD") == git(repo, "rev-parse", "HEAD")
    assert record["branch"] is None
    assert subprocess.run(["git", "-C", str(child), "symbolic-ref", "-q", "HEAD"], capture_output=True).returncode == 1
    (child / "a.txt").write_text("agent\n")
    assert (repo / "a.txt").read_text() == "alpha\nmiddle\nomega\n"
    assert (other_child / "a.txt").read_text() == "alpha\nmiddle\nomega\n"
    assert len(IsolatedWorkspaceManager(repo).list_workspaces()) == 2
    assert manager.inspect_status(record["workspace_id"])["dirty"]
    assert not manager.inspect_status(record["workspace_id"])["cleanup_eligible"]


def test_dirty_source_requires_explicit_clean_head_and_preserves_index(repo):
    manager = IsolatedWorkspaceManager(repo)
    (repo / "a.txt").write_text("user staged\n")
    git(repo, "add", "a.txt")
    (repo / "b.txt").write_text("user unstaged\n")
    (repo / "local.txt").write_text("untracked\n")
    before = git(repo, "status", "--porcelain")
    with pytest.raises(WorkspaceError, match="dirty"):
        create(manager)
    record, child = create(manager, dirty_policy="clean_head")
    assert record["source_dirty"] is True
    assert (child / "a.txt").read_text() == "alpha\nmiddle\nomega\n"
    assert not (child / "local.txt").exists()
    assert git(repo, "status", "--porcelain") == before
    assert (repo / "a.txt").read_text() == "user staged\n"


def test_collision_and_option_revisions_fail_without_mutation(repo):
    manager = IsolatedWorkspaceManager(repo)
    record, _ = create(manager, workspace_id="chosen_id")
    with pytest.raises(WorkspaceError, match="already exists"):
        create(manager, workspace_id=record["workspace_id"])
    for identifier in ("../bad", "--help", "a/b"):
        with pytest.raises(WorkspaceError):
            create(manager, workspace_id=identifier)
    with pytest.raises(WorkspaceError, match="Invalid starting"):
        create(manager, revision="--output=/tmp/escape")
    assert git(repo, "status", "--porcelain") == ""


def test_concurrent_creates_use_unique_worktrees_and_metadata_lock(repo):
    manager = IsolatedWorkspaceManager(repo)
    with ThreadPoolExecutor(max_workers=3) as executor:
        records = list(executor.map(lambda n: manager.create_workspace(f"worker{n}"), range(3)))
    assert len({record["path"] for record in records}) == 3
    assert all(manager.inspect_status(r["workspace_id"])["cleanup_eligible"] for r in records)


def test_binary_untracked_and_executable_patch_is_complete(repo):
    manager = IsolatedWorkspaceManager(repo)
    record, child = create(manager)
    (child / "a.txt").unlink()
    (child / "new.bin").write_bytes(b"\x00\x01binary\xff")
    (child / "run.sh").write_text("#!/bin/sh\nexit 0\n")
    (child / "run.sh").chmod(0o755)
    patch = manager.collect_patch(record["workspace_id"])
    assert set(patch["paths"]) == {"a.txt", "new.bin", "run.sh"}
    assert "GIT binary patch" in patch["patch"]
    proposal = reviewed(manager, record["workspace_id"])
    result = manager.integrate(proposal["proposal_id"], approved=True)
    assert result["state"] == "integrated" and result["verification_required"]
    assert not (repo / "a.txt").exists()
    assert (repo / "new.bin").read_bytes() == b"\x00\x01binary\xff"
    assert (repo / "run.sh").stat().st_mode & 0o111
    assert git(repo, "diff", "--cached") == ""


def test_review_and_approval_cannot_be_omitted_or_self_approved(repo):
    manager = IsolatedWorkspaceManager(repo)
    record, child = create(manager)
    (child / "a.txt").write_text("agent\n")
    proposal = manager.propose_merge(record["workspace_id"])
    with pytest.raises(WorkspaceError, match="review"):
        manager.integrate(proposal["proposal_id"], approved=True)
    with pytest.raises(WorkspaceError, match="own review"):
        manager.mark_reviewed(proposal["proposal_id"], "worker")
    manager.mark_reviewed(proposal["proposal_id"], "reviewer")
    with pytest.raises(WorkspaceError, match="permission"):
        manager.integrate(proposal["proposal_id"], approved="yes")
    assert (repo / "a.txt").read_text().startswith("alpha")


def test_stale_agent_patch_and_dirty_target_preserve_both_sides(repo):
    manager = IsolatedWorkspaceManager(repo)
    record, child = create(manager)
    (child / "a.txt").write_text("agent\n")
    proposal = reviewed(manager, record["workspace_id"])
    (child / "a.txt").write_text("new agent work\n")
    with pytest.raises(WorkspaceConflict, match="Agent work changed"):
        manager.integrate(proposal["proposal_id"], approved=True)
    (repo / "b.txt").write_text("user work\n")
    with pytest.raises(WorkspaceConflict, match="Target must be clean"):
        manager.integrate(proposal["proposal_id"], approved=True)
    assert (child / "a.txt").read_text() == "new agent work\n"
    assert (repo / "b.txt").read_text() == "user work\n"


def test_stale_target_revision_requires_new_review(repo):
    manager = IsolatedWorkspaceManager(repo)
    record, child = create(manager)
    (child / "a.txt").write_text("agent\n")
    proposal = reviewed(manager, record["workspace_id"])
    (repo / "b.txt").write_text("user committed\n")
    commit(repo)
    with pytest.raises(WorkspaceConflict, match="Target revision changed"):
        manager.integrate(proposal["proposal_id"], approved=True)
    assert (repo / "b.txt").read_text() == "user committed\n"


def test_three_way_merge_preserves_nonoverlapping_committed_user_edit(repo):
    manager = IsolatedWorkspaceManager(repo)
    record, child = create(manager)
    (child / "a.txt").write_text("agent alpha\nmiddle\nomega\n")
    (repo / "a.txt").write_text("alpha\nmiddle\nuser omega\n")
    commit(repo)
    proposal = reviewed(manager, record["workspace_id"])
    manager.integrate(proposal["proposal_id"], approved=True)
    assert (repo / "a.txt").read_text() == "agent alpha\nmiddle\nuser omega\n"


def test_three_way_conflict_does_not_touch_user_tree_or_index(repo):
    manager = IsolatedWorkspaceManager(repo)
    record, child = create(manager)
    (child / "a.txt").write_text("agent\n")
    (repo / "a.txt").write_text("user committed\n")
    commit(repo)
    before = git(repo, "status", "--porcelain")
    proposal = manager.propose_merge(record["workspace_id"])
    assert proposal["state"] == "conflict" and proposal["conflicts"] == ["a.txt"]
    with pytest.raises(WorkspaceConflict):
        manager.mark_reviewed(proposal["proposal_id"], "reviewer")
    assert git(repo, "status", "--porcelain") == before
    assert (repo / "a.txt").read_text() == "user committed\n"
    assert (child / "a.txt").read_text() == "agent\n"


def test_overlap_between_agents_is_observable(repo):
    manager = IsolatedWorkspaceManager(repo)
    a, a_root = create(manager, "worker_a")
    b, b_root = create(manager, "worker_b")
    (a_root / "a.txt").write_text("a\n")
    (b_root / "a.txt").write_text("b\n")
    assert manager.overlapping_paths([a["workspace_id"], b["workspace_id"]]) == ["a.txt"]
    assert (repo / "a.txt").read_text().startswith("alpha")


def test_failed_integration_rolls_back_written_files(repo, monkeypatch):
    manager = IsolatedWorkspaceManager(repo)
    record, child = create(manager)
    (child / "a.txt").write_text("agent a\n")
    (child / "b.txt").write_text("agent b\n")
    proposal = reviewed(manager, record["workspace_id"])
    original = manager._write_content
    failed = False
    def fail_second(relative, data, mode):
        nonlocal failed
        if relative == "b.txt" and not failed:
            failed = True
            raise OSError("simulated full disk")
        return original(relative, data, mode)
    monkeypatch.setattr(manager, "_write_content", fail_second)
    with pytest.raises(OSError, match="full disk"):
        manager.integrate(proposal["proposal_id"], approved=True)
    assert git(repo, "status", "--porcelain") == ""
    assert manager.get_proposal(proposal["proposal_id"])["state"] == "rolled_back"
    assert (child / "a.txt").read_text() == "agent a\n"


def test_rollback_never_overwrites_concurrent_user_edit(repo, monkeypatch):
    manager = IsolatedWorkspaceManager(repo)
    record, child = create(manager)
    (child / "a.txt").write_text("agent a\n")
    (child / "b.txt").write_text("agent b\n")
    proposal = reviewed(manager, record["workspace_id"])
    original = manager._write_content
    def fail_second(relative, data, mode):
        if relative == "b.txt":
            (repo / "a.txt").write_text("concurrent user\n")
            raise OSError("interrupted")
        return original(relative, data, mode)
    monkeypatch.setattr(manager, "_write_content", fail_second)
    with pytest.raises(OSError):
        manager.integrate(proposal["proposal_id"], approved=True)
    assert (repo / "a.txt").read_text() == "concurrent user\n"
    assert manager.get_proposal(proposal["proposal_id"])["rollback_pending"] == ["a.txt"]


def test_ignored_target_file_is_never_overwritten(repo):
    manager = IsolatedWorkspaceManager(repo)
    record, child = create(manager)
    # Force-track a normally ignored new artifact in the child.
    (child / "secret.cache").write_text("agent\n")
    git(child, "add", "-f", "secret.cache")
    proposal = reviewed(manager, record["workspace_id"])
    (repo / "secret.cache").write_text("user ignored data\n")
    with pytest.raises(WorkspaceConflict, match="ignored or untracked"):
        manager.integrate(proposal["proposal_id"], approved=True)
    assert (repo / "secret.cache").read_text() == "user ignored data\n"


def test_symlink_escape_in_child_and_target_is_rejected(repo, tmp_path):
    manager = IsolatedWorkspaceManager(repo)
    record, child = create(manager)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n")
    (child / "a.txt").unlink()
    (child / "a.txt").symlink_to(outside)
    with pytest.raises(WorkspaceError, match="Symlink"):
        manager.collect_patch(record["workspace_id"])
    assert outside.read_text() == "outside\n"


def test_tracked_symlinks_rejected_before_worktree_created(repo, tmp_path):
    (repo / "link").symlink_to(tmp_path)
    commit(repo)
    manager = IsolatedWorkspaceManager(repo)
    with pytest.raises(WorkspaceError, match="Tracked symlinks"):
        create(manager)
    assert manager.list_workspaces() == []


def test_cleanup_refuses_dirty_ignored_or_committed_agent_work(repo):
    manager = IsolatedWorkspaceManager(repo)
    record, child = create(manager)
    (child / "ignored.cache").write_text("expensive build\n")
    with pytest.raises(WorkspaceError, match="retained"):
        manager.destroy_workspace(record["workspace_id"])
    (child / "ignored.cache").unlink()
    (child / "a.txt").write_text("committed work\n")
    commit(child)
    with pytest.raises(WorkspaceError, match="retained"):
        manager.destroy_workspace(record["workspace_id"])
    assert child.exists()
    clean, clean_child = create(manager, "clean")
    assert manager.destroy_workspace(clean["workspace_id"])["state"] == "destroyed"
    assert not clean_child.exists()


def test_crash_reconciliation_retains_uncertain_work_and_integration(repo):
    manager = IsolatedWorkspaceManager(repo)
    record, child = create(manager)
    record["state"] = "creating"
    manager._save(manager.records, record["workspace_id"], record)
    (child / "a.txt").write_text("partial work\n")
    proposal = manager.propose_merge(record["workspace_id"])
    proposal["state"] = "integrating"
    manager._save(manager.proposals, proposal["proposal_id"], proposal)
    record["state"] = "creating"
    manager._save(manager.records, record["workspace_id"], record)
    restored = IsolatedWorkspaceManager(repo)
    assert restored.reconcile()[0]["state"] == "creation_interrupted_retained"
    assert restored.get_proposal(proposal["proposal_id"])["state"] == "integration_interrupted_inspection_required"
    assert (child / "a.txt").read_text() == "partial work\n"


def test_repository_hooks_filters_external_diff_and_fsmonitor_never_execute(repo, tmp_path):
    marker = tmp_path / "PWNED"
    script = tmp_path / "malicious.sh"
    script.write_text(f"#!/bin/sh\ntouch '{marker}'\ncat\n")
    script.chmod(0o755)
    (repo / ".gitattributes").write_text("*.txt filter=evil diff=evil merge=evil\n")
    commit(repo)
    git(repo, "config", "filter.evil.smudge", str(script))
    git(repo, "config", "filter.evil.clean", str(script))
    git(repo, "config", "diff.evil.command", str(script))
    git(repo, "config", "diff.evil.textconv", str(script))
    git(repo, "config", "merge.evil.driver", str(script))
    git(repo, "config", "core.fsmonitor", str(script))
    hook = repo / ".git" / "hooks" / "post-checkout"
    hook.write_text(script.read_text())
    hook.chmod(0o755)
    manager = IsolatedWorkspaceManager(repo)
    record, child = create(manager)
    assert not marker.exists()
    (child / "a.txt").write_text("agent\n")
    patch = manager.collect_patch(record["workspace_id"])
    assert patch["paths"] == ["a.txt"] and not marker.exists()
    proposal = reviewed(manager, record["workspace_id"])
    manager.integrate(proposal["proposal_id"], approved=True)
    assert not marker.exists()


def test_custom_merge_driver_cannot_execute_during_actual_three_way_merge(repo, tmp_path):
    (repo / ".gitattributes").write_text("*.txt merge=evil\n")
    commit(repo)
    manager = IsolatedWorkspaceManager(repo)
    record, child = create(manager)
    (child / "a.txt").write_text("agent alpha\nmiddle\nomega\n")
    (repo / "a.txt").write_text("alpha\nmiddle\nuser omega\n")
    commit(repo)
    marker = tmp_path / "MERGE_PWNED"
    script = tmp_path / "merge-driver.sh"
    script.write_text(f"#!/bin/sh\ntouch '{marker}'\nexit 0\n")
    script.chmod(0o755)
    git(repo, "config", "merge.evil.driver", str(script))
    proposal = reviewed(manager, record["workspace_id"])
    manager.integrate(proposal["proposal_id"], approved=True)
    assert (repo / "a.txt").read_text() == "agent alpha\nmiddle\nuser omega\n"
    assert not marker.exists()


def test_clean_worktree_cleanup_does_not_run_repo_clean_filter(repo, tmp_path):
    (repo / ".gitattributes").write_text("*.txt filter=evil\n")
    commit(repo)
    marker = tmp_path / "CLEAN_PWNED"
    script = tmp_path / "filter.sh"
    script.write_text(f"#!/bin/sh\ntouch '{marker}'\ncat\n")
    script.chmod(0o755)
    git(repo, "config", "filter.evil.clean", str(script))
    manager = IsolatedWorkspaceManager(repo)
    record, child = create(manager)
    assert manager.destroy_workspace(record["workspace_id"])["state"] == "destroyed"
    assert not marker.exists()
    assert not child.exists()


def test_overlapping_workspaces_require_trusted_explicit_resolution_even_if_mergeable(repo):
    manager = IsolatedWorkspaceManager(repo)
    a, a_root = create(manager, "worker_a")
    b, b_root = create(manager, "worker_b")
    (a_root / "a.txt").write_text("agent alpha\nmiddle\nomega\n")
    (b_root / "a.txt").write_text("alpha\nmiddle\nother omega\n")
    proposal = manager.propose_merge(a["workspace_id"])
    assert proposal["conflicts"] == ["a.txt"]
    assert b["workspace_id"] in proposal["overlapping_workspaces"]
    with pytest.raises(WorkspaceConflict):
        manager.mark_reviewed(proposal["proposal_id"], "reviewer")
    with pytest.raises(WorkspaceError):
        manager.resolve_overlaps(proposal["proposal_id"], {}, "reviewer")
    manager.resolve_overlaps(proposal["proposal_id"], {b["workspace_id"]: "Inspected both; integrate first result now, retain second result for follow-up."}, "reviewer")
    manager.mark_reviewed(proposal["proposal_id"], "reviewer")
    manager.integrate(proposal["proposal_id"], approved=True)
    assert (b_root / "a.txt").read_text() == "alpha\nmiddle\nother omega\n"


def test_overlap_created_after_review_blocks_integration(repo):
    manager = IsolatedWorkspaceManager(repo)
    a, a_root = create(manager, "worker_a")
    b, b_root = create(manager, "worker_b")
    (a_root / "a.txt").write_text("agent alpha\nmiddle\nomega\n")
    proposal = reviewed(manager, a["workspace_id"])
    (b_root / "a.txt").write_text("alpha\nmiddle\nother omega\n")
    with pytest.raises(WorkspaceConflict, match="Overlapping agent edits"):
        manager.integrate(proposal["proposal_id"], approved=True)
    assert (repo / "a.txt").read_text() == "alpha\nmiddle\nomega\n"


def test_hardlinked_host_file_cannot_enter_patch(repo, tmp_path):
    manager = IsolatedWorkspaceManager(repo)
    record, child = create(manager)
    outside = tmp_path / "host-secret"
    outside.write_text("host private data\n")
    os.link(outside, child / "leaked.txt")
    with pytest.raises(WorkspaceError, match="regular files"):
        manager.collect_patch(record["workspace_id"])
    assert outside.read_text() == "host private data\n"


def test_tampered_git_pointer_and_workspace_symlink_are_rejected(repo, tmp_path):
    manager = IsolatedWorkspaceManager(repo)
    record, child = create(manager)
    (child / ".git").write_text(f"gitdir: {repo / '.git'}\n")
    with pytest.raises(WorkspaceError, match="association"):
        manager.inspect_status(record["workspace_id"])
    # A user-selected storage symlink must never be followed.
    link = tmp_path / "storage-link"
    link.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(WorkspaceError, match="symlink"):
        IsolatedWorkspaceManager(repo, link)


def test_integration_refuses_parent_symlink_even_if_ignored(repo, tmp_path):
    manager = IsolatedWorkspaceManager(repo)
    record, child = create(manager)
    (child / "nested").mkdir()
    (child / "nested" / "file.txt").write_text("agent\n")
    proposal = reviewed(manager, record["workspace_id"])
    outside = tmp_path / "outside"
    outside.mkdir()
    (repo / "nested").symlink_to(outside, target_is_directory=True)
    # Dirty rejection or symlink rejection both preserve the target and outside.
    with pytest.raises(WorkspaceError):
        manager.integrate(proposal["proposal_id"], approved=True)
    assert not (outside / "file.txt").exists()


def test_repository_union_merge_cannot_hide_a_three_way_conflict(repo):
    (repo / ".gitattributes").write_text("*.txt merge=union\n")
    commit(repo)
    manager = IsolatedWorkspaceManager(repo)
    record, child = create(manager)
    (child / "a.txt").write_text("agent alpha\nmiddle\nomega\n")
    (repo / "a.txt").write_text("user alpha\nmiddle\nomega\n")
    commit(repo)
    proposal = manager.propose_merge(record["workspace_id"])
    assert proposal["state"] == "conflict" and proposal["conflicts"] == ["a.txt"]
    assert (repo / "a.txt").read_text().startswith("user alpha")
