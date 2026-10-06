"""Read-only Git intelligence, backed by the repository's actual history.

This mixin deliberately uses argument vectors and NUL-separated machine formats.
It never changes Git configuration and never executes configured diff/textconv
helpers. GitService supplies ``_run_git(cwd, args, timeout)``.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
from typing import Any


class GitInsightsMixin:
    """Bounded queries for history, attribution, branches and recovery records."""

    _INSIGHTS_FORMAT = "%H%x00%h%x00%P%x00%s%x00%aN%x00%aE%x00%aI%x00%D%x00%cI"
    _INSIGHTS_HASH = re.compile(r"^[0-9a-f]{40,64}$")
    _INSIGHTS_PATCH_CHARS = 250_000
    _INSIGHTS_PATCH_LINES = 4_000

    @classmethod
    def _insights_run(cls, path: str | Path, args: list[str], timeout: float = 20.0) -> str:
        code, stdout, stderr = cls._run_git(
            path, ["--no-pager", "-c", "color.ui=false", "-c", "core.quotepath=false", *args], timeout
        )
        if code:
            raise RuntimeError((stderr or "Git query failed").strip()[:2000])
        return stdout

    @classmethod
    def _insights_root(cls, path: str | Path) -> Path:
        location = Path(path).expanduser().resolve()
        if not location.is_dir():
            raise ValueError("Repository directory does not exist")
        code, stdout, stderr = cls._run_git(location, ["rev-parse", "--show-toplevel"])
        if code == 0:
            return Path(stdout.removesuffix("\n")).resolve()
        # Bare repositories support history, refs and attribution, but no worktree.
        code, bare, _ = cls._run_git(location, ["rev-parse", "--is-bare-repository"])
        if code == 0 and bare.strip() == "true":
            return location
        raise ValueError((stderr or "This directory is not a Git repository").strip()[:1000])

    @staticmethod
    def _insights_limit(value: int, maximum: int = 2000, minimum: int = 1) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("Limit must be an integer")
        if not minimum <= value <= maximum:
            raise ValueError(f"Limit must be between {minimum} and {maximum}")
        return value

    @staticmethod
    def _insights_text(value: str, label: str, maximum: int = 1000) -> str:
        if not isinstance(value, str) or len(value) > maximum or "\0" in value or "\n" in value or "\r" in value:
            raise ValueError(f"Invalid {label}")
        return value

    @classmethod
    def _insights_ref(cls, path: Path, ref: str, *, allow_unborn: bool = False) -> str:
        ref = cls._insights_text(ref, "Git reference", 512)
        if not ref or ref.startswith("-"):
            raise ValueError("A valid Git reference is required")
        code, stdout, stderr = cls._run_git(path, ["rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}"])
        if code:
            if allow_unborn and ref == "HEAD" and cls._insights_unborn(path):
                return ""
            raise ValueError((stderr or f"Unknown Git reference: {ref}").strip()[:1000])
        result = stdout.strip()
        if not cls._INSIGHTS_HASH.fullmatch(result):
            raise ValueError("Reference did not resolve to a commit")
        return result

    @classmethod
    def _insights_unborn(cls, path: Path) -> bool:
        code, _, _ = cls._run_git(path, ["rev-parse", "--verify", "HEAD"])
        if code == 0:
            return False
        code, branch, _ = cls._run_git(path, ["symbolic-ref", "-q", "HEAD"])
        if code:
            return False
        # No branch ref means the symbolic HEAD is genuinely unborn, not a
        # missing/damaged object from an existing branch.
        code, _, _ = cls._run_git(path, ["show-ref", "--verify", "--quiet", branch.strip()])
        return code == 1

    @classmethod
    def _insights_file(cls, root: Path, file_path: str) -> str:
        if not isinstance(file_path, str) or len(file_path) > 4096 or "\0" in file_path:
            raise ValueError("Invalid file path")
        if not file_path:
            raise ValueError("A file path is required")
        target = Path(file_path).expanduser()
        target = (target if target.is_absolute() else root / target).resolve()
        try:
            relative = target.relative_to(root)
        except ValueError as exc:
            raise ValueError("File path must stay inside the repository") from exc
        if not relative.parts or relative.parts[0] == ".git":
            raise ValueError("Choose a repository file")
        # --literal-pathspecs is also used for every path query: ':' and glob
        # characters in real filenames must not become Git pathspec syntax.
        return relative.as_posix()

    @classmethod
    def _insights_history_parse(cls, stdout: str, *, numstat: bool = False) -> list[dict[str, Any]]:
        tokens = stdout.split("\0")
        rows: list[dict[str, Any]] = []
        pos = 0
        while pos < len(tokens):
            token = tokens[pos]
            if not token:
                pos += 1
                continue
            if not cls._INSIGHTS_HASH.fullmatch(token) or pos + 8 >= len(tokens):
                raise RuntimeError("Incomplete Git history output")
            fields = tokens[pos:pos + 9]
            row: dict[str, Any] = dict(zip(
                ("hash", "short_hash", "parents", "message", "author", "email", "date", "refs", "committed_date"), fields
            ))
            row["parents"] = fields[2].split()
            pos += 9
            if numstat:
                row["files"] = []
                while pos < len(tokens) and not cls._INSIGHTS_HASH.fullmatch(tokens[pos]):
                    stat = tokens[pos]
                    pos += 1
                    if not stat or stat == "\n":
                        continue
                    pieces = stat.lstrip("\n").split("\t", 2)
                    if len(pieces) != 3:
                        raise RuntimeError("Incomplete Git file statistics")
                    added, removed, filename = pieces
                    original = ""
                    if not filename:
                        if pos + 1 >= len(tokens):
                            raise RuntimeError("Incomplete Git rename statistics")
                        original, filename = tokens[pos:pos + 2]
                        pos += 2
                    binary = added == "-" or removed == "-"
                    row["files"].append({
                        "path": filename, "old_path": original,
                        "added": 0 if binary else int(added),
                        "removed": 0 if binary else int(removed), "binary": binary,
                    })
            rows.append(row)
        return rows

    @classmethod
    def _insights_patch(cls, patch: str) -> tuple[str, bool]:
        truncated = len(patch) > cls._INSIGHTS_PATCH_CHARS
        patch = patch[:cls._INSIGHTS_PATCH_CHARS]
        lines = patch.splitlines(keepends=True)
        if len(lines) > cls._INSIGHTS_PATCH_LINES:
            lines = lines[:cls._INSIGHTS_PATCH_LINES]
            truncated = True
        patch = "".join(lines)
        if truncated:
            patch += "\n… Patch preview truncated. Narrow the comparison to inspect more.\n"
        return patch, truncated

    @classmethod
    def get_repository_info(cls, path: str | Path) -> dict[str, Any]:
        root = cls._insights_root(path)
        code, branch, _ = cls._run_git(root, ["symbolic-ref", "--quiet", "--short", "HEAD"])
        detached = code != 0
        code, head, _ = cls._run_git(root, ["rev-parse", "--verify", "HEAD"])
        head = head.strip() if code == 0 else ""
        bare = cls._insights_run(root, ["rev-parse", "--is-bare-repository"]).strip() == "true"
        shallow = cls._insights_run(root, ["rev-parse", "--is-shallow-repository"]).strip() == "true"
        return {
            "root": str(root), "name": root.name, "branch": branch.strip() if not detached else "Detached HEAD",
            "head": head, "detached": detached, "unborn": not head and cls._insights_unborn(root),
            "bare": bare, "shallow": shallow,
            "git_version": cls._insights_run(root, ["--version"]).strip(),
            "remotes": cls._insights_run(root, ["remote"]).splitlines(),
        }

    @classmethod
    def _insights_status(cls, path: Path, info: dict[str, Any]) -> dict[str, Any]:
        status: dict[str, Any] = {
            "branch": info["branch"], "staged": [], "modified": [], "untracked": [], "conflicts": [],
            "ahead": 0, "behind": 0, "clean": True,
        }
        if info["bare"]:
            return status
        shared_status = getattr(cls, "get_status", None)
        if callable(shared_status):
            status = shared_status(path)
            if status.get("error"):
                raise RuntimeError(status["error"])
            return status
        data = cls._insights_run(path, ["status", "--porcelain=v1", "-z", "--untracked-files=normal"])
        tokens, pos = data.split("\0"), 0
        while pos < len(tokens):
            entry = tokens[pos]
            pos += 1
            if not entry:
                continue
            if len(entry) < 4:
                raise RuntimeError("Incomplete Git status output")
            x, y, filename = entry[0], entry[1], entry[3:]
            row: dict[str, Any] = {"path": filename, "status": x + y}
            if x in "RC" or y in "RC":
                if pos >= len(tokens):
                    raise RuntimeError("Incomplete Git rename status")
                row["old_path"] = tokens[pos]
                pos += 1
            if x + y in ("DD", "AU", "UD", "UA", "DU", "AA", "UU"):
                status["conflicts"].append(row.copy())
            if x + y == "??":
                status["untracked"].append({**row, "status": "?"})
            else:
                if x != " ":
                    status["staged"].append({**row, "status": x})
                if y != " ":
                    status["modified"].append({**row, "status": y})
        code, counts, _ = cls._run_git(path, ["rev-list", "--left-right", "--count", "@{upstream}...HEAD"])
        if code == 0:
            behind, ahead = counts.split()
            status.update(ahead=int(ahead), behind=int(behind))
        status["clean"] = not any(status[key] for key in ("staged", "modified", "untracked", "conflicts"))
        return status

    @classmethod
    def get_dashboard(cls, path: str | Path, days: int = 90, ref: str = "HEAD", limit: int = 2000) -> dict[str, Any]:
        days = cls._insights_limit(days, 3650)
        limit = cls._insights_limit(limit, 5000)
        info = cls.get_repository_info(path)
        root = Path(info["root"])
        target = cls._insights_ref(root, ref, allow_unborn=True)
        today = datetime.now(timezone.utc).date()
        since = (today - timedelta(days=days - 1)).isoformat()
        commits = []
        if target:
            output = cls._insights_run(root, [
                "log", "-z", f"--max-count={limit + 1}", f"--since={since}T00:00:00Z", f"--until={today.isoformat()}T23:59:59Z",
                "--numstat", "--find-renames", "--no-ext-diff", "--no-textconv",
                f"--format={cls._INSIGHTS_FORMAT}", target, "--",
            ], 30.0)
            commits = cls._insights_history_parse(output, numstat=True)
        truncated = len(commits) > limit
        commits = commits[:limit]
        authors: dict[tuple[str, str], dict[str, Any]] = {}
        files: dict[str, dict[str, Any]] = {}
        activity: dict[str, int] = defaultdict(int)
        added = removed = binary_changes = merge_commits = 0
        for commit in commits:
            key = (commit["author"], commit["email"].lower())
            contributor = authors.setdefault(key, {
                "author": commit["author"], "email": commit["email"], "commits": 0,
                "added": 0, "removed": 0, "last_date": commit["committed_date"],
            })
            contributor["commits"] += 1
            activity_date = datetime.fromisoformat(commit["committed_date"].replace("Z", "+00:00")).astimezone(timezone.utc).date().isoformat()
            activity[activity_date] += 1
            merge_commits += int(len(commit["parents"]) > 1)
            for change in commit["files"]:
                hotspot = files.setdefault(change["path"], {
                    "path": change["path"], "commits": 0, "added": 0, "removed": 0,
                    "last_author": commit["author"], "last_date": commit["committed_date"],
                })
                hotspot["commits"] += 1
                hotspot["added"] += change["added"]
                hotspot["removed"] += change["removed"]
                contributor["added"] += change["added"]
                contributor["removed"] += change["removed"]
                added += change["added"]
                removed += change["removed"]
                binary_changes += int(change["binary"])
        for delta in range(days):
            activity.setdefault((today - timedelta(days=delta)).isoformat(), 0)
        return {
            "repository": info, "status": cls._insights_status(root, info),
            "summary": {
                "contributors": len(authors), "commits": len(commits), "added": added, "removed": removed,
                "files": len(files), "days": days, "merges": merge_commits, "binary_changes": binary_changes,
            },
            "contributors": sorted(authors.values(), key=lambda a: (-a["commits"], -a["added"], a["author"])),
            "activity": [{"date": date, "commits": count} for date, count in sorted(activity.items())],
            "hotspots": sorted(files.values(), key=lambda f: (-f["commits"], -(f["added"] + f["removed"]), f["path"])),
            "truncated": truncated,
            "scope": {
                "ref": ref, "since": since, "until": today.isoformat(), "days": days,
                "limit": limit, "commits_returned": len(commits), "merge_commits": merge_commits,
                "binary_changes": binary_changes, "shallow": info["shallow"], "timezone": "UTC",
                "notes": [
                    "Commit sayısına merge commitleri dahildir; satır ve dosya toplamları merge dışındaki değişikliklerden hesaplanır.",
                    "İkili dosyalar değişen dosya sayısına dahildir; metin satırı toplamına eklenmez.",
                    "Aktivite UTC takviminde committer tarihini, katkılar mailmap ile birleştirilen yazar kimliklerini kullanır; commit sayısı üretkenlik ölçüsü değildir.",
                    "Yeniden adlandırmalarda yeni yol kullanılır; bir dosya eski ve yeni adıyla ayrı satırlarda görünebilir.",
                    "Sığ depolarda veya commit sınırına ulaşıldığında geçmiş eksik olabilir.",
                ],
            },
        }

    @classmethod
    def get_history(
        cls, path: str | Path, limit: int = 100, ref: str = "HEAD", author: str = "", query: str = "",
        file_path: str = "", since: str = "", pickaxe: str = "", all_refs: bool = False,
    ) -> list[dict[str, Any]]:
        limit = cls._insights_limit(limit)
        root = cls._insights_root(path)
        target = cls._insights_ref(root, ref, allow_unborn=True)
        for label, value in (("author", author), ("query", query), ("since", since), ("pickaxe", pickaxe)):
            cls._insights_text(value, label)
        filename = cls._insights_file(root, file_path) if file_path else ""
        if not target and not all_refs:
            return []
        args = ["--literal-pathspecs", "log", "-z", f"--max-count={limit}", f"--format={cls._INSIGHTS_FORMAT}"]
        if author:
            args.append(f"--author={author}")
        if query:
            args.extend(["--fixed-strings", f"--grep={query}"])
        if since:
            args.append(f"--since={since}")
        if pickaxe:
            args.extend([f"-S{pickaxe}", "--no-ext-diff", "--no-textconv"])
        args.extend(["--all"] if all_refs else [target])
        args.append("--")
        if filename:
            args.append(filename)
        return cls._insights_history_parse(cls._insights_run(root, args, 30.0))

    @classmethod
    def get_graph(cls, path: str | Path, limit: int = 100) -> str:
        limit = cls._insights_limit(limit, 500)
        root = cls._insights_root(path)
        if cls._insights_unborn(root):
            # Other branch refs can still exist when the selected branch is new.
            code, refs, _ = cls._run_git(root, ["for-each-ref", "--count=1", "--format=%(refname)"])
            if code == 0 and not refs.strip():
                return ""
        graph = cls._insights_run(root, [
            "log", "--graph", "--all", "--decorate=short", f"--max-count={limit}",
            "--format=%h %s (%aN, %ar)%d",
        ])
        return cls._insights_patch(graph)[0]

    @classmethod
    def get_file_history(
        cls, path: str | Path, file_path: str, limit: int = 50, *, ref: str = "HEAD",
    ) -> list[dict[str, Any]]:
        limit = cls._insights_limit(limit)
        root = cls._insights_root(path)
        filename = cls._insights_file(root, file_path)
        target = cls._insights_ref(root, ref, allow_unborn=True)
        if not target:
            return []
        return cls._insights_history_parse(cls._insights_run(root, [
            "--literal-pathspecs", "log", "--follow", "--find-renames", "--no-ext-diff", "--no-textconv", "-z", f"--max-count={limit}",
            f"--format={cls._INSIGHTS_FORMAT}", target, "--", filename,
        ], 30.0))

    @classmethod
    def get_blame(
        cls, path: str | Path, file_path: str, start: int = 1, end: int | None = None, ref: str = "",
    ) -> list[dict[str, Any]]:
        cls._insights_limit(start, 100_000_000)
        if end is not None:
            cls._insights_limit(end, 100_000_000)
            if end < start or end - start >= 500:
                raise ValueError("Blame ranges must contain between 1 and 500 lines")
        root = cls._insights_root(path)
        filename = cls._insights_file(root, file_path)
        target = cls._insights_ref(root, ref) if ref else ""
        if not target and cls._insights_unborn(root):
            return []
        # A bounded range with no explicit end must also work for short files.
        # Git accepts an end beyond EOF and returns the available lines.
        args = ["--literal-pathspecs", "blame", "--no-textconv", "--line-porcelain", "-L", f"{start},{end or start + 499}"]
        if target:
            args.append(target)
        args.extend(["--", filename])
        output = cls._insights_run(root, args, 30.0)
        rows: list[dict[str, Any]] = []
        row: dict[str, Any] | None = None
        for line in output.split("\n"):
            match = re.fullmatch(r"([0-9a-f]{40,64}) (\d+) (\d+)(?: \d+)?", line)
            if match:
                full_hash, original, current = match.groups()
                row = {
                    "line": int(current), "original_line": int(original), "hash": full_hash,
                    "short_hash": full_hash[:8], "author": "", "email": "", "date": "", "summary": "",
                    "text": "", "uncommitted": not full_hash.strip("0"),
                }
            elif row is not None and line.startswith("\t"):
                row["text"] = line[1:].removesuffix("\r")
                timestamp = row.pop("_time", None)
                tz = row.pop("_tz", "+0000")
                if timestamp is not None:
                    direction = -1 if tz.startswith("-") else 1
                    offset = direction * (int(tz[1:3]) * 60 + int(tz[3:5]))
                    row["date"] = datetime.fromtimestamp(timestamp, timezone(timedelta(minutes=offset))).isoformat()
                rows.append(row)
                row = None
            elif row is not None:
                key, _, value = line.partition(" ")
                if key == "author":
                    row["author"] = value
                elif key == "author-mail":
                    row["email"] = value.removeprefix("<").removesuffix(">")
                elif key == "author-time":
                    row["_time"] = int(value)
                elif key == "author-tz":
                    row["_tz"] = value
                elif key == "summary":
                    row["summary"] = value
        return rows

    @classmethod
    def _insights_changed_files(cls, output: str) -> list[dict[str, Any]]:
        tokens, pos, rows = output.split("\0"), 0, []
        while pos < len(tokens) and tokens[pos]:
            status = tokens[pos]
            pos += 1
            if pos >= len(tokens):
                raise RuntimeError("Incomplete Git changed-file output")
            filename = tokens[pos]
            pos += 1
            row: dict[str, Any] = {"status": status, "path": filename, "old_path": ""}
            if status.startswith(("R", "C")):
                if pos >= len(tokens):
                    raise RuntimeError("Incomplete Git rename output")
                row.update(old_path=filename, path=tokens[pos])
                pos += 1
            rows.append(row)
        return rows

    @classmethod
    def get_commit_detail(cls, path: str | Path, commit_hash: str) -> dict[str, Any]:
        root = cls._insights_root(path)
        commit = cls._insights_ref(root, commit_hash)
        metadata = cls._insights_history_parse(cls._insights_run(root, [
            "log", "-1", "-z", f"--format={cls._INSIGHTS_FORMAT}", commit, "--",
        ]))[0]
        metadata["body"] = cls._insights_run(root, ["show", "-s", "--format=%B", commit, "--"]).rstrip("\n")
        diff, truncated = cls._insights_patch(cls._insights_run(root, [
            "show", "--format=", "--patch", "--diff-merges=first-parent", "--find-renames", "--no-ext-diff", "--no-textconv", commit, "--",
        ], 30.0))
        if metadata["parents"]:
            file_args = ["diff", "--name-status", "-z", "--find-renames", metadata["parents"][0], commit, "--"]
        else:
            file_args = ["diff-tree", "--root", "--no-commit-id", "-r", "--name-status", "-z", "--find-renames", commit, "--"]
        files = cls._insights_changed_files(cls._insights_run(root, file_args))
        metadata.update(diff=diff, diff_truncated=truncated, files=files,
                        diff_base=metadata["parents"][0] if metadata["parents"] else "", diff_mode="first-parent")
        return metadata

    @classmethod
    def get_branch_details(cls, path: str | Path) -> list[dict[str, Any]]:
        root = cls._insights_root(path)
        fields = "%(refname:short)%00%(objectname)%00%(authorname)%00%(authordate:iso-strict)%00%(upstream:short)%00%(upstream:track)%00%(HEAD)%00%(refname)%00%(symref)"
        output = cls._insights_run(root, ["for-each-ref", "--count=2000", "--sort=-committerdate", f"--format={fields}", "refs/heads/", "refs/remotes/"])
        rows = []
        for line in output.splitlines():
            parts = line.split("\0")
            if len(parts) != 9:
                raise RuntimeError("Incomplete Git branch output")
            name, commit, author, date, upstream, track, current, full_name, symbolic = parts
            if symbolic:
                continue
            remote = full_name.startswith("refs/remotes/")
            ahead = re.search(r"ahead (\d+)", track)
            behind = re.search(r"behind (\d+)", track)
            rows.append({
                "name": name, "hash": commit, "author": author, "date": date, "upstream": upstream,
                "ahead": int(ahead.group(1)) if ahead else 0, "behind": int(behind.group(1)) if behind else 0,
                "current": current == "*" and not remote, "upstream_gone": track == "[gone]",
                "remote": remote, "kind": "remote" if remote else "local", "tracking_known": bool(upstream) and track != "[gone]",
            })
        return rows

    @classmethod
    def compare_refs(cls, path: str | Path, base: str, target: str = "HEAD") -> dict[str, Any]:
        root = cls._insights_root(path)
        base_hash, target_hash = cls._insights_ref(root, base), cls._insights_ref(root, target)
        code, ancestor, error = cls._run_git(root, ["merge-base", base_hash, target_hash])
        if code not in (0, 1):
            raise RuntimeError((error or "Could not find merge base").strip()[:1000])
        counts = cls._insights_run(root, ["rev-list", "--left-right", "--count", f"{base_hash}...{target_hash}"]).split()
        diff, truncated = cls._insights_patch(cls._insights_run(root, [
            "diff", "--no-ext-diff", "--no-textconv", "--find-renames", base_hash, target_hash, "--",
        ], 30.0))
        stat, stat_truncated = cls._insights_patch(cls._insights_run(root, [
            "diff", "--stat", "--no-ext-diff", "--no-textconv", base_hash, target_hash, "--",
        ]))
        files = cls._insights_changed_files(cls._insights_run(root, [
            "diff", "--name-status", "-z", "--find-renames", base_hash, target_hash, "--",
        ]))
        commits = cls._insights_history_parse(cls._insights_run(root, [
            "log", "-z", "--max-count=201", f"--format={cls._INSIGHTS_FORMAT}", f"{base_hash}..{target_hash}", "--",
        ]))
        return {
            "base": base, "target": target, "base_hash": base_hash, "target_hash": target_hash,
            "merge_base": ancestor.strip() if code == 0 else "", "behind": int(counts[0]), "ahead": int(counts[1]),
            "stat": stat, "stat_truncated": stat_truncated, "diff": diff, "diff_truncated": truncated,
            "commits": commits[:200], "commits_truncated": len(commits) > 200, "files": files,
        }

    @classmethod
    def list_tracked_files(cls, path: str | Path) -> list[str]:
        root = cls._insights_root(path)
        output = cls._insights_run(root, ["ls-files", "-z"])
        return list(dict.fromkeys(filename for filename in output.split("\0") if filename))

    @classmethod
    def get_tags(cls, path: str | Path) -> list[dict[str, Any]]:
        root = cls._insights_root(path)
        fields = "%(refname:short)%00%(objectname)%00%(creatordate:iso-strict)%00%(subject)"
        output = cls._insights_run(root, ["for-each-ref", "--count=1000", "--sort=-creatordate", f"--format={fields}", "refs/tags/"])
        rows = []
        for line in output.splitlines():
            parts = line.split("\0", 3)
            if len(parts) != 4:
                raise RuntimeError("Incomplete Git tag output")
            rows.append(dict(zip(("name", "hash", "date", "message"), parts)))
        return rows

    @classmethod
    def get_reflog(cls, path: str | Path, limit: int = 50) -> list[dict[str, Any]]:
        limit = cls._insights_limit(limit, 500)
        root = cls._insights_root(path)
        if cls._insights_unborn(root):
            return []
        output = cls._insights_run(root, [
            "reflog", "show", "-z", f"--max-count={limit}", "--date=iso-strict",
            "--format=%H%x00%h%x00%gD%x00%gs%x00%gN%x00%gE%x00%aI", "HEAD",
        ])
        tokens = output.split("\0")
        rows, pos = [], 0
        while pos < len(tokens) and tokens[pos]:
            if pos + 6 >= len(tokens):
                raise RuntimeError("Incomplete Git reflog output")
            row = dict(zip(("hash", "short_hash", "ref", "message", "author", "email", "date"), tokens[pos:pos + 7]))
            event_date = re.search(r"@\{([^}]+)\}$", row["ref"])
            if event_date:
                row["date"] = event_date.group(1)
            rows.append(row)
            pos += 7
        return rows

    @classmethod
    def get_stashes(cls, path: str | Path) -> list[dict[str, Any]]:
        root = cls._insights_root(path)
        output = cls._insights_run(root, [
            "stash", "list", "-z", "--max-count=500", "--format=%gd%x00%H%x00%gs%x00%aI%x00%aN",
        ])
        tokens, rows, pos = output.split("\0"), [], 0
        while pos < len(tokens) and tokens[pos]:
            if pos + 4 >= len(tokens):
                raise RuntimeError("Incomplete Git stash output")
            rows.append(dict(zip(("ref", "hash", "message", "date", "author"), tokens[pos:pos + 5])))
            pos += 5
        return rows

    @classmethod
    def get_worktrees(cls, path: str | Path) -> list[dict[str, Any]]:
        root = cls._insights_root(path)
        output = cls._insights_run(root, ["worktree", "list", "--porcelain", "-z"])
        rows, row = [], {}
        for entry in output.split("\0"):
            if not entry:
                if row:
                    rows.append(row)
                    row = {}
                continue
            key, _, value = entry.partition(" ")
            if key == "worktree":
                row = {"path": str(Path(value)), "hash": "", "branch": "", "detached": False, "bare": False, "locked": False, "prunable": False}
            elif key == "HEAD":
                row["hash"] = value
            elif key == "branch":
                row["branch"] = value.removeprefix("refs/heads/")
            elif key in ("detached", "bare"):
                row[key] = True
            elif key in ("locked", "prunable"):
                row[key] = True
                row[f"{key}_reason"] = value
        if row:
            rows.append(row)
        return rows
