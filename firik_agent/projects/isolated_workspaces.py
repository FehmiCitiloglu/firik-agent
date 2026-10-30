"""Persistent, conservative agent worktrees and revision-bound integration proposals.

This is application-level conflict protection, not an OS sandbox. Git plumbing
avoids repository hooks, checkout filters, external diff tools and merge drivers.
Uncommitted user changes are never copied implicitly or reset by this manager.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import signal
import stat
import subprocess
import tempfile
import threading
import time
from typing import Any
from uuid import uuid4


class WorkspaceError(ValueError):
    """A fail-closed workspace or integration precondition was not satisfied."""


class WorkspaceConflict(WorkspaceError):
    """A proposal conflicts with current user or agent work."""


_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()
_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}\Z")
_OID = re.compile(r"[a-f0-9]{40}(?:[a-f0-9]{24})?\Z")
MAX_BYTES = 32 * 1024 * 1024
MAX_FILES = 10_000


def _identifier(value: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise WorkspaceError("Invalid workspace identifier.")
    return value


def _relative(value: str) -> str:
    if (not isinstance(value, str) or not value or "\\" in value or "\x00" in value
            or any(ord(c) < 32 for c in value)):
        raise WorkspaceError("Unsupported or unsafe file name.")
    path = PurePosixPath(value)
    if path.is_absolute() or any(p in {"", ".", ".."} or p.lower() == ".git" for p in value.split("/")):
        raise WorkspaceError("Path leaves the workspace or addresses Git metadata.")
    return value


def _no_symlinks(path: Path) -> None:
    current = path.absolute()
    for item in [current, *current.parents]:
        if item.is_symlink():
            raise WorkspaceError("Symlink paths are not supported for workspace management.")


class IsolatedWorkspaceManager:
    """Create detached agent worktrees; retain edits until explicitly integrated.

    All methods are synchronous and bounded. Call them with ``asyncio.to_thread``
    from an asynchronous runtime. ``approved=True`` on integrate is a caller's
    assertion that its existing permission gate authorized this exact proposal.
    Review identity must be supplied by a trusted reviewer, never by the worker.
    """

    def __init__(self, repository: str | Path, storage_root: str | Path | None = None):
        candidate = Path(repository).absolute()
        if candidate.is_symlink():
            raise WorkspaceError("Repository root cannot be a symlink.")
        self.repository = candidate.resolve()
        _no_symlinks(self.repository)
        if not self.repository.is_dir():
            raise WorkspaceError("Repository directory does not exist.")
        self._hooks = Path(tempfile.gettempdir()) / "firik-disabled-git-hooks"
        # A nonexistent path disables hooks without trusting a shared writable directory.
        self._hooks = self._hooks / uuid4().hex
        top = self._git(self.repository, ["rev-parse", "--show-toplevel"]).decode().strip()
        if Path(top).resolve() != self.repository:
            raise WorkspaceError("Select the repository root, not a subdirectory.")
        common = self._git(self.repository, ["rev-parse", "--git-common-dir"]).decode().strip()
        common_path = Path(common)
        self.common_dir = (common_path if common_path.is_absolute() else self.repository / common_path).resolve()
        self.storage_root = Path(storage_root).absolute() if storage_root else self.common_dir / "firik-isolation"
        if self.storage_root.is_symlink():
            raise WorkspaceError("Workspace storage cannot be a symlink.")
        self.storage_root = self.storage_root.resolve()
        _no_symlinks(self.storage_root)
        self.storage_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.storage_root = self.storage_root.resolve()
        if self.storage_root == self.repository:
            raise WorkspaceError("Workspace storage cannot replace the source repository.")
        self.trees = ((self.repository.parent / ".firik-workspaces" / hashlib.sha256(str(self.common_dir).encode()).hexdigest()[:16]).resolve()
                      if storage_root is None else self.storage_root / "trees")
        self.records = self.storage_root / "records"
        self.proposals = self.storage_root / "proposals"
        for directory in (self.trees, self.records, self.proposals):
            _no_symlinks(directory)
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        with _LOCKS_GUARD:
            self._thread_lock = _LOCKS.setdefault(str(self.common_dir), threading.RLock())

    def _env(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                   GIT_TERMINAL_PROMPT="0", GIT_ATTR_NOSYSTEM="1", GCM_INTERACTIVE="never",
                   GIT_PAGER="cat", GIT_EDITOR="false", GIT_ASKPASS="", LC_ALL="C",
                   GIT_NO_LAZY_FETCH="1", GIT_NO_REPLACE_OBJECTS="1", GIT_ALLOW_PROTOCOL="")
        if extra:
            env.update(extra)
        return env

    def _git(self, root: Path, args: list[str], *, data: bytes | None = None,
             extra: dict[str, str] | None = None, check: bool = True, limit: int = MAX_BYTES) -> bytes:
        configs = {"core.hooksPath": str(self._hooks), "core.fsmonitor": "false",
                   "core.attributesFile": os.devnull, "core.excludesFile": os.devnull,
                   "core.autocrlf": "false", "core.safecrlf": "false", "core.untrackedCache": "false",
                   "core.splitIndex": "false", "submodule.recurse": "false", "gc.auto": "0",
                   "maintenance.auto": "false", "commit.gpgsign": "false", "diff.external": "",
                   "protocol.ext.allow": "never", "protocol.file.allow": "never"}
        argv = ["git", "--no-pager", "--literal-pathspecs", "-C", str(root)]
        for key, value in configs.items():
            argv += ["-c", f"{key}={value}"]
        env = self._env(extra)
        env["GIT_WORK_TREE"] = str(root)
        with tempfile.TemporaryFile() as incoming, tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
            if data:
                incoming.write(data)
            incoming.seek(0)
            try:
                process = subprocess.Popen(argv + args, env=env, stdin=incoming, stdout=out, stderr=err,
                                           start_new_session=os.name != "nt")
                deadline = time.monotonic() + 30
                while process.poll() is None:
                    if time.monotonic() > deadline or out.tell() > limit or err.tell() > limit:
                        if os.name == "nt":
                            process.kill()
                        else:
                            try:
                                os.killpg(process.pid, signal.SIGKILL)
                            except ProcessLookupError:
                                pass
                        process.wait(timeout=5)
                        raise WorkspaceError("Git operation exceeded its time or output limit; outcome requires inspection.")
                    try:
                        process.wait(timeout=0.05)
                    except subprocess.TimeoutExpired:
                        pass
            except OSError as exc:
                raise WorkspaceError("Git is unavailable.") from exc
            if out.tell() > limit or err.tell() > limit:
                raise WorkspaceError("Git output exceeded its safe limit.")
            out.seek(0)
            err.seek(0)
            result, error = out.read(limit + 1), err.read(limit + 1)
            if process.returncode and check:
                # Paths and fixed Git diagnostics are useful, but bound error data.
                raise WorkspaceError(error.decode("utf-8", "replace")[:2048] or "Git operation failed.")
            return result

    @contextmanager
    def _locked(self):
        with self._thread_lock:
            path = self.common_dir / "firik-workspace-manager.lock"
            _no_symlinks(path)
            descriptor = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
            try:
                if os.name == "nt":
                    import msvcrt
                    if os.fstat(descriptor).st_size == 0:
                        os.write(descriptor, b"\0")
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(descriptor, fcntl.LOCK_EX)
                yield
            finally:
                os.close(descriptor)

    def _save(self, directory: Path, identifier: str, value: dict) -> None:
        _identifier(identifier)
        destination = directory / f"{identifier}.json"
        _no_symlinks(destination)
        descriptor, temporary = tempfile.mkstemp(prefix=".record-", dir=directory)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(value, stream, ensure_ascii=False, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _load(self, directory: Path, identifier: str) -> dict:
        path = directory / f"{_identifier(identifier)}.json"
        _no_symlinks(path)
        try:
            if path.stat().st_size > MAX_BYTES * 2:
                raise WorkspaceError("Workspace record is too large.")
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise WorkspaceError("Workspace record does not exist or is invalid.") from exc
        if value.get("repository") != str(self.repository):
            raise WorkspaceError("Workspace belongs to a different repository.")
        return value

    def _record(self, workspace_id: str) -> dict:
        value = self._load(self.records, workspace_id)
        expected = self.trees / _identifier(workspace_id)
        if value.get("path") != str(expected):
            raise WorkspaceError("Workspace path metadata was changed.")
        _no_symlinks(expected)
        if expected.exists():
            if (expected / ".git").is_symlink():
                raise WorkspaceError("Workspace Git pointer cannot be a symlink.")
            actual_git = self._git(expected, ["rev-parse", "--absolute-git-dir"]).decode().strip()
            if value.get("git_directory") and Path(actual_git).resolve() != Path(value["git_directory"]):
                raise WorkspaceError("Workspace Git metadata association was changed.")
            common = self._git(expected, ["rev-parse", "--git-common-dir"]).decode().strip()
            common_path = Path(common)
            actual = (common_path if common_path.is_absolute() else expected / common_path).resolve()
            if actual != self.common_dir:
                raise WorkspaceError("Workspace Git association was changed.")
        return value

    def _revision(self, root: Path, revision: str) -> str:
        if not isinstance(revision, str) or not revision or revision.startswith("-") or any(c in revision for c in "\0\r\n"):
            raise WorkspaceError("Invalid starting Git revision.")
        result = self._git(root, ["rev-parse", "--verify", "--end-of-options", f"{revision}^{{commit}}"], limit=1024).decode().strip()
        if not _OID.fullmatch(result):
            raise WorkspaceError("Starting revision is not a commit.")
        return result

    def _entries(self, root: Path, tree: str) -> dict[str, tuple[str, str]]:
        result = {}
        for entry in self._git(root, ["ls-tree", "-rz", "--full-tree", tree]).split(b"\0"):
            if not entry:
                continue
            metadata, raw_path = entry.split(b"\t", 1)
            mode, kind, oid = metadata.decode().split()
            try:
                path = _relative(raw_path.decode("utf-8"))
            except UnicodeError as exc:
                raise WorkspaceError("Non-UTF8 paths are unsupported.") from exc
            if mode not in {"100644", "100755"} or kind != "blob":
                raise WorkspaceError("Tracked symlinks and submodules are unsupported in isolated workspaces.")
            result[path] = (mode, oid)
            if len(result) > MAX_FILES:
                raise WorkspaceError("Workspace file count exceeds its limit.")
        return result

    def _blob(self, root: Path, oid: str) -> bytes:
        return self._git(root, ["cat-file", "blob", oid])

    def _dirty(self, root: Path) -> bool:
        # git status/diff against a working tree can execute clean filters. Use
        # raw objects and bytes instead, including index-only/staged changes.
        revision = self._revision(root, "HEAD")
        entries = self._entries(root, revision)
        indexed = {}
        for raw in self._git(root, ["ls-files", "--stage", "-z"]).split(b"\0"):
            if not raw:
                continue
            metadata, name = raw.split(b"\t", 1)
            mode, oid, stage = metadata.decode().split()
            if stage != "0":
                return True
            indexed[_relative(name.decode("utf-8"))] = (mode, oid)
        if indexed != entries:
            return True
        if self._git(root, ["ls-files", "--others", "--exclude-standard", "-z"]):
            return True
        total = 0
        for relative, (mode, oid) in entries.items():
            actual = self._read_file(root, relative)
            if actual is None:
                return True
            data, actual_mode = actual
            total += len(data)
            if total > MAX_BYTES:
                raise WorkspaceError("Workspace exceeds the safe inspection limit.")
            algorithm = hashlib.sha256 if len(oid) == 64 else hashlib.sha1
            actual_oid = algorithm(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
            if oid != actual_oid or mode != actual_mode:
                return True
        return False

    def create_workspace(self, agent_id: str, revision: str = "HEAD", *,
                         dirty_policy: str = "reject", workspace_id: str | None = None) -> dict:
        _identifier(agent_id)
        if dirty_policy not in {"reject", "clean_head"}:
            raise WorkspaceError("Dirty policy must be reject or clean_head.")
        with self._locked():
            identifier = _identifier(workspace_id or "agent_" + uuid4().hex)
            path = self.trees / identifier
            if path.exists() or path.is_symlink() or (self.records / f"{identifier}.json").exists():
                raise WorkspaceError("Workspace identifier or path already exists.")
            source_dirty = self._dirty(self.repository)
            if source_dirty and dirty_policy == "reject":
                raise WorkspaceError("The source workspace is dirty; explicitly select clean_head to omit local changes.")
            base = self._revision(self.repository, revision)
            entries = self._entries(self.repository, base)
            record = dict(workspace_id=identifier, agent_id=agent_id, repository=str(self.repository),
                          path=str(path), base_revision=base, source_dirty=source_dirty,
                          dirty_policy=dirty_policy, state="creating", created_at=time.time(), branch=None)
            self._save(self.records, identifier, record)
            try:
                self._git(self.repository, ["worktree", "add", "--detach", "--no-checkout", str(path), base])
                # No checkout: repository clean/smudge filters must never run.
                total = 0
                for relative, (mode, oid) in entries.items():
                    data = self._blob(self.repository, oid)
                    total += len(data)
                    if total > MAX_BYTES:
                        raise WorkspaceError("Workspace contents exceed the safe materialization limit.")
                    target = path / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with target.open("xb") as stream:
                        stream.write(data)
                    target.chmod(0o755 if mode == "100755" else 0o644)
                self._git(path, ["read-tree", base])
                record["state"] = "ready"
                record["git_directory"] = str(Path(self._git(path, ["rev-parse", "--absolute-git-dir"]).decode().strip()).resolve())
                self._save(self.records, identifier, record)
                return deepcopy(record)
            except BaseException:
                record["state"] = "creation_failed_retained"
                self._save(self.records, identifier, record)
                # Do not guess whether a cancelled creation produced user work.
                raise

    def list_workspaces(self) -> list[dict]:
        with self._locked():
            return [self._load(self.records, path.stem) for path in sorted(self.records.glob("*.json"))]

    def inspect_status(self, workspace_id: str) -> dict:
        with self._locked():
            record = self._record(workspace_id)
            path = Path(record["path"])
            exists = path.is_dir()
            dirty = self._dirty(path) if exists else False
            head = self._revision(path, "HEAD") if exists else None
            ignored = bool(self._git(path, ["ls-files", "--others", "--ignored", "--exclude-standard", "-z"])) if exists else False
            record.update(exists=exists, dirty=dirty, workspace_revision=head, ignored_files=ignored,
                          cleanup_eligible=exists and not dirty and not ignored and head == record["base_revision"],
                          execution_boundary="host", isolation="git_worktree")
            return record

    def _read_file(self, root: Path, relative: str) -> tuple[bytes, str] | None:
        relative = _relative(relative)
        path = root / relative
        _no_symlinks(path)
        parents = []
        descriptor = None
        try:
            if os.name != "nt" and os.open in os.supports_dir_fd:
                parent = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                parents.append(parent)
                for part in relative.split("/")[:-1]:
                    parent = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                    parents.append(parent)
                descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
            else:
                descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_BYTES:
                raise WorkspaceError("Only bounded independent regular files can be collected.")
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                data = stream.read(MAX_BYTES + 1)
            after = os.fstat(descriptor)
            if (info.st_size, info.st_mtime_ns, info.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise WorkspaceConflict("File changed while being collected; retry inspection after the editor stops.")
            if len(data) > MAX_BYTES:
                raise WorkspaceError("File grew beyond its collection limit.")
            return data, "100755" if info.st_mode & 0o111 else "100644"
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise WorkspaceError("File access changed or could not remain within the workspace.") from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
            for parent in reversed(parents):
                os.close(parent)

    @contextmanager
    def _index(self, root: Path, revision: str | None = None, *, extra: dict | None = None):
        descriptor, name = tempfile.mkstemp(prefix="index-", dir=self.storage_root)
        os.close(descriptor)
        os.unlink(name)
        env = {**(extra or {}), "GIT_INDEX_FILE": name}
        try:
            self._git(root, ["read-tree", revision] if revision else ["read-tree", "--empty"], extra=env)
            yield env
        finally:
            for candidate in (name, name + ".lock"):
                if os.path.exists(candidate):
                    os.unlink(candidate)

    def _collect(self, record: dict) -> dict:
        root = Path(record["path"])
        head = self._revision(root, "HEAD")
        names = self._git(root, ["ls-files", "--cached", "--others", "--exclude-standard", "-z"]).split(b"\0")
        paths = sorted({_relative(raw.decode("utf-8")) for raw in names if raw})
        if len(paths) > MAX_FILES:
            raise WorkspaceError("Workspace file count exceeds its limit.")
        total = 0
        with self._index(root) as env:
            updates = bytearray()
            for relative in paths:
                item = self._read_file(root, relative)
                if item is None:
                    continue
                data, mode = item
                total += len(data)
                if total > MAX_BYTES:
                    raise WorkspaceError("Workspace exceeds its patch collection limit.")
                oid = self._git(root, ["hash-object", "-w", "--stdin"], data=data, limit=1024).decode().strip()
                updates.extend(f"{mode} {oid}\t{relative}".encode() + b"\0")
            if updates:
                self._git(root, ["update-index", "-z", "--index-info"], data=bytes(updates), extra=env)
            tree = self._git(root, ["write-tree"], extra=env, limit=1024).decode().strip()
        patch = self._git(root, ["diff", "--binary", "--full-index", "--no-ext-diff", "--no-textconv",
                                  record["base_revision"], tree, "--"])
        changed = self._git(root, ["diff", "--name-only", "-z", "--no-ext-diff", "--no-textconv",
                                    record["base_revision"], tree, "--"])
        return dict(workspace_id=record["workspace_id"], agent_id=record["agent_id"],
                    base_revision=record["base_revision"], workspace_revision=head, tree=tree,
                    patch=patch.decode("utf-8"), digest=hashlib.sha256(patch).hexdigest(),
                    paths=[raw.decode("utf-8") for raw in changed.split(b"\0") if raw],
                    ignored_files_excluded=True)

    def collect_patch(self, workspace_id: str) -> dict:
        with self._locked():
            record = self._record(workspace_id)
            result = self._collect(record)
            record.update(last_patch=result, state="changes_collected")
            self._save(self.records, workspace_id, record)
            return result

    def overlapping_paths(self, workspace_ids: list[str]) -> list[str]:
        with self._locked():
            seen, overlaps = set(), set()
            for identifier in workspace_ids:
                paths = set(self._collect(self._record(identifier))["paths"])
                overlaps |= seen & paths
                seen |= paths
            return sorted(overlaps)

    def _overlaps(self, workspace_id: str, paths: list[str]) -> dict:
        overlaps = {}
        for file in sorted(self.records.glob("*.json")):
            if file.stem == workspace_id:
                continue
            record = self._record(file.stem)
            if record["state"] in {"destroyed", "missing_retained"} or not Path(record["path"]).exists():
                continue
            patch = self._collect(record)
            if record["state"] == "integrated" and patch["digest"] == record.get("integrated_digest"):
                continue
            common = sorted(set(paths) & set(patch["paths"]))
            if common:
                overlaps[file.stem] = {"paths": common, "digest": patch["digest"],
                                       "workspace_revision": patch["workspace_revision"]}
        return overlaps

    def _merge_tree(self, target_revision: str, patch: dict) -> tuple[str | None, list[str]]:
        if not patch["patch"]:
            return self._git(self.repository, ["rev-parse", f"{target_revision}^{{tree}}"], limit=1024).decode().strip(), []
        # A separate temporary Git config prevents custom merge drivers from
        # being selected by repository-controlled .gitattributes. Objects remain
        # in the source object store so proposals survive temporary-index cleanup.
        with tempfile.TemporaryDirectory(prefix="merge-", dir=self.storage_root) as temporary:
            root = Path(temporary)
            self._git(root, ["init", "--quiet"], extra={"GIT_DIR": str(root / ".git")})
            # info/attributes has higher priority than tracked attributes. Force
            # ordinary conflict-reporting text merge, including when a repo asks
            # for union/ours or its own permissive merge driver.
            (root / ".git" / "info" / "attributes").write_text("* merge=text\n", encoding="utf-8")
            isolated = {"GIT_DIR": str(root / ".git"),
                        "GIT_OBJECT_DIRECTORY": str(self.common_dir / "objects")}
            with self._index(root, target_revision, extra=isolated) as env:
                try:
                    self._git(root, ["apply", "--cached", "--3way", "--whitespace=nowarn"],
                              data=patch["patch"].encode(), extra=env)
                except WorkspaceError:
                    conflicts = self._git(root, ["diff", "--cached", "--name-only", "--diff-filter=U", "-z"], extra=env)
                    return None, [p.decode() for p in conflicts.split(b"\0") if p] or patch["paths"]
                tree = self._git(root, ["write-tree"], extra=env, limit=1024).decode().strip()
                return tree, []

    def propose_merge(self, workspace_id: str) -> dict:
        with self._locked():
            record = self._record(workspace_id)
            patch = self._collect(record)
            target = self._revision(self.repository, "HEAD")
            merged_tree, merge_conflicts = self._merge_tree(target, patch)
            overlaps = self._overlaps(workspace_id, patch["paths"])
            conflicts = sorted(set(merge_conflicts) | {p for item in overlaps.values() for p in item["paths"]})
            identifier = "proposal_" + uuid4().hex
            proposal = dict(**patch, proposal_id=identifier, repository=str(self.repository),
                            target_revision=target, merged_tree=merged_tree, conflicts=conflicts,
                            merge_conflicts=merge_conflicts, overlapping_workspaces=overlaps, overlap_resolutions={},
                            reviewed=False, reviewer=None, state="conflict" if conflicts else "awaiting_review",
                            created_at=time.time(), requires_approval=True,
                            target_dirty=self._dirty(self.repository))
            self._save(self.proposals, identifier, proposal)
            record.update(proposal_id=identifier, state=proposal["state"], last_patch=patch)
            self._save(self.records, workspace_id, record)
            return deepcopy(proposal)

    def resolve_overlaps(self, proposal_id: str, resolutions: dict[str, str], reviewer: str) -> dict:
        """Record a trusted reviewer's explicit resolution of every overlap.

        Work in both worktrees is retained. The caller must have inspected both
        patches and, if needed, edited the worker result into the resolved form
        before creating this proposal. This only acknowledges the bounded
        competing versions; it never silently selects or deletes either result.
        """
        with self._locked():
            proposal = self._load(self.proposals, proposal_id)
            overlaps = self._overlaps(proposal["workspace_id"], proposal["paths"])
            if (not isinstance(reviewer, str) or not reviewer.strip() or reviewer == proposal["agent_id"]
                    or not isinstance(resolutions, dict) or set(resolutions) != set(overlaps)
                    or any(not isinstance(value, str) or not value.strip() or len(value) > 4096
                           for value in resolutions.values())):
                raise WorkspaceError("Every overlapping workspace requires a trusted explicit resolution.")
            if overlaps != proposal.get("overlapping_workspaces", {}):
                raise WorkspaceConflict("Competing work changed; regenerate the proposal before resolution.")
            source = self._collect(self._record(proposal["workspace_id"]))
            if source["digest"] != proposal["digest"]:
                raise WorkspaceConflict("Agent work changed; regenerate the proposal before resolution.")
            proposal.update(overlap_resolutions={identifier: dict(**item, resolution=resolutions[identifier], reviewer=reviewer)
                                                 for identifier, item in overlaps.items()},
                            conflicts=proposal.get("merge_conflicts", []), reviewed=False, reviewer=None,
                            state="conflict" if proposal.get("merge_conflicts") else "awaiting_review")
            self._save(self.proposals, proposal_id, proposal)
            return deepcopy(proposal)

    def mark_reviewed(self, proposal_id: str, reviewer: str) -> dict:
        if not isinstance(reviewer, str) or not reviewer.strip() or len(reviewer) > 256:
            raise WorkspaceError("A trusted reviewer identity is required.")
        with self._locked():
            proposal = self._load(self.proposals, proposal_id)
            if proposal["conflicts"]:
                raise WorkspaceConflict("Resolve conflicts in an agent worktree and create a new proposal.")
            if reviewer == proposal["agent_id"]:
                raise WorkspaceError("The worker cannot approve its own review.")
            proposal.update(reviewed=True, reviewer=reviewer, reviewed_at=time.time(), state="reviewed")
            self._save(self.proposals, proposal_id, proposal)
            return deepcopy(proposal)

    def get_proposal(self, proposal_id: str) -> dict:
        with self._locked():
            return self._load(self.proposals, proposal_id)

    def _write_content(self, relative: str, data: bytes | None, mode: str | None) -> None:
        # Dirfd + O_NOFOLLOW prevents a generated symlink from redirecting writes.
        if os.name == "nt" or os.open not in os.supports_dir_fd or os.rename not in os.supports_dir_fd:
            raise WorkspaceError("Safe integration requires POSIX dirfd support on this platform.")
        parts = _relative(relative).split("/")
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        descriptor = os.open(self.repository, flags)
        try:
            for part in parts[:-1]:
                try:
                    child = os.open(part, flags, dir_fd=descriptor)
                except FileNotFoundError:
                    if data is None:
                        return
                    os.mkdir(part, 0o755, dir_fd=descriptor)
                    child = os.open(part, flags, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
            leaf = parts[-1]
            try:
                current = os.stat(leaf, dir_fd=descriptor, follow_symlinks=False)
                if not stat.S_ISREG(current.st_mode):
                    raise WorkspaceError("Integration path is no longer a regular file.")
            except FileNotFoundError:
                pass
            if data is None:
                try:
                    os.unlink(leaf, dir_fd=descriptor)
                except FileNotFoundError:
                    pass
                return
            temporary = ".firik-merge-" + uuid4().hex
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o755 if mode == "100755" else 0o644, dir_fd=descriptor)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, leaf, src_dir_fd=descriptor, dst_dir_fd=descriptor)
            finally:
                try:
                    os.unlink(temporary, dir_fd=descriptor)
                except FileNotFoundError:
                    pass
        finally:
            os.close(descriptor)

    def integrate(self, proposal_id: str, *, approved: bool = False) -> dict:
        with self._locked():
            proposal = self._load(self.proposals, proposal_id)
            if approved is not True or proposal.get("reviewed") is not True:
                raise WorkspaceError("Integration requires trusted review and explicit permission approval.")
            if proposal["state"] != "reviewed" or proposal["conflicts"]:
                raise WorkspaceConflict("Proposal is not eligible for integration.")
            if self._dirty(self.repository):
                raise WorkspaceConflict("Target must be clean; local user work is preserved.")
            target = self._revision(self.repository, "HEAD")
            if target != proposal["target_revision"]:
                raise WorkspaceConflict("Target revision changed; regenerate and review the proposal.")
            source = self._collect(self._record(proposal["workspace_id"]))
            if source["digest"] != proposal["digest"] or source["workspace_revision"] != proposal["workspace_revision"]:
                raise WorkspaceConflict("Agent work changed after review.")
            overlaps = self._overlaps(proposal["workspace_id"], source["paths"])
            resolved = proposal.get("overlap_resolutions", {})
            for identifier, competing in overlaps.items():
                if identifier not in resolved or any(resolved[identifier].get(key) != value for key, value in competing.items()):
                    raise WorkspaceConflict("Overlapping agent edits require explicit conflict resolution and renewed review.")
            tree, conflicts = self._merge_tree(target, source)
            if conflicts or tree != proposal["merged_tree"]:
                raise WorkspaceConflict("Three-way merge result changed after review.")
            old_entries, new_entries = self._entries(self.repository, target), self._entries(self.repository, tree)
            changed = sorted(p for p in old_entries.keys() | new_entries.keys() if old_entries.get(p) != new_entries.get(p))
            backups, desired = {}, {}
            for relative in changed:
                actual = self._read_file(self.repository, relative)
                expected = old_entries.get(relative)
                if expected:
                    expected_content = self._blob(self.repository, expected[1]), expected[0]
                    if actual != expected_content:
                        raise WorkspaceConflict("Target content changed or checkout transforms prevent a safe merge.")
                elif actual is not None:
                    raise WorkspaceConflict("An ignored or untracked target file would be overwritten.")
                backups[relative] = actual
                item = new_entries.get(relative)
                desired[relative] = (self._blob(self.repository, item[1]), item[0]) if item else None
            # Retain proposal and backup object identifiers before the first write.
            proposal.update(state="integrating", integration_paths=changed)
            self._save(self.proposals, proposal_id, proposal)
            written = []
            try:
                for relative in changed:
                    if self._read_file(self.repository, relative) != backups[relative]:
                        raise WorkspaceConflict("Concurrent target edit detected before writing.")
                    item = desired[relative]
                    self._write_content(relative, item[0] if item else None, item[1] if item else None)
                    written.append(relative)
            except BaseException:
                rollback_pending = []
                for relative in reversed(written):
                    try:
                        if self._read_file(self.repository, relative) != desired[relative]:
                            rollback_pending.append(relative)
                            continue
                        item = backups[relative]
                        self._write_content(relative, item[0] if item else None, item[1] if item else None)
                    except BaseException:
                        rollback_pending.append(relative)
                proposal.update(state="rollback_pending" if rollback_pending else "rolled_back",
                                rollback_pending=rollback_pending)
                self._save(self.proposals, proposal_id, proposal)
                raise
            proposal.update(state="integrated", integrated_at=time.time(),
                            verification_required=True, staged=False, committed=False)
            self._save(self.proposals, proposal_id, proposal)
            record = self._record(proposal["workspace_id"])
            record.update(state="integrated", integrated_proposal_id=proposal_id,
                          integrated_digest=proposal["digest"])
            self._save(self.records, record["workspace_id"], record)
            return deepcopy(proposal)

    def destroy_workspace(self, workspace_id: str) -> dict:
        with self._locked():
            record = self._record(workspace_id)
            path = Path(record["path"])
            if not path.exists():
                record["state"] = "missing_retained"
                self._save(self.records, workspace_id, record)
                return record
            if (self._dirty(path) or self._revision(path, "HEAD") != record["base_revision"]
                    or self._git(path, ["ls-files", "--others", "--ignored", "--exclude-standard", "-z"])):
                raise WorkspaceError("Workspace contains agent work or ignored artifacts; cleanup refused and data retained.")
            filter_keys = self._git(path, ["config", "--null", "--name-only", "--get-regexp",
                                          r"^filter\..*\.(clean|smudge|process|required)$"], check=False)
            overrides = []
            for key in filter_keys.decode("utf-8").split("\0"):
                if key:
                    overrides += ["-c", key + ("=false" if key.endswith(".required") else "=")]
            self._git(self.repository, [*overrides, "worktree", "remove", str(path)])
            record.update(state="destroyed", destroyed_at=time.time())
            self._save(self.records, workspace_id, record)
            return deepcopy(record)

    def reconcile(self) -> list[dict]:
        """Inspect crash leftovers without deleting work or retrying integration."""
        with self._locked():
            results = []
            for file in sorted(self.records.glob("*.json")):
                record = self._record(file.stem)
                if not Path(record["path"]).exists() and record["state"] != "destroyed":
                    record["state"] = "missing_retained"
                elif record["state"] == "creating":
                    record["state"] = "creation_interrupted_retained"
                self._save(self.records, file.stem, record)
                results.append(record)
            for file in self.proposals.glob("*.json"):
                proposal = self._load(self.proposals, file.stem)
                if proposal["state"] == "integrating":
                    proposal["state"] = "integration_interrupted_inspection_required"
                    self._save(self.proposals, file.stem, proposal)
            return results
