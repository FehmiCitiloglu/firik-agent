"""Real local persistence and optimistic file/hunk review, without provider calls."""
from concurrent.futures import ThreadPoolExecutor
import os
import sqlite3
import subprocess

import pytest

from firik_agent.projects.checkpoints import CheckpointConflict, CheckpointStore
from firik_agent.projects.coding_sessions import CodingSessionStore
from firik_agent.projects.db import ProjectDatabase
from firik_agent.projects.models import Project


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    db = ProjectDatabase(tmp_path / "projects.db")
    db.create_project(Project(id="p1", name="Birinci", local_path=str(root)))
    db.create_project(Project(id="p2", name="İkinci", local_path=str(root)))
    run = CodingSessionStore(db).create_run("p1")
    return root, db, run.id, CheckpointStore(db)


def compare(store, checkpoint, root, run_id=None):
    return store.compare_checkpoint(checkpoint["id"], project_id="p1", root=root, run_id=run_id)


def restore(store, checkpoint, root, preview):
    return store.restore_checkpoint(checkpoint["id"], project_id="p1", root=root,
                                    expected_hashes=preview["expected_hashes"])


def test_checkpoint_survives_restart_and_preserves_preexisting_dirty_text(workspace):
    root, db, run_id, store = workspace
    (root / "dirty.py").write_text("print('kullanıcı düzenlemesi')\n", encoding="utf-8")
    (root / "deleted.py").write_text("obsolete\n")
    checkpoint = store.create_checkpoint("p1", root, run_id=run_id, label="Başlangıç")
    (root / "dirty.py").write_text("print('ajan değişikliği')\n", encoding="utf-8")
    (root / "deleted.py").unlink()
    (root / "new.py").write_text("created\n")
    reopened = CheckpointStore(ProjectDatabase(db.db_path))
    assert reopened.list_checkpoints("p1", run_id=run_id)[0] == checkpoint
    preview = compare(reopened, checkpoint, root, run_id)
    assert {c["path"] for c in preview["changes"]} == {"dirty.py", "deleted.py", "new.py"}
    result = restore(reopened, checkpoint, root, preview)
    assert (root / "dirty.py").read_text(encoding="utf-8") == "print('kullanıcı düzenlemesi')\n"
    assert (root / "deleted.py").read_text() == "obsolete\n"
    assert not (root / "new.py").exists()
    backup = {"id": result["backup_checkpoint_id"]}
    restore(reopened, backup, root, compare(reopened, backup, root))
    assert (root / "dirty.py").read_text(encoding="utf-8") == "print('ajan değişikliği')\n"
    assert not (root / "deleted.py").exists()
    assert (root / "new.py").read_text() == "created\n"


def test_restore_rejects_stale_preview_before_any_mutation(workspace):
    root, _, _, store = workspace
    (root / "a.py").write_text("old A\n")
    (root / "b.py").write_text("old B\n")
    checkpoint = store.create_checkpoint("p1", root)
    (root / "a.py").write_text("agent A\n")
    (root / "b.py").write_text("agent B\n")
    preview = compare(store, checkpoint, root)
    (root / "b.py").write_text("user edit after preview\n")
    with pytest.raises(CheckpointConflict, match="kullanıcı"):
        restore(store, checkpoint, root, preview)
    assert (root / "a.py").read_text() == "agent A\n"
    assert (root / "b.py").read_text() == "user edit after preview\n"
    assert len(store.list_checkpoints("p1")) == 1


def test_hunk_rejection_is_independent_and_acceptance_is_revision_bound(workspace):
    root, db, _, store = workspace
    original = "".join(f"line {i}\n" for i in range(30))
    (root / "main.py").write_text(original)
    checkpoint = store.create_checkpoint("p1", root)
    changed = original.replace("line 2\n", "agent 2\n").replace("line 25\n", "agent 25\n")
    (root / "main.py").write_text(changed)
    change = compare(store, checkpoint, root)["changes"][0]
    assert len(change["hunks"]) == 2
    first, second = change["hunks"]
    store.review_change(checkpoint["id"], project_id="p1", root=root, path="main.py", decision="accepted",
                        revision=change["revision"], hunk_id=second["id"])
    reopened = CheckpointStore(ProjectDatabase(db.db_path))
    assert compare(reopened, checkpoint, root)["changes"][0]["hunks"][1]["decision"] == "accepted"
    result = reopened.review_change(checkpoint["id"], project_id="p1", root=root, path="main.py", decision="rejected",
                                    revision=change["revision"], hunk_id=first["id"])
    assert result["backup_checkpoint_id"]
    assert (root / "main.py").read_text() == original.replace("line 25\n", "agent 25\n")
    assert compare(reopened, checkpoint, root)["changes"][0]["hunks"][0]["decision"] == "accepted"
    with pytest.raises(CheckpointConflict, match="incelemeden"):
        reopened.review_change(checkpoint["id"], project_id="p1", root=root, path="main.py", decision="rejected",
                               revision=change["revision"], hunk_id=second["id"])


@pytest.mark.parametrize("initial,new", [(None, "new\n"), ("old\n", None), (None, "")])
def test_whole_file_reject_handles_add_delete_and_empty(workspace, initial, new):
    root, _, _, store = workspace
    path = root / "main.py"
    if initial is not None:
        path.write_text(initial)
    checkpoint = store.create_checkpoint("p1", root)
    if new is None:
        path.unlink()
    else:
        path.write_text(new)
    change = compare(store, checkpoint, root)["changes"][0]
    store.review_change(checkpoint["id"], project_id="p1", root=root, path="main.py", decision="rejected",
                        revision=change["revision"])
    assert path.read_text() == initial if initial is not None else not path.exists()


def test_checkpoint_scopes_and_root_are_enforced(workspace, tmp_path):
    root, db, run_id, store = workspace
    other_run = CodingSessionStore(db).create_run("p2")
    checkpoint = store.create_checkpoint("p1", root, run_id=run_id)
    with pytest.raises(ValueError, match="projeye"):
        store.create_checkpoint("p1", root, run_id=other_run.id)
    with pytest.raises(ValueError, match="proje"):
        store.compare_checkpoint(checkpoint["id"], project_id="p2", root=root)
    with pytest.raises(ValueError, match="çalışmaya"):
        store.compare_checkpoint(checkpoint["id"], project_id="p1", root=root,
                                 run_id=CodingSessionStore(db).create_run("p1").id)
    other_root = tmp_path / "other"
    other_root.mkdir()
    with pytest.raises(ValueError, match="klasörüne"):
        store.compare_checkpoint(checkpoint["id"], project_id="p1", root=other_root)
    with pytest.raises(ValueError, match="klasörüne"):
        store.create_checkpoint("p1", other_root)


def test_secrets_binary_links_and_dependencies_never_enter_persistent_snapshots(workspace, tmp_path):
    root, db, _, _ = workspace
    known = "known-configured-value-777"
    store = CheckpointStore(db, known_secrets=(known,))
    (root / "safe.py").write_text("ok = True\n")
    (root / ".env").write_text("password=environment-secret\n")
    (root / "inline.py").write_text("api_key = 'inline-secret-777'\n")
    (root / "known.py").write_text(f"ordinary = '{known}'\n")
    (root / "binary.txt").write_bytes(b"\x00binary")
    (root / "invalid.txt").write_bytes(b"\xff\xfe")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "third.js").write_text("dependency\n")
    outside = tmp_path / "outside.py"
    outside.write_text("outside confidential text\n")
    (root / "sym.py").symlink_to(outside)
    os.link(outside, root / "hard.py")
    checkpoint = store.create_checkpoint("p1", root, label="api_key='label-secret-777'")
    assert checkpoint["file_count"] == 1
    conn = sqlite3.connect(db.db_path)
    try:
        persisted = conn.execute("SELECT label, snapshot FROM coding_checkpoints").fetchall()
    finally:
        conn.close()
    text = str(persisted)
    for value in ("environment-secret", "inline-secret-777", known, "outside confidential text", "label-secret-777"):
        assert value not in text
    assert "ok = True" in text
    assert "[SECRET_FILTERED]" in text


@pytest.mark.parametrize("link_kind", ["symbolic", "hard", "directory"])
def test_restore_refuses_links_introduced_after_preview(workspace, tmp_path, link_kind):
    root, _, _, store = workspace
    (root / "folder").mkdir()
    path = root / "folder" / "main.py"
    path.write_text("before\n")
    checkpoint = store.create_checkpoint("p1", root)
    path.write_text("agent\n")
    preview = compare(store, checkpoint, root)
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / "main.py"
    target.write_text("private\n")
    path.unlink()
    if link_kind == "symbolic":
        path.symlink_to(target)
    elif link_kind == "hard":
        os.link(target, path)
    else:
        path.parent.rmdir()
        path.parent.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        store.restore_checkpoint(checkpoint["id"], project_id="p1", root=root, expected_hashes=preview["expected_hashes"],
                                 paths=["folder/main.py"])
    assert target.read_text() == "private\n"


def test_partial_snapshots_cannot_delete_unobserved_files(workspace):
    root, _, _, store = workspace
    (root / "a.py").write_text("before\n")
    (root / "b.py").write_text("user file\n")
    checkpoint = store.create_checkpoint("p1", root, max_files=1)
    assert checkpoint["limited"]
    (root / "a.py").write_text("after\n")
    (root / "new.py").write_text("also user file\n")
    preview = compare(store, checkpoint, root)
    assert [c["path"] for c in preview["changes"]] == ["a.py"]
    restore(store, checkpoint, root, preview)
    assert (root / "b.py").read_text() == "user file\n"
    assert (root / "new.py").read_text() == "also user file\n"


def test_targeted_pre_mutation_checkpoint_can_restore_new_file(workspace):
    root, _, run_id, store = workspace
    checkpoint = store.create_checkpoint("p1", root, run_id=run_id, paths=["new.py"], kind="before_edit")
    (root / "new.py").write_text("created\n")
    (root / "unrelated.py").write_text("user\n")
    preview = compare(store, checkpoint, root)
    assert [c["path"] for c in preview["changes"]] == ["new.py"]
    restore(store, checkpoint, root, preview)
    assert not (root / "new.py").exists()
    assert (root / "unrelated.py").read_text() == "user\n"


def test_restore_preview_does_not_include_new_user_file_created_after_review(workspace):
    root, _, _, store = workspace
    (root / "main.py").write_text("before\n")
    checkpoint = store.create_checkpoint("p1", root)
    (root / "main.py").write_text("agent\n")
    preview = compare(store, checkpoint, root)
    (root / "new_user.py").write_text("user\n")
    restore(store, checkpoint, root, preview)
    assert (root / "main.py").read_text() == "before\n"
    assert (root / "new_user.py").read_text() == "user\n"


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO requires POSIX")
def test_fifo_snapshot_is_nonblocking_and_skipped(workspace):
    root, _, _, store = workspace
    os.mkfifo(root / "pipe.txt")
    checkpoint = store.create_checkpoint("p1", root)
    assert checkpoint["file_count"] == 0
    assert checkpoint["skipped"] == ["pipe.txt"]


def test_failed_multi_file_restore_rolls_back_and_keeps_durable_backup(workspace, monkeypatch):
    root, db, _, store = workspace
    for name in ("a.py", "b.py"):
        (root / name).write_text("before\n")
    checkpoint = store.create_checkpoint("p1", root)
    for name in ("a.py", "b.py"):
        (root / name).write_text("agent\n")
    preview = compare(store, checkpoint, root)
    replace = store._replace

    def fail_second(root, path, content, expected_hash, mode=None):
        if path == "b.py":
            raise OSError("disk full")
        replace(root, path, content, expected_hash, mode)

    monkeypatch.setattr(store, "_replace", fail_second)
    with pytest.raises(CheckpointConflict) as failure:
        restore(store, checkpoint, root, preview)
    assert failure.value.backup_checkpoint_id
    assert (root / "a.py").read_text() == (root / "b.py").read_text() == "agent\n"
    reopened = CheckpointStore(ProjectDatabase(db.db_path))
    assert reopened.list_operations("p1")[0]["status"] == "rolled_back"
    assert reopened.list_checkpoints("p1")[0]["id"] == failure.value.backup_checkpoint_id


def test_open_is_passive_after_interrupted_restore(workspace, monkeypatch):
    root, db, _, store = workspace
    (root / "main.py").write_text("before\n")
    checkpoint = store.create_checkpoint("p1", root)
    (root / "main.py").write_text("agent\n")
    preview = compare(store, checkpoint, root)

    def crash(*args, **kwargs):
        raise SystemExit("simulated process interruption")

    monkeypatch.setattr(store, "_replace", crash)
    with pytest.raises(SystemExit):
        restore(store, checkpoint, root, preview)
    reopened = CheckpointStore(ProjectDatabase(db.db_path))
    assert reopened.list_operations("p1")[0]["status"] == "applying"
    assert (root / "main.py").read_text() == "agent\n"
    assert reopened.list_checkpoints("p1")[0]["kind"] == "before_restore"


def test_git_staged_index_and_dirty_worktree_are_untouched(workspace):
    root, _, _, store = workspace
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "main.py").write_text("staged baseline\n")
    subprocess.run(["git", "-C", str(root), "add", "main.py"], check=True)
    index = (root / ".git" / "index").read_bytes()
    (root / "main.py").write_text("dirty user baseline\n")
    checkpoint = store.create_checkpoint("p1", root)
    (root / "main.py").write_text("agent text\n")
    restore(store, checkpoint, root, compare(store, checkpoint, root))
    assert (root / ".git" / "index").read_bytes() == index
    assert (root / "main.py").read_text() == "dirty user baseline\n"
    assert not (root / ".git" / "refs" / "stash").exists()


def test_parallel_checkpoint_writes_use_existing_database_serialization(workspace):
    root, db, _, store = workspace
    (root / "main.py").write_text("pass\n")
    second = CheckpointStore(db)
    with ThreadPoolExecutor(max_workers=4) as pool:
        values = list(pool.map(lambda i: (store if i % 2 else second).create_checkpoint("p1", root, label=str(i)), range(12)))
    assert len({v["id"] for v in values}) == len(store.list_checkpoints("p1")) == 12
