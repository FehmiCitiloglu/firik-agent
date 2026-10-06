"""Opt-in specialist foundation, inheriting the existing coding execution gate.

No provider, worker, worktree, or background run is created by importing this
module. A caller must enable the coordinator and register a real runner.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
import inspect
import json
from pathlib import Path
import threading
from types import MappingProxyType
from typing import Any, Callable, Iterable, Mapping
from uuid import uuid4

from firik_agent.core.types import ToolCall, ToolResult
from firik_agent.projects.coding_sessions import redact_sensitive
from firik_agent.projects.indexer import safe_project_path


class SpecialistRole(str, Enum):
    PLANNING = "planning"
    CODING = "coding"
    TESTING = "testing"
    REVIEW = "review"
    DOCUMENTATION = "documentation"


READ_TOOLS = frozenset({"read_file", "list_files", "search_project", "search_symbols", "git_status", "git_diff"})
EDIT_TOOLS = frozenset({"edit_file", "write_file", "apply_patch", "delete_file"})
ROLE_TOOLS = {
    SpecialistRole.PLANNING: READ_TOOLS | {"update_plan"},
    SpecialistRole.CODING: READ_TOOLS | EDIT_TOOLS | {"update_plan", "run_command", "run_tests", "record_verification"},
    SpecialistRole.TESTING: READ_TOOLS | {"run_tests", "record_verification"},
    SpecialistRole.REVIEW: READ_TOOLS | {"record_verification"},
    SpecialistRole.DOCUMENTATION: READ_TOOLS | {"edit_file", "write_file", "apply_patch"},
}


@dataclass(frozen=True)
class WorkspaceBinding:
    """Identity for an existing checkout; isolated checkout provisioning is external."""
    project_id: str
    root: str
    workspace_id: str = ""
    kind: str = "existing"  # existing or isolated_existing; neither creates a checkout.

    def validate(self) -> Path:
        if self.kind not in {"existing", "isolated_existing"}:
            raise ValueError("Çalışma alanı türü desteklenmiyor.")
        root = Path(self.root).resolve()
        if not root.is_dir():
            raise ValueError("Uzman ajan çalışma klasörü bulunamadı.")
        return root


class FileLeaseConflict(RuntimeError):
    pass


class FileLeaseManager:
    """Process-local leases prevent overlapping source edits by registered specialists."""

    def __init__(self):
        self._lock = threading.RLock()
        self._leases: dict[Path, tuple[str, int]] = {}

    @contextmanager
    def acquire(self, workspace: WorkspaceBinding, owner: str, paths: Iterable[str]):
        root = workspace.validate()
        normalized = set(safe_project_path(root, path) for path in paths)
        if not owner:
            raise ValueError("Dosya kilidi için uzman ajan kimliği gerekli.")
        with self._lock:
            for candidate in normalized:
                for leased, (holder, _) in self._leases.items():
                    if holder != owner and (candidate.is_relative_to(leased) or leased.is_relative_to(candidate)):
                        raise FileLeaseConflict("Dosya başka bir uzman ajanın çalışmasına ayrılmış.")
            for candidate in normalized:
                _, count = self._leases.get(candidate, (owner, 0))
                self._leases[candidate] = (owner, count + 1)
        try:
            yield tuple(sorted(normalized))
        finally:
            with self._lock:
                for candidate in normalized:
                    holder, count = self._leases[candidate]
                    if count == 1:
                        del self._leases[candidate]
                    else:
                        self._leases[candidate] = (holder, count - 1)


@dataclass(frozen=True)
class SpecialistAssignment:
    id: str
    role: SpecialistRole
    workspace: WorkspaceBinding
    project_id: str
    task_id: str | None
    session_id: str
    mode: str
    permissions: Mapping[str, bool]
    allowed_tools: frozenset[str]
    context: Mapping[str, Any]
    write_paths: tuple[str, ...]


def intersect_permissions(parent: Mapping[str, Any], requested: Mapping[str, Any] | None = None) -> Mapping[str, bool]:
    requested = parent if requested is None else requested
    # Unknown permissions and truthy non-boolean inputs can never grant privileges.
    return MappingProxyType({key: value is True and requested.get(key) is True for key, value in parent.items()})


class SpecialistToolFilter:
    """All calls still use the parent's live gate, approval, and cancellation control."""

    def __init__(self, parent: Any, session: Any, assignment: SpecialistAssignment):
        self.parent, self.session, self.assignment = parent, session, assignment

    def _allowed(self, name: str) -> bool:
        if name not in self.assignment.allowed_tools:
            return False
        permission = ("allow_reads" if name in READ_TOOLS else "allow_edits" if name in EDIT_TOOLS else "allow_tests" if name == "run_tests"
                      else "allow_terminal" if name == "run_command" else None)
        if permission and self.assignment.permissions.get(permission) is not True:
            return False
        if name == "delete_file" and self.assignment.permissions.get("allow_deletes") is not True:
            return False
        return self.parent.is_tool_allowed(name, self.session)

    def get_schemas(self) -> list[dict[str, Any]]:
        return [schema for schema in self.parent.get_schemas(self.session)
                if self._allowed(schema.get("function", {}).get("name", ""))]

    async def execute(self, call: ToolCall) -> ToolResult:
        name = call.function.name
        try:
            if not self._allowed(name):
                raise PermissionError("Uzman ajan bu aracı kullanmaya yetkili değil.")
            if name in EDIT_TOOLS:
                arguments = json.loads(call.function.arguments or "{}")
                if not isinstance(arguments, dict):
                    raise ValueError("Uzman ajan araç parametreleri nesne olmalıdır.")
                target = safe_project_path(self.assignment.workspace.root, arguments.get("path", ""))
                leases = [safe_project_path(self.assignment.workspace.root, path) for path in self.assignment.write_paths]
                if not any(target.is_relative_to(lease) for lease in leases):
                    raise PermissionError("Dosya uzman ajanın ayrılmış düzenleme kapsamının dışında.")
            # Re-use the parent's exact session, rather than manufacturing a wider child session.
            return await self.parent.execute(call, self.session)
        except (ValueError, OSError, TypeError) as error:
            return ToolResult(tool_call_id=call.id, name=name, content=str(error), is_error=True)


class SpecialistCoordinator:
    """A small opt-in dispatcher; registered runners decide provider/model work."""

    def __init__(self, enabled: bool = False, leases: FileLeaseManager | None = None):
        self.enabled = enabled
        self.leases = leases or FileLeaseManager()
        self._runners: dict[SpecialistRole, Callable[..., Any]] = {}

    def register(self, role: SpecialistRole | str, runner: Callable[..., Any]) -> None:
        if not callable(runner):
            raise ValueError("Uzman ajan yürütücüsü çağrılabilir olmalıdır.")
        self._runners[self._role(role)] = runner

    @staticmethod
    def _role(role: SpecialistRole | str) -> SpecialistRole:
        try:
            return SpecialistRole(role)
        except ValueError as error:
            raise ValueError("Uzman ajan rolü desteklenmiyor.") from error

    async def run(self, role: SpecialistRole | str, *, workspace: WorkspaceBinding,
                  parent_filter: Any, session: Any, context: Mapping[str, Any],
                  requested_permissions: Mapping[str, Any] | None = None,
                  requested_tools: Iterable[str] | None = None,
                  write_paths: Iterable[str] = ()) -> Any:
        role = self._role(role)
        if not self.enabled or role not in self._runners:
            raise ValueError("Uzman ajan desteği etkin değil veya yürütücü yapılandırılmadı.")
        control = parent_filter.control
        root = workspace.validate()
        if (workspace.project_id != control.project_id or session.session_id != control.session_id
                or root != Path(parent_filter.root).resolve()):
            raise ValueError("Uzman ajan çalışma alanı ana oturuma ait değil.")
        if context.get("project_id", workspace.project_id) != workspace.project_id:
            raise ValueError("Uzman ajan bağlamı başka bir projeye ait.")
        if context.get("task_id", control.task_id) != control.task_id:
            raise ValueError("Uzman ajan bağlamı başka bir göreve ait.")
        permissions = intersect_permissions(control.permissions, requested_permissions)
        schemas = parent_filter.get_schemas(session)
        parent_tools = {schema.get("function", {}).get("name", "") for schema in schemas}
        tools = parent_tools & ROLE_TOOLS[role]
        if requested_tools is not None:
            tools &= set(requested_tools)
        if control.mode in {"ask", "plan"}:
            tools -= EDIT_TOOLS | {"run_command", "run_tests"}
        paths = tuple(dict.fromkeys(write_paths))
        if paths and (control.mode in {"ask", "plan"} or permissions.get("allow_edits") is not True
                      or not tools & EDIT_TOOLS):
            raise PermissionError("Bu uzman ajan için dosya düzenlemesi izinli değil.")
        assignment = SpecialistAssignment(
            id=f"specialist_{uuid4().hex}", role=role, workspace=workspace,
            project_id=control.project_id, task_id=control.task_id, session_id=session.session_id,
            mode=control.mode, permissions=permissions, allowed_tools=frozenset(tools),
            context=MappingProxyType(redact_sensitive(dict(context))), write_paths=paths,
        )
        await control.checkpoint()
        with self.leases.acquire(workspace, assignment.id, paths):
            result = self._runners[role](assignment, SpecialistToolFilter(parent_filter, session, assignment))
            return await result if inspect.isawaitable(result) else result
