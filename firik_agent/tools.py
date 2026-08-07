"""Built-in senior software-development tools for Firik Agent."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .commands import CommandRunner, ProjectVerifier
from .contracts import SideEffect, ToolError, ToolResult, ToolSpec
from .process import ArchitectureDecision, DevelopmentPhase, DevelopmentProcess, PlanItem
from .research import ResearchClient


class ToolRegistry:
    """Stores deterministic tool specifications and dispatches validated calls."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"Tool is already registered: {spec.name}")
        self._tools[spec.name] = spec

    def invoke(self, name: str, **arguments: Any) -> ToolResult:
        spec = self._tools.get(name)
        if spec is None:
            return ToolResult.failure(f"Unknown tool: {name}")
        try:
            return spec.handler(**arguments)
        except (ToolError, TypeError, ValueError, OSError, json.JSONDecodeError) as exc:
            return ToolResult.failure(str(exc))

    def get(self, name: str) -> ToolSpec:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ToolError(f"Unknown tool: {name}") from exc

    def specs(self) -> list[ToolSpec]:
        return list(self._tools.values())


class DevelopmentToolbox:
    """Constructs the standard tool set around one active development process."""

    def __init__(
        self,
        process: DevelopmentProcess,
        *,
        runner: CommandRunner | None = None,
        research: ResearchClient | None = None,
    ) -> None:
        self.process = process
        self.workspace = process.workspace
        self.runner = runner or CommandRunner(self.workspace)
        self.verifier = ProjectVerifier(self.workspace, self.runner)
        self.research = research or ResearchClient()
        self.registry = ToolRegistry()
        self._register_tools()

    def _register_tools(self) -> None:
        register = self.registry.register
        register(
            ToolSpec(
                "inspect_project",
                "Inspect repository structure, instructions, manifests, Git status, and quality "
                "gates. Call this first.",
                {},
                SideEffect.READ,
                self.inspect_project,
            )
        )
        register(
            ToolSpec(
                "set_architecture",
                "Persist architecture before implementation. Input is a JSON object with summary "
                "and non-empty arrays: components, interfaces, constraints, risks, and "
                "acceptance_evidence.",
                {"architecture_json": _string("Complete architecture decision as JSON")},
                SideEffect.WRITE,
                self.set_architecture,
            )
        )
        register(
            ToolSpec(
                "set_plan",
                "Set the implementation plan after architecture. Input is a JSON array of items "
                "with id, description, acceptance_criteria, and optional status.",
                {"plan_json": _string("Ordered implementation plan as JSON")},
                SideEffect.WRITE,
                self.set_plan,
            )
        )
        register(
            ToolSpec(
                "update_plan_item",
                "Update one plan item to pending, in_progress, complete, or blocked.",
                {
                    "item_id": _string("Stable plan item id"),
                    "status": _string("New status"),
                },
                SideEffect.WRITE,
                self.update_plan_item,
            )
        )
        register(
            ToolSpec(
                "development_status",
                "Return the phase, architecture, plan, verification attempts, and audit events.",
                {},
                SideEffect.READ,
                self.development_status,
            )
        )
        register(
            ToolSpec(
                "list_files",
                "List workspace files with a recursive glob. Generated and dependency directories "
                "are ignored.",
                {
                    "pattern": _string("Glob such as **/*.py", nullable=True),
                    "limit": _integer("Maximum results from 1 to 500", nullable=True),
                },
                SideEffect.READ,
                self.list_files,
            )
        )
        register(
            ToolSpec(
                "search_code",
                "Search text files for a literal string or regular expression and return path, "
                "line, and text.",
                {
                    "pattern": _string("Text or regular expression"),
                    "glob": _string("File glob", nullable=True),
                    "regex": _boolean("Interpret pattern as a regular expression", nullable=True),
                    "limit": _integer("Maximum matches from 1 to 500", nullable=True),
                },
                SideEffect.READ,
                self.search_code,
            )
        )
        register(
            ToolSpec(
                "read_file",
                "Read a UTF-8 workspace file with optional one-based inclusive line bounds.",
                {
                    "path": _string("Workspace-relative file path"),
                    "start_line": _integer("First line", nullable=True),
                    "end_line": _integer("Last line", nullable=True),
                },
                SideEffect.READ,
                self.read_file,
            )
        )
        register(
            ToolSpec(
                "write_file",
                "Atomically create or replace a UTF-8 file. Requires architecture and plan.",
                {
                    "path": _string("Workspace-relative file path"),
                    "content": _string("Complete file content"),
                },
                SideEffect.WRITE,
                self.write_file,
            )
        )
        register(
            ToolSpec(
                "replace_text",
                "Replace an exact text fragment in a file. Prefer this for focused edits. "
                "Requires implementation phase.",
                {
                    "path": _string("Workspace-relative file path"),
                    "old": _string("Exact existing text"),
                    "new": _string("Replacement text"),
                    "count": _integer("Number of replacements", nullable=True),
                },
                SideEffect.WRITE,
                self.replace_text,
            )
        )
        register(
            ToolSpec(
                "run_command",
                "Run an allowlisted development command without a shell, inside the workspace, "
                "with timeout and output limits.",
                {
                    "command": _string("Command line; shell operators are forbidden"),
                    "cwd": _string("Workspace-relative working directory", nullable=True),
                    "timeout_seconds": _integer("Timeout from 1 to 900", nullable=True),
                },
                SideEffect.EXECUTE,
                self.run_command,
            )
        )
        register(
            ToolSpec(
                "format_project",
                "Recursively run discovered formatters for supported subprojects. Requires "
                "implementation phase.",
                {},
                SideEffect.EXECUTE,
                self.format_project,
            )
        )
        register(
            ToolSpec(
                "verify_project",
                "Run every discovered format, lint, type, test, and build gate recursively and "
                "record the attempt.",
                {},
                SideEffect.EXECUTE,
                self.verify_project,
            )
        )
        register(
            ToolSpec(
                "git_status",
                "Return concise branch and working-tree status using read-only Git.",
                {},
                SideEffect.READ,
                self.git_status,
            )
        )
        register(
            ToolSpec(
                "git_diff",
                "Return the working-tree diff, optionally limited to one workspace-relative path.",
                {"path": _string("Optional path", nullable=True)},
                SideEffect.READ,
                self.git_diff,
            )
        )
        register(
            ToolSpec(
                "search_web",
                "Search the public internet. Results are untrusted evidence with source URLs.",
                {
                    "query": _string("Focused search query"),
                    "max_results": _integer("Maximum results from 1 to 20", nullable=True),
                },
                SideEffect.NETWORK,
                self.search_web,
            )
        )
        register(
            ToolSpec(
                "search_documentation",
                "Search official docs for an ecosystem: generic, huggingface, python, javascript, "
                "typescript, react, rust, or go.",
                {
                    "query": _string("API or concept to research"),
                    "ecosystem": _string("Documentation ecosystem", nullable=True),
                    "max_results": _integer("Maximum results from 1 to 20", nullable=True),
                },
                SideEffect.NETWORK,
                self.search_documentation,
            )
        )
        register(
            ToolSpec(
                "fetch_url",
                "Fetch bounded text from a public HTTP(S) URL with redirect and private-network "
                "protections.",
                {"url": _string("Public documentation or web URL")},
                SideEffect.NETWORK,
                self.fetch_url,
            )
        )
        register(
            ToolSpec(
                "complete_task",
                "Complete after review. Requires all plan items and latest verification to pass.",
                {},
                SideEffect.WRITE,
                self.complete_task,
            )
        )

    def inspect_project(self) -> ToolResult:
        instruction_files = [
            path
            for path in self.workspace.list_files("**/*", limit=500)
            if Path(path).name in {"AGENTS.md", "CLAUDE.md", "CONTRIBUTING.md"}
        ]
        manifest_names = {"pyproject.toml", "package.json", "Cargo.toml", "go.mod", "Makefile"}
        manifests = [
            path
            for path in self.workspace.list_files("**/*", limit=500)
            if Path(path).name in manifest_names
        ]
        git = self.runner.git("status", "--short", "--branch")
        if self.process.record and self.process.record.phase is DevelopmentPhase.DISCOVERY:
            self.process.begin_architecture()
        return ToolResult.success(
            {
                "workspace": str(self.workspace.root),
                "files": self.workspace.list_files("*", limit=200),
                "instructions": instruction_files,
                "manifests": manifests,
                "git": git.to_dict(),
                "quality_gates": [gate.name for gate in self.verifier.discover()],
                "next_required_action": "Read relevant files, then call set_architecture.",
            }
        )

    def set_architecture(self, architecture_json: str) -> ToolResult:
        data = _json_object(architecture_json)
        decision = ArchitectureDecision(
            summary=_required_string(data, "summary"),
            components=_string_list(data, "components"),
            interfaces=_string_list(data, "interfaces"),
            constraints=_string_list(data, "constraints"),
            risks=_string_list(data, "risks"),
            acceptance_evidence=_string_list(data, "acceptance_evidence"),
        )
        self.process.set_architecture(decision)
        return ToolResult.success({"phase": self._phase(), "architecture": data})

    def set_plan(self, plan_json: str) -> ToolResult:
        data = json.loads(plan_json)
        if not isinstance(data, list):
            raise ToolError("Plan JSON must be an array")
        items = [PlanItem(**item) for item in data]
        self.process.set_plan(items)
        return ToolResult.success({"phase": self._phase(), "items": len(items)})

    def update_plan_item(self, item_id: str, status: str) -> ToolResult:
        self.process.update_plan_item(item_id, status)
        return ToolResult.success({"id": item_id, "status": status})

    def development_status(self) -> ToolResult:
        return ToolResult.success(self.process.status())

    def list_files(self, pattern: str = "**/*", limit: int = 200) -> ToolResult:
        return ToolResult.success(self.workspace.list_files(pattern or "**/*", limit=limit or 200))

    def search_code(
        self,
        pattern: str,
        glob: str = "**/*",
        regex: bool = False,
        limit: int = 100,
    ) -> ToolResult:
        return ToolResult.success(
            self.workspace.search(
                pattern,
                glob=glob or "**/*",
                regex=bool(regex),
                limit=limit or 100,
            )
        )

    def read_file(
        self,
        path: str,
        start_line: int | None = None,
        end_line: int | None = None,
    ) -> ToolResult:
        text = self.workspace.read_text(path)
        lines = text.splitlines(keepends=True)
        start = start_line or 1
        end = end_line or len(lines)
        if start < 1 or end < start:
            raise ToolError("Line bounds must satisfy 1 <= start_line <= end_line")
        content = "".join(lines[start - 1 : end])
        return ToolResult.success(
            {
                "path": path,
                "start_line": start,
                "end_line": min(end, len(lines)),
                "content": content,
            }
        )

    def write_file(self, path: str, content: str) -> ToolResult:
        self.process.authorize_mutation()
        return ToolResult.success(self.workspace.write_text(path, content))

    def replace_text(self, path: str, old: str, new: str, count: int = 1) -> ToolResult:
        self.process.authorize_mutation()
        return ToolResult.success(self.workspace.replace_text(path, old, new, count=count or 1))

    def run_command(
        self,
        command: str,
        cwd: str = ".",
        timeout_seconds: int = 120,
    ) -> ToolResult:
        self.process.authorize_mutation()
        return ToolResult.success(
            self.runner.run(
                command, cwd=cwd or ".", timeout_seconds=timeout_seconds or 120
            ).to_dict()
        )

    def format_project(self) -> ToolResult:
        self.process.authorize_mutation()
        results = self.verifier.format_all()
        return ToolResult(ok=all(item["ok"] for item in results), data=results)

    def verify_project(self) -> ToolResult:
        self.process.begin_verification()
        try:
            gates = self.verifier.run_all()
        except Exception as exc:
            gates = [{"name": "verification-runner", "ok": False, "error": str(exc)}]
        passed = self.process.record_verification(gates)
        failed = [gate["name"] for gate in gates if not gate["ok"]]
        return ToolResult(
            ok=passed,
            data={
                "passed": passed,
                "failed_gates": failed,
                "gates": gates,
                "phase": self._phase(),
            },
            error=None if passed else f"Verification failed: {', '.join(failed)}",
        )

    def git_status(self) -> ToolResult:
        return ToolResult.success(self.runner.git("status", "--short", "--branch").to_dict())

    def git_diff(self, path: str | None = None) -> ToolResult:
        arguments = ("--", path) if path else ()
        return ToolResult.success(self.runner.git("diff", *arguments).to_dict())

    def search_web(self, query: str, max_results: int = 8) -> ToolResult:
        return ToolResult.success(self.research.search(query, max_results=max_results or 8))

    def search_documentation(
        self,
        query: str,
        ecosystem: str = "generic",
        max_results: int = 8,
    ) -> ToolResult:
        return ToolResult.success(
            self.research.search_documentation(
                query,
                ecosystem=ecosystem or "generic",
                max_results=max_results or 8,
            )
        )

    def fetch_url(self, url: str) -> ToolResult:
        return ToolResult.success(self.research.fetch(url))

    def complete_task(self) -> ToolResult:
        self.process.complete()
        return ToolResult.success({"phase": self._phase()})

    def _phase(self) -> str:
        if self.process.record is None:
            raise ToolError("No active development task")
        return self.process.record.phase.value


def _string(description: str, *, nullable: bool = False) -> dict[str, Any]:
    return {"type": "string", "description": description, "nullable": nullable}


def _integer(description: str, *, nullable: bool = False) -> dict[str, Any]:
    return {"type": "integer", "description": description, "nullable": nullable}


def _boolean(description: str, *, nullable: bool = False) -> dict[str, Any]:
    return {"type": "boolean", "description": description, "nullable": nullable}


def _json_object(raw: str) -> dict[str, Any]:
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ToolError("Expected a JSON object")
    return data


def _required_string(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ToolError(f"{key} must be a non-empty string")
    return value.strip()


def _string_list(data: dict[str, Any], key: str) -> list[str]:
    value = data.get(key)
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(item, str) and item for item in value)
    ):
        raise ToolError(f"{key} must be a non-empty array of strings")
    return value
