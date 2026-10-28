"""Git integration service for Firik Agent Projects."""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from firik_agent.projects.git_insights import GitInsightsMixin
from firik_agent.projects.git_operations import GitOperationsMixin


class GitService(GitInsightsMixin, GitOperationsMixin):
    """Manages Git queries, commits, branches, and diff analysis for a project directory."""

    @staticmethod
    def is_git_repo(path: str | Path) -> bool:
        code, output, _ = GitService._run_git(path, ["rev-parse", "--is-inside-work-tree"])
        return code == 0 and output.strip() == "true"

    @staticmethod
    def _run_git(cwd: str | Path, args: List[str], timeout: float = 15.0) -> Tuple[int, str, str]:
        try:
            environment = dict(os.environ)
            for key in list(environment):
                if key in {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
                           "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_COMMON_DIR", "GIT_NAMESPACE", "GIT_PREFIX"} or key.startswith("GIT_CONFIG_"):
                    environment.pop(key, None)
            environment.update(GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0", GCM_INTERACTIVE="never")
            # Spool output to disk so a large diff cannot exhaust GUI memory.
            # Keep leading spaces and NUL separators intact for Git's machine formats.
            with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
                res = subprocess.run(
                    ["git", "--no-pager", "-c", "color.ui=false", "-c", "core.quotepath=false", *args],
                    cwd=str(cwd), stdout=out, stderr=err, stdin=subprocess.DEVNULL, timeout=timeout,
                    env=environment,
                )
                cap = 16 * 1024 * 1024
                out.seek(0)
                err.seek(0)
                stdout, stderr = out.read(cap + 1), err.read(cap + 1)
                if len(stdout) > cap or len(stderr) > cap:
                    return 1, "", "Git çıktısı 16 MB sınırını aştı. Tarih, dosya veya commit aralığını daraltın."
                return res.returncode, stdout.decode("utf-8", errors="replace"), stderr.decode("utf-8", errors="replace").strip()
        except Exception as e:
            return 1, "", str(e)

    @classmethod
    def get_current_branch(cls, path: str | Path) -> str:
        code, stdout, _ = cls._run_git(path, ["symbolic-ref", "--quiet", "--short", "HEAD"])
        if code == 0:
            return stdout.strip()
        code, stdout, _ = cls._run_git(path, ["rev-parse", "--short", "HEAD"])
        return f"HEAD · {stdout.strip()}" if code == 0 else ""

    @classmethod
    def get_remote_url(cls, path: str | Path) -> str:
        code, stdout, _ = cls._run_git(path, ["config", "--get", "remote.origin.url"])
        return stdout.strip() if code == 0 else ""

    @classmethod
    def get_branches(cls, path: str | Path) -> List[str]:
        code, stdout, _ = cls._run_git(path, ["branch", "--format=%(refname:short)"])
        if code == 0 and stdout:
            return [b.strip() for b in stdout.splitlines() if b.strip()]
        return []

    @classmethod
    def get_status(cls, path: str | Path) -> Dict[str, Any]:
        """Returns structured status of modified, staged, and untracked files."""
        code, stdout, error = cls._run_git(path, ["status", "--porcelain=v1", "-z", "--untracked-files=all"])
        staged = []
        modified = []
        untracked = []
        conflicts = []
        files = []
        records = iter(stdout.split("\x00"))
        if code == 0:
            for record in records:
                if len(record) < 3:
                    continue
                index_st, work_st = record[:2]
                filepath = record[3:]
                original = next(records, "") if index_st in "RC" or work_st in "RC" else ""
                entry = {"path": filepath, "index": index_st, "worktree": work_st, "original_path": original}
                files.append(entry)
                if record[:2] in {"DD", "AU", "UD", "UA", "DU", "AA", "UU"}:
                    conflicts.append(dict(entry, status=record[:2]))
                elif record[:2] == "??":
                    untracked.append(dict(entry, status="?"))
                else:
                    if index_st in "MADRCT":
                        staged.append(dict(entry, status=index_st))
                    if work_st not in " ?!":
                        modified.append(dict(entry, status=work_st))

        ahead_behind = cls.get_ahead_behind(path)

        return {
            "branch": cls.get_current_branch(path),
            "staged": staged,
            "modified": modified,
            "untracked": untracked,
            "conflicts": conflicts,
            "files": files,
            "error": error if code else "",
            "upstream": ahead_behind.get("upstream", ""),
            "upstream_known": ahead_behind.get("known", False),
            "ahead": ahead_behind.get("ahead", 0),
            "behind": ahead_behind.get("behind", 0),
            "clean": code == 0 and not files,
        }

    @classmethod
    def get_ahead_behind(cls, path: str | Path) -> Dict[str, Any]:
        upstream_code, upstream, _ = cls._run_git(path, ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"])
        code, stdout, _ = cls._run_git(path, ["rev-list", "--left-right", "--count", "@{upstream}...HEAD"])
        if code == 0 and stdout:
            parts = stdout.split()
            if len(parts) == 2:
                try:
                    return {"behind": int(parts[0]), "ahead": int(parts[1]), "upstream": upstream.strip(), "known": upstream_code == 0}
                except ValueError:
                    pass
        return {"ahead": 0, "behind": 0, "upstream": "", "known": False}

    @classmethod
    def get_recent_commits(cls, path: str | Path, limit: int = 15) -> List[Dict[str, Any]]:
        fmt = "%H%x1f%s%x1f%an%x1f%ad%x1f%h"
        code, stdout, _ = cls._run_git(path, ["log", f"-{limit}", f"--pretty=format:{fmt}", "--date=relative"])
        commits = []
        if code == 0 and stdout:
            for line in stdout.splitlines():
                parts = line.split("\x1f")
                if len(parts) >= 5:
                    commits.append({
                        "hash": parts[0],
                        "short_hash": parts[4],
                        "message": parts[1],
                        "author": parts[2],
                        "relative_date": parts[3],
                    })
        return commits

    @classmethod
    def get_diff(cls, path: str | Path, staged: bool = False, max_lines: int = 500, file_path: str = "", strict: bool = False,
                 file_paths: Optional[List[str]] = None) -> str:
        args = ["diff", "--no-ext-diff", "--no-textconv", "--cached"] if staged else ["diff", "--no-ext-diff", "--no-textconv"]
        if file_paths is not None:
            if not file_paths:
                return ""
            # Paths are relative to the selected workspace (which may itself be
            # a subdirectory of a repository). Non-Git projects retain the
            # historical empty-diff behavior through _run_git below.
            root = Path(path).expanduser().resolve()
            relatives = [cls._insights_file(root, entry) for entry in dict.fromkeys(file_paths[:64])]
            args.extend(["--", *[f":(literal){relative}" for relative in relatives]])
            path = root
        elif file_path:
            root = cls._insights_root(path)
            relative = cls._insights_file(root, file_path)
            args.extend(["--", f":(literal){relative}"])
            path = root
        code, stdout, error = cls._run_git(path, args)
        if code and strict:
            raise RuntimeError(error or "Git farkı okunamadı.")
        if code == 0 and stdout:
            lines = stdout.splitlines()
            if len(lines) > max_lines:
                return "\n".join(lines[:max_lines]) + f"\n... (Diff truncated at {max_lines} lines)"
            return stdout
        return ""

    @classmethod
    def get_commit_diff(cls, path: str | Path, commit_hash: str) -> str:
        root = cls._insights_root(path)
        commit = cls._insights_ref(root, commit_hash)
        code, stdout, _ = cls._run_git(root, ["show", "--no-ext-diff", "--no-textconv", "--stat", commit, "--"])
        return stdout if code == 0 else ""

    @classmethod
    def find_tasks_in_commits(cls, commits: List[Dict[str, Any]]) -> Dict[str, List[str]]:
        """Maps detected task keys (e.g. TASK-123 or #123) to commit hashes."""
        mapping: Dict[str, List[str]] = {}
        pattern = re.compile(r"(?:TASK|FIRIK)[-_](\d+)|#(\d+)", re.IGNORECASE)
        for c in commits:
            msg = c.get("message", "")
            matches = pattern.findall(msg)
            for m in matches:
                key = m[0] or m[1]
                if key:
                    t_key = f"TASK-{key}"
                    mapping.setdefault(t_key, []).append(c["short_hash"])
        return mapping

    @classmethod
    def clone_repository(cls, repo_url: str, target_dir: str | Path) -> Tuple[bool, str]:
        target = Path(target_dir)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            res = subprocess.run(
                ["git", "clone", repo_url, str(target)],
                capture_output=True,
                text=True,
                timeout=120.0,
            )
            if res.returncode == 0:
                return True, f"Repository cloned successfully to {target}"
            return False, res.stderr or "Git clone failed"
        except Exception as e:
            return False, str(e)
