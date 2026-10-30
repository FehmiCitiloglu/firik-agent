"""Optional execution isolation. Permission checks belong to the calling run control.

Docker executes a filtered private copy, never the user's checkout. Host execution
is deliberately labelled as host and retains the user's ordinary process rights.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from typing import AsyncIterator, Mapping, Protocol, runtime_checkable
from uuid import uuid4

from firik_agent.core.shell import shell_args, _stop
from firik_agent.projects.indexer import IGNORED_DIRS, is_sensitive_file, safe_project_path


class ExecutionError(RuntimeError):
    pass


class SandboxUnavailable(ExecutionError):
    """Required isolation could not be established; callers must not retry on host."""


class WorkspaceConflict(ExecutionError):
    pass


def _local_endpoint(value: str) -> bool:
    if not isinstance(value, str) or any(character in value for character in "\x00\r\n"):
        return False
    if value.startswith("unix:///"):
        return len(value) > len("unix:///")
    # Windows named pipes may also address remote machines. Accept only the
    # local device namespace used by Docker Desktop.
    normalized = value.replace("\\", "/").lower()
    return normalized.startswith(("npipe:////./pipe/", "npipe:////?/pipe/"))


@dataclass(frozen=True)
class ExecutionConfig:
    backend: str = "host"
    image: str = "python:3.12-slim"
    profile: str = "restricted"
    network: bool = False
    network_approved: bool = False
    cpus: float = 1.0
    memory_mb: int = 512
    pids_limit: int = 128
    max_timeout: float = 300
    output_limit: int = 32768
    tmpfs_mb: int = 64
    environment_allowlist: tuple[str, ...] = ("LANG", "LC_ALL", "TZ", "PYTHONUNBUFFERED", "CI")

    def __post_init__(self):
        if self.backend not in {"host", "docker"} or self.profile not in {"restricted", "development", "custom"}:
            raise ValueError("Unsupported execution backend or profile")
        for name, low, high in (("cpus", .1, 8), ("memory_mb", 64, 8192),
                                ("pids_limit", 16, 512), ("max_timeout", .01, 3600),
                                ("output_limit", 1024, 262144), ("tmpfs_mb", 1, 512)):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
                raise ValueError(f"Invalid resource limit: {name}")
            if name in {"memory_mb", "pids_limit", "output_limit", "tmpfs_mb"} and type(value) is not int:
                raise ValueError(f"Invalid integer resource limit: {name}")
        if type(self.network) is not bool or type(self.network_approved) is not bool:
            raise ValueError("Network policy must be boolean")
        if self.network and (self.profile == "restricted" or not self.network_approved):
            raise ValueError("Network requires an approved development/custom profile")
        # A custom profile may narrow this fixed allowlist, never inject loader,
        # package-manager, proxy, credential, PATH, or Docker configuration.
        hard_allowlist = {"LANG", "LC_ALL", "TZ", "PYTHONUNBUFFERED", "CI"}
        if not set(self.environment_allowlist) <= hard_allowlist:
            raise ValueError("Environment allowlist exceeds hard security limits")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/:@-]{0,255}", self.image) or self.image.startswith("-"):
            raise ValueError("Invalid Docker image reference")


@dataclass(frozen=True)
class CommandRequest:
    command: str
    cwd: str = "."
    timeout: float = 60
    shell: str | None = None
    environment: Mapping[str, str] = field(default_factory=dict)
    command_id: str = field(default_factory=lambda: "command_" + uuid4().hex)


@dataclass(frozen=True)
class BackendAvailability:
    available: bool
    backend: str
    diagnostic: str = ""
    platform: str = sys.platform
    endpoint: str = ""
    isolation: str = "none"


@dataclass
class ExecutionWorkspace:
    workspace_id: str
    root: Path
    source_root: Path
    backend: str
    managed: bool = False
    excluded: tuple[str, ...] = ()
    source_digest: str = ""
    baseline: dict[str, str] = field(default_factory=dict, repr=False)
    state: str = "ready"
    endpoint: str = ""


@dataclass(frozen=True)
class OutputEvent:
    command_id: str
    stream: str
    text: str


@dataclass
class CommandResult:
    command_id: str
    workspace_id: str
    backend: str
    boundary: str
    status: str
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    cancelled: bool = False
    truncated: bool = False
    cleanup_error: str = ""
    network: str = "host"
    artifacts_changed: list[str] = field(default_factory=list)
    source_unchanged: bool = True
    source_tree_matches: bool = True
    duration_seconds: float = 0

    def format_shell(self) -> str:
        if self.status in {"error", "unavailable", "uncertain"}:
            prefix = f"Command execution {self.status}:"
        elif self.cancelled:
            prefix = "Command cancelled; process terminated."
        elif self.timed_out:
            prefix = "Command timed out; process terminated."
        else:
            prefix = f"Exit code {self.exit_code}:"
        body = self.stdout
        if self.stderr:
            body += "\n[stderr]:\n" + self.stderr
        if self.truncated:
            body += "\n[output truncated]"
        if self.cleanup_error:
            body += "\n[cleanup unresolved]: " + self.cleanup_error
        if self.backend == "docker":
            body += f"\n[execution boundary: sandbox; network: {self.network}; workspace: {self.workspace_id}]"
            if self.artifacts_changed:
                body += "\n[isolated artifacts require review; no host files were updated]"
        return prefix + "\n" + body.strip()


@dataclass(frozen=True)
class ArtifactRecord:
    path: str
    status: str
    size: int
    sha256: str
    local_path: str


@dataclass
class _Running:
    workspace_id: str
    process: asyncio.subprocess.Process | None = None
    task: asyncio.Task | None = None
    container: str | None = None
    state: str = "starting"
    events: list[OutputEvent] = field(default_factory=list)
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)
    result: CommandResult | None = None


# High-confidence content signals supplement filename exclusions. Unknown secrets
# in ordinary project files cannot be identified universally; callers must review
# what source they admit. Never silently redact executable source into a new program.
_SECRET = re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----|\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}|\b(?:AKIA|ASIA)[A-Z0-9]{16}\b|\b(?:ghp_|github_pat_)[A-Za-z0-9_]{24,}")
_MAX_FILES = 20000
_MAX_BYTES = 256 * 1024 * 1024


def _digest(values: Mapping[str, str]) -> str:
    return hashlib.sha256(json.dumps(dict(values), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _scoped(root: Path, relative: str, *, directory=False) -> Path:
    raw = Path(relative)
    candidate = raw if raw.is_absolute() else root / raw
    try:
        local = candidate.relative_to(root)
    except ValueError:
        raise ExecutionError("Command working directory escapes its workspace") from None
    current = root
    for part in local.parts:
        if part == "..":
            raise ExecutionError("Parent traversal is not allowed")
        current /= part
        if current.is_symlink():
            raise ExecutionError("Symlink paths are not admitted")
    target = candidate.resolve()
    if not target.is_relative_to(root) or (directory and not target.is_dir()):
        raise ExecutionError("Command working directory is unavailable")
    return target


def _snapshot(root: Path, *, source=False) -> tuple[dict[str, bytes], list[str]]:
    """Read independent regular files, bounded, without following directory links."""
    files, excluded, total = {}, [], 0
    for directory, dirs, names in os.walk(root, followlinks=False):
        retained = []
        for name in sorted(dirs):
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink() or is_sensitive_file(relative) or (source and name in IGNORED_DIRS):
                excluded.append(relative + "/")
            else:
                retained.append(name)
        dirs[:] = retained
        for name in sorted(names):
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            if is_sensitive_file(relative):
                excluded.append(relative)
                continue
            try:
                descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
                with os.fdopen(descriptor, "rb") as stream:
                    info = os.fstat(stream.fileno())
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink > 1 or path.is_symlink():
                        excluded.append(relative)
                        continue
                    # Detect parent substitution before admitting data. A hostile
                    # host process racing this scan is outside this threat boundary.
                    if not path.resolve().is_relative_to(root):
                        raise ExecutionError("Workspace changed during admission")
                    if info.st_size > _MAX_BYTES - total or len(files) >= _MAX_FILES:
                        raise ExecutionError("Workspace admission/artifact size limit exceeded")
                    data = stream.read(_MAX_BYTES - total + 1)
                    if len(data) > _MAX_BYTES - total:
                        raise ExecutionError("Workspace admission/artifact size limit exceeded")
                    if source and _SECRET.search(data):
                        excluded.append(relative)
                        continue
                    files[relative] = data
                    total += len(data)
            except OSError as exc:
                if path.is_symlink():
                    excluded.append(relative)
                    continue
                raise ExecutionError(f"Workspace file unavailable: {relative}") from exc
    return files, excluded


def _hashes(files: Mapping[str, bytes]) -> dict[str, str]:
    return {name: hashlib.sha256(value).hexdigest() for name, value in files.items()}


@runtime_checkable
class ExecutionBackend(Protocol):
    """Authorization-independent lifecycle contract; no backend may widen policy."""
    config: ExecutionConfig

    async def create_workspace(self, root: str | Path, workspace_id: str | None = None) -> ExecutionWorkspace: ...
    async def prepare_environment(self, workspace: ExecutionWorkspace) -> BackendAvailability: ...
    async def refresh_workspace(self, workspace: ExecutionWorkspace) -> None: ...
    async def execute_command(self, workspace: ExecutionWorkspace, request: CommandRequest) -> CommandResult: ...
    def stream_output(self, command_id: str) -> AsyncIterator[OutputEvent]: ...
    async def cancel_command(self, command_id: str) -> bool: ...
    async def inspect_status(self, workspace: ExecutionWorkspace | None = None) -> dict: ...
    async def collect_artifacts(self, workspace: ExecutionWorkspace) -> list[ArtifactRecord]: ...
    async def destroy_workspace(self, workspace: ExecutionWorkspace, *, discard: bool = False) -> None: ...


class HostExecutionBackend:
    """Host process lifecycle only; this is not an OS sandbox or authorization gate."""
    def __init__(self, config: ExecutionConfig | None = None):
        self.config = config or ExecutionConfig()
        if self.config.backend != "host":
            raise ValueError("Host backend cannot accept a required Docker configuration")
        self.workspaces: dict[str, ExecutionWorkspace] = {}
        self.commands: dict[str, _Running] = {}
        self._workspace_locks: dict[str, asyncio.Lock] = {}

    async def availability(self) -> BackendAvailability:
        return BackendAvailability(True, "host", "Local user permissions; no OS isolation")

    async def create_workspace(self, root: str | Path, workspace_id: str | None = None) -> ExecutionWorkspace:
        root = Path(root).resolve()
        if not root.is_dir():
            raise ExecutionError("Workspace directory does not exist")
        identity = workspace_id or "workspace_" + uuid4().hex
        if identity in self.workspaces or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", identity):
            raise ExecutionError("Workspace identifier is invalid or already in use")
        workspace = ExecutionWorkspace(identity, root, root, "host")
        self.workspaces[identity] = workspace
        self._workspace_locks[identity] = asyncio.Lock()
        return workspace

    def _owned(self, workspace: ExecutionWorkspace):
        if self.workspaces.get(workspace.workspace_id) is not workspace or workspace.state == "destroyed":
            raise ExecutionError("Workspace is not owned by this backend")

    async def prepare_environment(self, workspace: ExecutionWorkspace) -> BackendAvailability:
        self._owned(workspace)
        return await self.availability()

    async def refresh_workspace(self, workspace: ExecutionWorkspace) -> None:
        self._owned(workspace)

    async def _publish(self, record: _Running, event: OutputEvent):
        async with record.condition:
            record.events.append(event)
            # The retained events share the same bounded budget as output. Events
            # are never backpressure on a child's pipes.
            if len(record.events) > 128:
                del record.events[:-128]
            record.condition.notify_all()

    async def stream_output(self, command_id: str) -> AsyncIterator[OutputEvent]:
        record = self.commands.get(command_id)
        if record is None:
            raise ExecutionError("Unknown command")
        delivered = set()
        while True:
            async with record.condition:
                pending = [event for event in record.events if id(event) not in delivered]
                if not pending and record.state not in {"starting", "running", "stopping"}:
                    return
                if not pending:
                    await record.condition.wait()
                    continue
            for event in pending:
                delivered.add(id(event))
                yield event

    async def _spawn(self, args: list[str], *, cwd: Path | None = None,
                     env: dict[str, str] | None = None, raw_cmd: str | None = None):
        options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if sys.platform == "win32" else {"start_new_session": True}
        kwargs = dict(cwd=cwd, env=env, stdin=asyncio.subprocess.DEVNULL,
                      stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, **options)
        if raw_cmd is not None:
            return await asyncio.create_subprocess_shell(raw_cmd, executable=args[0], **kwargs)
        return await asyncio.create_subprocess_exec(*args, **kwargs)

    async def _run_process(self, args: list[str], request: CommandRequest, record: _Running,
                           *, cwd: Path | None = None, env: dict[str, str] | None = None,
                           raw_cmd: str | None = None) -> tuple[int | None, str, str, bool, bool]:
        process = await self._spawn(args, cwd=cwd, env=env, raw_cmd=raw_cmd)
        record.process, record.state = process, "running"
        buffers = [bytearray(), bytearray()]
        truncated = False

        async def capture(stream, number, label):
            nonlocal truncated
            while chunk := await stream.read(8192):
                available = max(0, self.config.output_limit - len(buffers[number]))
                keep = chunk[:available]
                buffers[number].extend(keep)
                truncated |= len(chunk) > available
                if keep:
                    await self._publish(record, OutputEvent(request.command_id, label, keep.decode("utf-8", errors="replace")))

        readers = [asyncio.create_task(capture(process.stdout, 0, "stdout")),
                   asyncio.create_task(capture(process.stderr, 1, "stderr"))]
        completion = asyncio.gather(process.wait(), *readers)
        timed_out = False
        try:
            await asyncio.wait_for(asyncio.shield(completion), timeout=request.timeout)
        except asyncio.TimeoutError:
            timed_out = True
            await _stop(process)
            await asyncio.wait_for(asyncio.shield(completion), timeout=5)
        except asyncio.CancelledError:
            await _stop(process)
            try:
                await asyncio.wait_for(asyncio.shield(completion), timeout=5)
            finally:
                for reader in readers:
                    reader.cancel()
            raise
        finally:
            # Also remove children that deliberately closed their output pipes.
            if sys.platform != "win32":
                await _stop(process)
            if not completion.done():
                completion.cancel()
        return process.returncode, *(data.decode("utf-8", errors="replace") for data in buffers), truncated, timed_out

    def _validate_request(self, request: CommandRequest):
        if not isinstance(request.command, str) or not request.command.strip():
            raise ExecutionError("Command cannot be empty")
        if isinstance(request.timeout, bool) or not isinstance(request.timeout, (int, float)) or not math.isfinite(request.timeout) or not 0 < request.timeout <= self.config.max_timeout:
            raise ExecutionError("Command timeout exceeds the configured bound")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", request.command_id) or request.command_id in self.commands:
            raise ExecutionError("Command identifier is invalid or already used")

    async def execute_command(self, workspace: ExecutionWorkspace, request: CommandRequest) -> CommandResult:
        self._owned(workspace)
        async with self._workspace_locks[workspace.workspace_id]:
            # Recheck after waiting: a queued duplicate identifier must not
            # execute twice or overwrite the first command's uncertain receipt.
            self._owned(workspace)
            self._validate_request(request)
            return await self._execute(workspace, request)

    async def _execute(self, workspace: ExecutionWorkspace, request: CommandRequest) -> CommandResult:
        record = _Running(workspace.workspace_id, task=asyncio.current_task())
        self.commands[request.command_id] = record
        result = CommandResult(request.command_id, workspace.workspace_id, "host", "host", "starting")
        started = time.monotonic()
        try:
            cwd = _scoped(workspace.root, request.cwd, directory=True)
            args = shell_args(request.command, request.shell)
            env = None if not request.environment else {**os.environ, **dict(request.environment)}
            raw = request.command if sys.platform == "win32" and Path(args[0]).name.lower() in {"cmd", "cmd.exe"} else None
            result.exit_code, result.stdout, result.stderr, result.truncated, result.timed_out = await self._run_process(args, request, record, cwd=cwd, env=env, raw_cmd=raw)
            result.status = "timed_out" if result.timed_out else "completed"
        except asyncio.CancelledError:
            result.status, result.cancelled = "cancelled", True
            raise
        except (OSError, ValueError, ExecutionError) as exc:
            result.status, result.stderr = "error", str(exc)
        finally:
            result.duration_seconds = time.monotonic() - started
            record.result, record.state = result, result.status
            async with record.condition:
                record.condition.notify_all()
        return result

    async def cancel_command(self, command_id: str) -> bool:
        record = self.commands.get(command_id)
        if not record or record.state not in {"starting", "running"}:
            return False
        record.state = "stopping"
        record.task.cancel()
        if record.task is not asyncio.current_task():
            try:
                await record.task
            except asyncio.CancelledError:
                pass
        return True

    async def inspect_status(self, workspace: ExecutionWorkspace | None = None) -> dict:
        if workspace:
            self._owned(workspace)
        availability = await self.availability()
        selected = [workspace] if workspace else list(self.workspaces.values())
        return {"backend": self.config.backend, "boundary": "sandbox" if self.config.backend == "docker" else "host",
                "availability": asdict(availability), "profile": self.config.profile,
                "resource_enforcement": "container" if self.config.backend == "docker" else "timeout_and_output_only",
                "network": ("bridge" if self.config.network else "none") if self.config.backend == "docker" else "host",
                "limits": {key: getattr(self.config, key) for key in ("cpus", "memory_mb", "pids_limit", "max_timeout", "tmpfs_mb", "output_limit")},
                "permissions": "Caller permission and approval policy still applies",
                "workspaces": [{"workspace_id": item.workspace_id, "root": str(item.root), "source_root": str(item.source_root),
                                "state": item.state, "excluded": list(item.excluded),
                                "cleanup_eligible": not any(c.workspace_id == item.workspace_id and c.state in {"starting", "running", "stopping"} for c in self.commands.values())}
                               for item in selected],
                "commands": [{"command_id": key, "workspace_id": value.workspace_id, "state": value.state,
                              "container": value.container, "pid": value.process.pid if value.process else None,
                              "cleanup_error": value.result.cleanup_error if value.result else ""}
                             for key, value in self.commands.items() if workspace is None or value.workspace_id == workspace.workspace_id]}

    async def collect_artifacts(self, workspace: ExecutionWorkspace) -> list[ArtifactRecord]:
        self._owned(workspace)
        return []  # Host edits already belong to the user's existing review flow.

    async def destroy_workspace(self, workspace: ExecutionWorkspace, *, discard: bool = False) -> None:
        self._owned(workspace)
        for key, record in list(self.commands.items()):
            if record.workspace_id == workspace.workspace_id:
                await self.cancel_command(key)
        workspace.state = "destroyed"  # Never delete a host checkout.


class DockerExecutionBackend(HostExecutionBackend):
    """Linux containers on a local Docker daemon, with a private source snapshot."""
    def __init__(self, config: ExecutionConfig | None = None):
        self.config = config or ExecutionConfig(backend="docker")
        if self.config.backend != "docker":
            raise ValueError("Docker backend requires an explicit Docker configuration")
        self.workspaces, self.commands, self._workspace_locks = {}, {}, {}
        self._docker = shutil.which("docker")
        self._endpoint = ""
        self._availability: BackendAvailability | None = None

    async def _docker_call(self, *args: str, timeout: float = 10) -> tuple[int, str, str]:
        if not self._docker:
            raise SandboxUnavailable("Docker CLI is not installed")
        # Trusted Docker CLI may read its local context. Its environment is never
        # forwarded to the container; image pulling is explicitly disabled.
        selecting = args[:2] == ("context", "inspect")
        env = self._docker_environment(selecting=selecting)
        argv = [self._docker, *args] if selecting else self._docker_argv(*args)
        command = CommandRequest("docker", timeout=timeout)
        record = _Running("docker-control")
        code, out, err, _, timed_out = await self._run_process(argv, command, record, env=env)
        if timed_out:
            raise SandboxUnavailable("Docker daemon did not respond within the prerequisite/cleanup timeout")
        return code or 0, out, err

    @staticmethod
    def _docker_environment(*, selecting=False):
        allowed = {"PATH", "HOME", "USERPROFILE", "SystemRoot", "SYSTEMROOT", "DOCKER_CONFIG"}
        if selecting:
            allowed |= {"DOCKER_CONTEXT", "DOCKER_HOST"}
        return {key: value for key, value in os.environ.items() if key in allowed}

    def _docker_argv(self, *args):
        # Pin every daemon operation, including attach and cleanup, to the
        # validated local socket. Environment/context drift cannot reroute it.
        if not _local_endpoint(self._endpoint):
            raise SandboxUnavailable("A validated local Docker endpoint is required")
        return [self._docker, "--host", self._endpoint, *args]

    async def availability(self) -> BackendAvailability:
        try:
            endpoint = self._endpoint
            if not endpoint:
                host = os.environ.get("DOCKER_HOST", "")
                if host and os.environ.get("DOCKER_CONTEXT"):
                    raise SandboxUnavailable("Choose DOCKER_HOST or DOCKER_CONTEXT explicitly, not both")
                if host and not _local_endpoint(host):
                    raise SandboxUnavailable("Remote Docker endpoints are not supported")
                code, out, _ = await self._docker_call("context", "inspect", "--format", "{{json .Endpoints.docker}}")
                if code:
                    raise SandboxUnavailable("Docker context is unavailable")
                endpoint = host or json.loads(out).get("Host", "")
            if not _local_endpoint(endpoint):
                raise SandboxUnavailable("Only a local Unix/named-pipe Docker endpoint is supported")
            self._endpoint = endpoint
            code, out, _ = await self._docker_call("info", "--format", "{{json .}}")
            if code:
                raise SandboxUnavailable("Docker daemon is unavailable; start Docker and retry explicitly")
            info = json.loads(out)
            if info.get("OSType") != "linux":
                raise SandboxUnavailable("Only Linux containers are supported")
            seccomp = [option for option in info.get("SecurityOptions", []) if "seccomp" in option]
            if not seccomp or any("unconfined" in option for option in seccomp):
                raise SandboxUnavailable("Docker daemon does not advertise seccomp support")
            self._endpoint = endpoint
            value = BackendAvailability(True, "docker", "Local Linux Docker; private filtered workspace; default seccomp", endpoint=endpoint, isolation="linux_container")
        except (OSError, ValueError, TypeError, SandboxUnavailable) as exc:
            value = BackendAvailability(False, "docker", str(exc), isolation="unavailable")
        self._availability = value
        return value

    @staticmethod
    def storage_root() -> Path:
        """User-scoped metadata survives application crashes (OS temp retention applies)."""
        identity = str(os.getuid()) if hasattr(os, "getuid") else hashlib.sha256(str(Path.home()).encode()).hexdigest()[:16]
        root = Path(tempfile.gettempdir()).resolve() / ("firik-sandboxes-" + identity)
        if root.is_symlink():
            raise ExecutionError("Sandbox metadata root cannot be a symlink")
        root.mkdir(mode=0o700, exist_ok=True)
        info = root.stat()
        if hasattr(os, "getuid") and info.st_uid != os.getuid():
            raise ExecutionError("Sandbox metadata root has an unexpected owner")
        if sys.platform != "win32" and info.st_mode & 0o077:
            raise ExecutionError("Sandbox metadata root must be private")
        return root

    @classmethod
    def retained_workspaces(cls) -> list[dict]:
        """Discover metadata only. Never run saved commands or delete saved artifacts."""
        output = []
        for storage in sorted(cls.storage_root().iterdir()):
            if storage.is_symlink() or not storage.is_dir():
                continue
            metadata = storage / "workspace.json"
            try:
                if metadata.is_symlink() or metadata.stat().st_nlink != 1 or metadata.stat().st_size > 4 * 1024 * 1024:
                    continue
                payload = json.loads(metadata.read_text(encoding="utf-8"))
                root = storage / "workspace"
                identity = payload.get("workspace_id", "")
                if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", identity) or storage.name != identity or root.is_symlink() or Path(payload["root"]) != root:
                    continue
                output.append({"workspace_id": identity, "root": str(root), "source_root": payload["source_root"],
                               "metadata_path": str(metadata), "state": payload.get("state", "unknown"),
                               "endpoint": payload.get("endpoint", ""),
                               "containers": payload.get("containers", []), "artifacts_preserved": root.is_dir()})
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return output

    async def recover_workspace(self, workspace_id: str) -> ExecutionWorkspace:
        """Reattach crash metadata and terminate only its label-verified containers.

        This is an explicit reconciliation action, never a retry of prior commands.
        Artifacts remain on disk even when daemon cleanup cannot be confirmed.
        """
        retained = next((item for item in self.retained_workspaces() if item["workspace_id"] == workspace_id), None)
        if retained is None or workspace_id in self.workspaces:
            raise ExecutionError("Retained workspace is unavailable or already attached")
        payload = json.loads(Path(retained["metadata_path"]).read_text(encoding="utf-8"))
        endpoint = payload.get("endpoint", "")
        if not _local_endpoint(endpoint):
            raise ExecutionError("Recovery requires the original validated local Docker endpoint; metadata is retained")
        if self._endpoint and self._endpoint != endpoint:
            raise ExecutionError("Use a separate backend to recover a workspace on another Docker endpoint")
        self._endpoint = endpoint
        baseline = payload.get("baseline", {})
        if not isinstance(baseline, dict) or any(not isinstance(name, str) or not isinstance(value, str) for name, value in baseline.items()):
            raise ExecutionError("Retained workspace metadata is invalid")
        workspace = ExecutionWorkspace(workspace_id, Path(retained["root"]), Path(retained["source_root"]),
                                       "docker", True, tuple(payload.get("excluded", [])), payload.get("source_digest", ""), baseline,
                                       endpoint=endpoint)
        self.workspaces[workspace_id] = workspace
        self._workspace_locks[workspace_id] = asyncio.Lock()
        recovered = []
        for container in retained["containers"]:
            if not isinstance(container, str) or not re.fullmatch(r"firik-[a-f0-9]{32}", container):
                workspace.state = "cleanup_unresolved"
                continue
            record = _Running(workspace_id, container=container, state="uncertain")
            self.commands["recovered_" + uuid4().hex] = record
            recovered.append(record)
        available = await self.availability()
        if not available.available:
            workspace.state = "cleanup_unresolved"
            self._persist(workspace)
            return workspace
        for record in recovered:
            container = record.container
            try:
                code, out, err = await self._docker_call("inspect", "--format", "{{json .Config.Labels}}", container)
                if code and "No such" in err:
                    record.container = None
                    record.state = "cleaned"
                    continue
                labels = json.loads(out) if not code else {}
                if labels.get("io.firik.sandbox") != "true" or labels.get("io.firik.workspace") != workspace_id:
                    workspace.state = "cleanup_unresolved"
                    continue
                error = await self._remove_container(record)
                if error:
                    workspace.state = "cleanup_unresolved"
                else:
                    record.container, record.state = None, "cleaned"
            except (OSError, ValueError, ExecutionError):
                workspace.state = "cleanup_unresolved"
        self._persist(workspace)
        return workspace

    def _persist(self, workspace: ExecutionWorkspace):
        metadata = workspace.root.parent / "workspace.json"
        payload = {"workspace_id": workspace.workspace_id, "root": str(workspace.root), "source_root": str(workspace.source_root),
                   "backend": "docker", "state": workspace.state, "source_digest": workspace.source_digest,
                   "endpoint": workspace.endpoint,
                   "baseline": workspace.baseline, "excluded": workspace.excluded,
                   "containers": [item.container for item in self.commands.values() if item.workspace_id == workspace.workspace_id and item.container]}
        temporary = metadata.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(metadata)

    async def create_workspace(self, root: str | Path, workspace_id: str | None = None) -> ExecutionWorkspace:
        source = Path(root).resolve()
        safe_project_path(source, ".")
        if not source.is_dir() or source.parent == source or source == Path.home().resolve() or is_sensitive_file(source.name):
            raise ExecutionError("A scoped project directory is required")
        identity = workspace_id or "workspace_" + uuid4().hex
        if identity in self.workspaces or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", identity):
            raise ExecutionError("Workspace identifier is invalid or already in use")
        available = await self.availability()
        if not available.available:
            raise SandboxUnavailable(available.diagnostic)
        files, excluded = await asyncio.to_thread(_snapshot, source, source=True)
        storage = self.storage_root() / identity
        try:
            storage.mkdir(mode=0o700)
        except FileExistsError as exc:
            raise WorkspaceConflict("Persistent workspace identifier collision") from exc
        staging = storage / "workspace"
        staging.mkdir(mode=0o700)
        try:
            for name, data in files.items():
                target = staging / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                # Preserve executable admission, never setuid/setgid.
                target.chmod(0o700 if (source / name).stat().st_mode & 0o111 else 0o600)
            baseline = _hashes(files)
            workspace = ExecutionWorkspace(identity, staging, source, "docker", True, tuple(excluded), _digest(baseline), baseline)
            workspace.endpoint = self._endpoint
            self.workspaces[identity] = workspace
            self._workspace_locks[identity] = asyncio.Lock()
            self._persist(workspace)
            return workspace
        except BaseException:
            shutil.rmtree(storage)
            raise

    async def prepare_environment(self, workspace: ExecutionWorkspace) -> BackendAvailability:
        self._owned(workspace)
        available = await self.availability()
        if not available.available:
            raise SandboxUnavailable(available.diagnostic)
        code, image_volumes, _ = await self._docker_call("image", "inspect", "--format", "{{json .Config.Volumes}}", self.config.image)
        if code:
            raise SandboxUnavailable(f"Docker image is not available locally: {self.config.image}; pull an approved image explicitly")
        try:
            if json.loads(image_volumes):
                raise SandboxUnavailable("Images with implicit writable VOLUME mounts are not supported")
        except ValueError as exc:
            raise SandboxUnavailable("Docker image mount configuration could not be verified") from exc
        return available

    async def refresh_workspace(self, workspace: ExecutionWorkspace) -> None:
        self._owned(workspace)
        source, excluded = await asyncio.to_thread(_snapshot, workspace.source_root, source=True)
        staged, _ = await asyncio.to_thread(_snapshot, workspace.root)
        hashes, current = _hashes(source), _hashes(staged)
        changes = [name for name in hashes.keys() | workspace.baseline.keys() if hashes.get(name) != workspace.baseline.get(name)]
        conflicts = [name for name in changes if current.get(name) != workspace.baseline.get(name)]
        if conflicts:
            raise WorkspaceConflict("Host changes overlap isolated artifacts: " + ", ".join(sorted(conflicts)[:20]))
        for name in changes:
            target = _scoped(workspace.root, name)
            if name not in source:
                target.unlink(missing_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(source[name])
                target.chmod(0o700 if (workspace.source_root / name).stat().st_mode & 0o111 else 0o600)
        workspace.baseline, workspace.excluded = hashes, tuple(excluded)
        workspace.source_digest = _digest(hashes)
        self._persist(workspace)

    def _container_args(self, workspace: ExecutionWorkspace, request: CommandRequest, container: str) -> list[str]:
        cwd = _scoped(workspace.root, request.cwd, directory=True).relative_to(workspace.root).as_posix()
        if request.shell not in {None, "/bin/sh", "/bin/bash"}:
            raise ExecutionError("Docker supports explicit /bin/sh or /bin/bash only")
        if any(key not in self.config.environment_allowlist or not isinstance(value, str) or "\x00" in value or len(value) > 4096 for key, value in request.environment.items()):
            raise ExecutionError("Container environment exceeds the explicit allowlist")
        if "," in str(workspace.root):
            raise ExecutionError("Workspace mount path contains unsupported punctuation")
        uid = os.getuid() if hasattr(os, "getuid") and os.getuid() != 0 else 65534
        gid = os.getgid() if hasattr(os, "getgid") and os.getuid() != 0 else 65534
        args = ["create", "--pull=never", "--no-healthcheck", "--log-driver=none", "--name", container, "--label", "io.firik.sandbox=true",
                "--label", f"io.firik.workspace={workspace.workspace_id}",
                "--user", f"{uid}:{gid}", "--read-only", "--cap-drop=ALL", "--security-opt", "no-new-privileges:true",
                "--network", "bridge" if self.config.network else "none",
                "--cpus", str(self.config.cpus), "--memory", f"{self.config.memory_mb}m", "--memory-swap", f"{self.config.memory_mb}m",
                "--pids-limit", str(self.config.pids_limit), "--ulimit", "nofile=256:256", "--ulimit", "core=0:0",
                "--tmpfs", f"/tmp:rw,noexec,nosuid,nodev,size={self.config.tmpfs_mb}m,mode=1777",
                "--mount", f"type=bind,src={workspace.root},dst=/workspace,bind-propagation=rprivate",
                "--workdir", "/workspace" + ("/" + cwd if cwd != "." else ""),
                "--env", "HOME=/tmp", "--env", "TMPDIR=/tmp"]
        for key, value in sorted(request.environment.items()):
            args += ["--env", f"{key}={value}"]
        # Trusted image defaults could contain credentials or privileged startup
        # scripts. Override entrypoint and clear the environment inside the image.
        clean_env = ["PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", "HOME=/tmp", "TMPDIR=/tmp"]
        clean_env += [f"{key}={value}" for key, value in sorted(request.environment.items())]
        return args + ["--entrypoint", "/usr/bin/env", self.config.image, "-i", *clean_env, request.shell or "/bin/sh", "-c", request.command]

    async def _remove_container(self, record: _Running) -> str:
        if not record.container:
            return ""
        try:
            inspected, payload, detail = await self._docker_call("inspect", "--format", "{{json .Config.Labels}}", record.container)
            if inspected and "No such" in detail:
                record.container = None
                return ""
            labels = json.loads(payload) if not inspected else {}
            if labels.get("io.firik.sandbox") != "true" or labels.get("io.firik.workspace") != record.workspace_id:
                return "Container ownership could not be verified; no unrelated container was removed"
            code, _, err = await self._docker_call("rm", "--force", record.container, timeout=10)
            if code and "No such container" not in err:
                return "Docker container removal could not be confirmed; explicit cleanup is required"
        except (OSError, ValueError, ExecutionError) as exc:
            return str(exc)
        record.container = None
        return ""

    async def _execute(self, workspace: ExecutionWorkspace, request: CommandRequest) -> CommandResult:
        record = _Running(workspace.workspace_id, task=asyncio.current_task(), container="firik-" + uuid4().hex)
        self.commands[request.command_id] = record
        result = CommandResult(request.command_id, workspace.workspace_id, "docker", "sandbox", "starting", network="bridge" if self.config.network else "none")
        started, initial_digest, before = time.monotonic(), workspace.source_digest, {}
        cancelled = False
        try:
            await self.prepare_environment(workspace)
            await self.refresh_workspace(workspace)
            initial_digest = workspace.source_digest
            before = _hashes((await asyncio.to_thread(_snapshot, workspace.root))[0])
            args = self._container_args(workspace, request, record.container)
            self._persist(workspace)  # Recoverable name is saved before create.
            code, _, err = await self._docker_call(*args)
            if code:
                raise SandboxUnavailable("Docker container creation failed: " + err[:1000])
            code, result.stdout, result.stderr, result.truncated, result.timed_out = await self._run_process(
                self._docker_argv("start", "--attach", record.container), request, record,
                env=self._docker_environment())
            if result.timed_out:
                result.status = "timed_out"
            else:
                inspected, payload, _ = await self._docker_call("inspect", "--format", "{{json .State}}", record.container)
                if inspected:
                    result.status = "uncertain"
                    result.stderr += "\nContainer exit status could not be confirmed; command must not be retried automatically"
                else:
                    state = json.loads(payload)
                    if state.get("Running"):
                        result.status = "uncertain"
                        result.stderr += "\nDocker attach ended while the container was still running"
                    else:
                        result.exit_code, result.status = state.get("ExitCode"), "completed"
                        if state.get("OOMKilled"):
                            result.stderr += "\nContainer exceeded its memory limit"
        except asyncio.CancelledError:
            result.status, result.cancelled, cancelled = "cancelled", True, True
        except SandboxUnavailable as exc:
            result.status, result.stderr = "unavailable", str(exc)
        except (OSError, ValueError, ExecutionError) as exc:
            result.status, result.stderr = "error", str(exc)
        finally:
            # Killing only the Docker CLI would leave container processes alive.
            # Container removal also terminates daemonized/detached descendants.
            async def finalize():
                result.cleanup_error = await self._remove_container(record)
                if result.cleanup_error:
                    result.status = "uncertain"
                try:
                    files, withheld = await asyncio.to_thread(_snapshot, workspace.root)
                    after = _hashes(files)
                    result.artifacts_changed = sorted(set(withheld) | {name for name in before.keys() | after.keys() if before.get(name) != after.get(name)})
                    host, _ = await asyncio.to_thread(_snapshot, workspace.source_root, source=True)
                    result.source_unchanged = initial_digest == _digest(_hashes(host))
                    staged_source, _ = await asyncio.to_thread(_snapshot, workspace.root, source=True)
                    result.source_tree_matches = _hashes(staged_source) == workspace.baseline
                except (OSError, ExecutionError):
                    result.source_unchanged = result.source_tree_matches = False
                result.duration_seconds = time.monotonic() - started
                record.result, record.state = result, result.status
                self._persist(workspace)
                async with record.condition:
                    record.condition.notify_all()
            cleanup = asyncio.create_task(finalize())
            while True:
                try:
                    await asyncio.shield(cleanup)
                    break
                except asyncio.CancelledError:
                    # Repeated Stop/Ctrl+C cannot interrupt removal or lose the
                    # retained container identity and completion receipt.
                    cancelled = True
        if cancelled:
            raise asyncio.CancelledError
        return result

    async def collect_artifacts(self, workspace: ExecutionWorkspace) -> list[ArtifactRecord]:
        self._owned(workspace)
        if any(record.workspace_id == workspace.workspace_id and record.state in {"starting", "running", "stopping"} for record in self.commands.values()):
            raise ExecutionError("Artifacts cannot be collected while a command is running")
        files, withheld = await asyncio.to_thread(_snapshot, workspace.root)
        current = _hashes(files)
        artifacts = [ArtifactRecord(name, "deleted" if name not in current else "added" if name not in workspace.baseline else "modified",
                               len(files.get(name, b"")), current.get(name, ""), str(workspace.root / name))
                for name in sorted(current.keys() | workspace.baseline.keys()) if current.get(name) != workspace.baseline.get(name)]
        # Unsafe/sensitive output is withheld, but it is still agent work. Its
        # presence prevents automatic deletion of the retained workspace.
        artifacts.extend(ArtifactRecord(name, "withheld", 0, "", str(workspace.root / name)) for name in sorted(set(withheld)))
        return artifacts

    async def inspect_status(self, workspace: ExecutionWorkspace | None = None) -> dict:
        result = await super().inspect_status(workspace)
        for item in result["workspaces"]:
            owned = self.workspaces[item["workspace_id"]]
            item["endpoint"] = owned.endpoint
            item["not_running"] = item["cleanup_eligible"]
            unresolved = any(record.workspace_id == owned.workspace_id and record.container
                             for record in self.commands.values())
            try:
                artifacts = await self.collect_artifacts(owned) if item["not_running"] else None
                item["artifacts_retained"] = len(artifacts) if artifacts is not None else None
                item["cleanup_eligible"] = item["not_running"] and not unresolved and not artifacts
            except (OSError, ExecutionError) as error:
                item.update(cleanup_eligible=False, diagnostic=str(error))
        return result

    async def destroy_workspace(self, workspace: ExecutionWorkspace, *, discard: bool = False) -> None:
        self._owned(workspace)
        for key, record in list(self.commands.items()):
            if record.workspace_id == workspace.workspace_id:
                await self.cancel_command(key)
                error = await self._remove_container(record)
                if error:
                    raise ExecutionError(error)
        artifacts = await self.collect_artifacts(workspace)
        if artifacts and not discard:
            raise WorkspaceConflict("Isolated artifacts retained; collect and review them before explicit discard")
        shutil.rmtree(workspace.root.parent)
        workspace.state = "destroyed"


def build_backend(config: ExecutionConfig | None = None) -> HostExecutionBackend:
    config = config or ExecutionConfig()
    return DockerExecutionBackend(config) if config.backend == "docker" else HostExecutionBackend(config)
