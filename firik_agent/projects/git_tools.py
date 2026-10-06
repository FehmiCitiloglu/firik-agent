"""Git intelligence tools shared by CLI and desktop coding agents."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from firik_agent.core.types import ToolDefinition
from firik_agent.projects.git_service import GitService


GIT_AGENT_INSTRUCTIONS = (
    "\nGit intelligence: use git_inspect to read real repository status, commit history, "
    "contributor analytics, line blame, file history and branch comparisons. "
    "Use git_tools to discover and run advanced read-only Git tools. git_prepare creates "
    "an exact operation preview for the Git God panel. Attribute a line using blame and "
    "a file using its latest file-history commit; never invent ownership or equate commit "
    "count with quality. Mention sampled/shallow history and uncommitted lines. "
    "For requested Git mutations, explain their effect and use the Git panel's operation "
    "preview when approval is needed. Preserve existing user changes."
)


class GitAgentTools:
    def __init__(self, agent: Any):
        self.agent = agent

    def _cwd(self, cwd: str = "") -> str:
        root = Path(cwd or self.agent.default_cwd or Path.cwd()).expanduser().resolve()
        if not GitService.is_git_repo(root):
            raise ValueError("Seçili klasör bir Git çalışma ağacı değil.")
        return str(root)

    async def inspect(self, view: str = "status", cwd: str = "", **options: Any) -> Any:
        return await asyncio.to_thread(self._inspect, view, cwd, options)

    def _inspect(self, view: str, cwd: str, options: dict) -> Any:
        path = self._cwd(cwd)
        limit = max(1, min(int(options.get("limit", 100)), 500))
        ref = options.get("ref", "HEAD")
        file_path = options.get("file_path", "")
        if view == "status":
            return GitService.get_status(path)
        if view == "dashboard":
            return GitService.get_dashboard(path, days=int(options.get("days", 90)), ref=ref,
                                             limit=limit if "limit" in options else 2000)
        if view == "history":
            keys = ("author", "query", "since", "pickaxe", "all_refs")
            return GitService.get_history(path, limit=limit, ref=ref, file_path=file_path,
                                          **{key: options[key] for key in keys if key in options})
        if view == "file_history":
            return GitService.get_file_history(path, file_path, limit=limit, ref=ref)
        if view == "blame":
            # HEAD is optional: omitting it includes uncommitted working tree lines.
            return GitService.get_blame(path, file_path, start=int(options.get("start", 1)),
                                        end=options.get("end"), ref=options.get("ref", ""))
        if view == "branches":
            return GitService.get_branch_details(path)
        if view == "compare":
            return GitService.compare_refs(path, options.get("base", ""), target=ref)
        if view == "commit":
            return GitService.get_commit_detail(path, ref)
        if view == "recovery":
            return {"reflog": GitService.get_reflog(path, limit=limit),
                    "stashes": GitService.get_stashes(path), "tags": GitService.get_tags(path),
                    "worktrees": GitService.get_worktrees(path)}
        raise ValueError(f"Bilinmeyen Git görünümü: {view}")

    async def tools(self, tool_id: str = "", params: dict | None = None, cwd: str = "") -> Any:
        if not tool_id:
            return GitService.get_tool_catalog()
        return await asyncio.to_thread(self._run_read_tool, tool_id, params or {}, cwd)

    def _run_read_tool(self, tool_id: str, params: dict, cwd: str) -> Any:
        path = self._cwd(cwd)
        preview = GitService.preview_tool(path, tool_id, params)
        if preview.get("ok") is False:
            raise ValueError(preview.get("error", "Git işlemi hazırlanamadı."))
        if preview["requires_confirmation"]:
            return {"executed": False, "requires_confirmation": True, "preview": preview,
                    "next_step": "Git God → Araç Kutusu: komutu inceleyip Çalıştır düğmesiyle onaylayın."}
        return GitService.execute_tool(path, tool_id, params)

    async def prepare(self, tool_id: str, params: dict | None = None, cwd: str = "") -> Any:
        return await asyncio.to_thread(lambda: GitService.preview_tool(self._cwd(cwd), tool_id, params or {}))


def register_git_tools(agent: Any) -> None:
    bridge = GitAgentTools(agent)
    inspect_properties = {
        "view": {"type": "string", "enum": ["status", "dashboard", "history", "file_history", "blame",
                                                "branches", "compare", "commit", "recovery"]},
        "cwd": {"type": "string", "description": "Repository folder; defaults to the current project."},
        "ref": {"type": "string", "description": "Commit/branch; omit for worktree blame, defaults to HEAD elsewhere."},
        "base": {"type": "string", "description": "Base branch/commit for comparison."},
        "file_path": {"type": "string", "description": "Repository-relative file for history or blame."},
        "author": {"type": "string"}, "query": {"type": "string"}, "since": {"type": "string"},
        "pickaxe": {"type": "string", "description": "Find commits changing occurrences of this text (-S)."},
        "limit": {"type": "integer", "minimum": 1, "maximum": 500},
        "days": {"type": "integer", "minimum": 1, "maximum": 3650},
        "start": {"type": "integer", "minimum": 1}, "end": {"type": "integer", "minimum": 1},
        "all_refs": {"type": "boolean"},
    }
    definitions = [
        ("git_inspect", "Read real Git data: contributor charts, commits, ownership of each line, latest file editor, branches, comparisons and recovery records.",
         {"type": "object", "properties": inspect_properties}, bridge.inspect),
        ("git_tools", "Discover advanced Git tools (omit tool_id) or run a read tool by its ID. Mutations return an approval preview without executing.",
         {"type": "object", "properties": {"tool_id": {"type": "string"}, "params": {"type": "object"}, "cwd": {"type": "string"}}}, bridge.tools),
        ("git_prepare", "Prepare the exact command, effects and warnings for a Git operation in the Git God UI. Does not execute it.",
         {"type": "object", "properties": {"tool_id": {"type": "string"}, "params": {"type": "object"}, "cwd": {"type": "string"}}, "required": ["tool_id"]}, bridge.prepare),
    ]
    for name, description, parameters, handler in definitions:
        agent.tools.register(ToolDefinition(name=name, description=description, parameters=parameters, source="builtin:git"), handler)
