import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
import sys

import pytest

from firik_agent.core.execution import (BackendAvailability, CommandRequest, DockerExecutionBackend,
    ExecutionConfig, ExecutionError, HostExecutionBackend, SandboxUnavailable, WorkspaceConflict, build_backend)


class MockDocker(DockerExecutionBackend):
    """Command construction/lifecycle evidence only: does not enforce isolation."""
    def __init__(self, config=None):
        super().__init__(config)
        self._docker = "docker"
        self.calls = []
        self.fail_remove = False
        self.running_state = False

    async def availability(self):
        self._endpoint = "unix:///mock/docker.sock"
        return BackendAvailability(True, "docker", isolation="mock")

    async def _docker_call(self, *args, timeout=10):
        self.calls.append(args)
        if args[0] == "image":
            return 0, "null", ""
        if args[:3] == ("inspect", "--format", "{{json .Config.Labels}}"):
            record = next(item for item in self.commands.values() if item.container == args[3])
            return 0, json.dumps({"io.firik.sandbox": "true", "io.firik.workspace": record.workspace_id}), ""
        if args[0] == "inspect":
            return 0, json.dumps({"Running": self.running_state, "ExitCode": 0}), ""
        if args[0] == "rm" and self.fail_remove:
            return 1, "", "daemon failure"
        return 0, "id", ""

    async def _run_process(self, args, request, record, **kwargs):
        self.calls.append(tuple(args))
        record.state = "running"
        return 0, "hello", "", False, False


@pytest.mark.asyncio
async def test_host_existing_shell_and_structured_streams(tmp_path, python_command):
    backend = HostExecutionBackend()
    workspace = await backend.create_workspace(tmp_path)
    result = await backend.execute_command(workspace, CommandRequest(python_command("-c", "import sys; sys.stdout.write('hello'); sys.stderr.write('error')")))
    assert result.exit_code == 0
    assert result.stdout == "hello" and result.stderr == "error"
    assert result.boundary == "host" and result.network == "host"
    assert result.format_shell().startswith("Exit code 0:")
    events = [event async for event in backend.stream_output(result.command_id)]
    assert {event.stream for event in events} == {"stdout", "stderr"}
    await backend.destroy_workspace(workspace)
    assert tmp_path.is_dir()


@pytest.mark.asyncio
async def test_timeout_and_output_bound(tmp_path, python_command):
    backend = HostExecutionBackend(ExecutionConfig(max_timeout=2, output_limit=1024))
    workspace = await backend.create_workspace(tmp_path)
    result = await backend.execute_command(workspace, CommandRequest(python_command("-c", "import sys,time; print('x'*2000, flush=True); time.sleep(30)"), timeout=1))
    assert result.timed_out and len(result.stdout.encode()) <= 1024 and result.truncated
    assert backend.commands[result.command_id].process.returncode is not None


@pytest.mark.asyncio
async def test_cancel_process_group(tmp_path):
    backend = HostExecutionBackend()
    workspace = await backend.create_workspace(tmp_path)
    request = CommandRequest("sleep 30 & wait")
    task = asyncio.create_task(backend.execute_command(workspace, request))
    for _ in range(100):
        if request.command_id in backend.commands and backend.commands[request.command_id].process:
            break
        await asyncio.sleep(.001)
    assert await backend.cancel_command(request.command_id)
    assert task.cancelled()
    assert backend.commands[request.command_id].result.cancelled


@pytest.mark.asyncio
async def test_unavailable_never_runs_host(tmp_path, monkeypatch):
    backend = build_backend(ExecutionConfig(backend="docker"))
    backend._docker = None
    monkeypatch.setenv("DOCKER_HOST", "tcp://remote:2375")
    availability = await backend.availability()
    assert not availability.available
    with pytest.raises(SandboxUnavailable):
        await backend.create_workspace(tmp_path)
    assert not (tmp_path / "executed").exists()
    with pytest.raises(ValueError):
        HostExecutionBackend(ExecutionConfig(backend="docker"))


@pytest.mark.asyncio
async def test_docker_mount_limits_and_secret_isolation(tmp_path, monkeypatch):
    (tmp_path / "code.py").write_text("print('hello')")
    (tmp_path / ".env").write_text("SECRET=host")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("credential helper")
    (tmp_path / "credentials.json").write_text("secret")
    (tmp_path / "key-in-source.txt").write_text("sk-proj-" + "a" * 50)
    outside = tmp_path.parent / "outside-secret"
    outside.write_text("outside secret")
    (tmp_path / "escape").symlink_to(outside)
    os.link(outside, tmp_path / "hardlink")
    monkeypatch.setenv("OPENAI_API_KEY", "host-api-key")
    backend = MockDocker()
    workspace = await backend.create_workspace(tmp_path)
    assert workspace.root != tmp_path
    assert list(workspace.root.iterdir()) == [workspace.root / "code.py"]
    result = await backend.execute_command(workspace, CommandRequest("echo hello"))
    assert result.exit_code == 0, result.format_shell()
    create = next(call for call in backend.calls if call[0] == "create")
    assert "--read-only" in create and "--cap-drop=ALL" in create
    assert "no-new-privileges:true" in create and "--pull=never" in create
    assert "--log-driver=none" in create
    assert create[create.index("--network") + 1] == "none"
    assert create[create.index("--user") + 1].split(":")[0] != "0"
    assert create[create.index("--memory") + 1] == "512m"
    assert create[create.index("--cpus") + 1] == "1.0"
    assert create[create.index("--pids-limit") + 1] == "128"
    assert "--privileged" not in create and "--device" not in create
    assert "host-api-key" not in repr(create)
    assert str(tmp_path) not in create[create.index("--mount") + 1]
    assert result.boundary == "sandbox" and result.source_tree_matches and result.source_unchanged
    assert any(call[:2] == ("rm", "--force") for call in backend.calls)
    await backend.destroy_workspace(workspace)


@pytest.mark.asyncio
async def test_artifacts_retained_refresh_conflict_and_no_host_write(tmp_path):
    source = tmp_path / "code.py"
    source.write_text("original")
    backend = MockDocker()
    workspace = await backend.create_workspace(tmp_path)
    (workspace.root / "code.py").write_text("isolated")
    (workspace.root / "output.txt").write_text("artifact")
    artifacts = await backend.collect_artifacts(workspace)
    assert {item.path for item in artifacts} == {"code.py", "output.txt"}
    assert source.read_text() == "original"
    with pytest.raises(WorkspaceConflict, match="retained"):
        await backend.destroy_workspace(workspace)
    source.write_text("host edit")
    with pytest.raises(WorkspaceConflict, match="overlap"):
        await backend.refresh_workspace(workspace)
    assert (workspace.root / "code.py").read_text() == "isolated"
    await backend.destroy_workspace(workspace, discard=True)
    assert source.read_text() == "host edit"


@pytest.mark.asyncio
async def test_refresh_host_changes_preserves_generated_outputs(tmp_path):
    (tmp_path / "code.py").write_text("first")
    backend = MockDocker()
    workspace = await backend.create_workspace(tmp_path)
    (workspace.root / "node_modules").mkdir()
    (workspace.root / "node_modules" / "generated").write_text("private dependency")
    (tmp_path / "code.py").write_text("second")
    await backend.refresh_workspace(workspace)
    assert (workspace.root / "code.py").read_text() == "second"
    assert (workspace.root / "node_modules" / "generated").exists()
    result = await backend.execute_command(workspace, CommandRequest("true"))
    assert result.source_tree_matches
    await backend.destroy_workspace(workspace, discard=True)


@pytest.mark.asyncio
async def test_daemon_failure_exit_uncertain_no_retry(tmp_path):
    backend = MockDocker()
    workspace = await backend.create_workspace(tmp_path)
    backend.fail_remove = True
    backend.running_state = True
    result = await backend.execute_command(workspace, CommandRequest("side effect"))
    assert result.status == "uncertain" and result.cleanup_error
    assert len([call for call in backend.calls if call[0] == "create"]) == 1
    assert backend.commands[result.command_id].result is result
    backend.fail_remove = False
    await backend.destroy_workspace(workspace)


@pytest.mark.asyncio
async def test_docker_cancellation_cleans_container(tmp_path):
    class WaitingDocker(MockDocker):
        async def _run_process(self, args, request, record, **kwargs):
            record.state = "running"
            await asyncio.Event().wait()
    backend = WaitingDocker()
    workspace = await backend.create_workspace(tmp_path)
    request = CommandRequest("sleep 30")
    task = asyncio.create_task(backend.execute_command(workspace, request))
    for _ in range(100):
        if request.command_id in backend.commands and backend.commands[request.command_id].state == "running":
            break
        await asyncio.sleep(.001)
    assert await backend.cancel_command(request.command_id)
    assert task.cancelled()
    assert any(call[:2] == ("rm", "--force") for call in backend.calls)
    await backend.destroy_workspace(workspace)


@pytest.mark.parametrize("kwargs", [
    {"network": True}, {"profile": "custom", "network": True},
    {"profile": "restricted", "network": True, "network_approved": True},
    {"cpus": 100}, {"memory_mb": 2}, {"pids_limit": 50000}, {"max_timeout": float("inf")},
    {"environment_allowlist": ("OPENAI_API_KEY",)}, {"backend": "fallback"}, {"image": "--privileged"},
])
def test_hard_limits(kwargs):
    with pytest.raises(ValueError):
        ExecutionConfig(backend="docker", **{key: value for key, value in kwargs.items() if key != "backend"}) if "backend" not in kwargs else ExecutionConfig(**kwargs)


@pytest.mark.asyncio
async def test_explicit_network_and_allowlist(tmp_path):
    backend = MockDocker(ExecutionConfig(backend="docker", profile="development", network=True, network_approved=True))
    workspace = await backend.create_workspace(tmp_path)
    args = backend._container_args(workspace, CommandRequest("true", environment={"CI": "true"}), "name")
    assert args[args.index("--network") + 1] == "bridge"
    assert "CI=true" in args
    for environment in ({"OPENAI_API_KEY": "secret"}, {"LD_PRELOAD": "evil"}, {"PATH": "/evil"}):
        with pytest.raises(ExecutionError):
            backend._container_args(workspace, CommandRequest("true", environment=environment), "name")
    with pytest.raises(ExecutionError):
        backend._container_args(workspace, CommandRequest("true", cwd="../"), "name")
    await backend.destroy_workspace(workspace)


@pytest.mark.asyncio
async def test_symlink_artifacts_are_never_followed(tmp_path):
    backend = MockDocker()
    workspace = await backend.create_workspace(tmp_path)
    secret = tmp_path / "hidden.txt"
    secret.write_text("secret")
    (workspace.root / "escape").symlink_to(secret)
    artifacts = await backend.collect_artifacts(workspace)
    assert len(artifacts) == 1 and artifacts[0].status == "withheld"
    assert artifacts[0].sha256 == ""  # Target contents are never read.
    with pytest.raises(ExecutionError):
        backend._container_args(workspace, CommandRequest("true", cwd="escape"), "name")
    with pytest.raises(WorkspaceConflict, match="retained"):
        await backend.destroy_workspace(workspace)
    await backend.destroy_workspace(workspace, discard=True)


@pytest.mark.asyncio
async def test_recover_crash_workspace_verifies_container_labels(tmp_path):
    class RecoveryDocker(MockDocker):
        async def _docker_call(self, *args, timeout=10):
            if args[:3] == ("inspect", "--format", "{{json .Config.Labels}}"):
                self.calls.append(args)
                return 0, json.dumps({"io.firik.sandbox": "true", "io.firik.workspace": workspace.workspace_id}), ""
            return await super()._docker_call(*args, timeout=timeout)
    backend = MockDocker()
    workspace = await backend.create_workspace(tmp_path)
    (workspace.root / "artifact.txt").write_text("retained across crash")
    metadata = workspace.root.parent / "workspace.json"
    payload = json.loads(metadata.read_text())
    container = "firik-" + "a" * 32
    payload["containers"] = [container]
    metadata.write_text(json.dumps(payload))
    assert any(item["workspace_id"] == workspace.workspace_id for item in DockerExecutionBackend.retained_workspaces())
    restored_backend = RecoveryDocker()
    restored = await restored_backend.recover_workspace(workspace.workspace_id)
    assert restored.root == workspace.root
    assert (restored.root / "artifact.txt").read_text() == "retained across crash"
    assert ("rm", "--force", container) in restored_backend.calls
    with pytest.raises(WorkspaceConflict):
        await restored_backend.destroy_workspace(restored)
    await restored_backend.destroy_workspace(restored, discard=True)


@pytest.mark.asyncio
async def test_recover_never_kills_unrelated_container(tmp_path):
    class UnrelatedDocker(MockDocker):
        async def _docker_call(self, *args, timeout=10):
            if args[0] == "inspect":
                self.calls.append(args)
                return 0, json.dumps({"io.firik.sandbox": "true", "io.firik.workspace": "another-owner"}), ""
            return await super()._docker_call(*args, timeout=timeout)
    backend = MockDocker()
    workspace = await backend.create_workspace(tmp_path)
    metadata = workspace.root.parent / "workspace.json"
    payload = json.loads(metadata.read_text())
    payload["containers"] = ["firik-" + "b" * 32]
    metadata.write_text(json.dumps(payload))
    restored_backend = UnrelatedDocker()
    restored = await restored_backend.recover_workspace(workspace.workspace_id)
    assert restored.state == "cleanup_unresolved"
    assert not any(call[0] == "rm" for call in restored_backend.calls)
    # Remove only our empty fixture via the original owner.
    await backend.destroy_workspace(workspace)


@pytest.mark.asyncio
async def test_image_implicit_volumes_are_rejected(tmp_path):
    class VolumeDocker(MockDocker):
        async def _docker_call(self, *args, timeout=10):
            if args[0] == "image":
                return 0, '{"/data": {}}', ""
            return await super()._docker_call(*args, timeout=timeout)
    backend = VolumeDocker()
    workspace = await backend.create_workspace(tmp_path)
    result = await backend.execute_command(workspace, CommandRequest("true"))
    assert result.status == "unavailable"
    assert not any(call[0] == "create" for call in backend.calls)
    await backend.destroy_workspace(workspace)


@pytest.mark.asyncio
async def test_duplicate_queued_command_id_never_executes_twice(tmp_path):
    backend = HostExecutionBackend()
    workspace = await backend.create_workspace(tmp_path)
    holder = asyncio.create_task(backend.execute_command(workspace, CommandRequest("sleep .03")))
    await asyncio.sleep(.001)
    request = CommandRequest("printf x >> count")
    first = asyncio.create_task(backend.execute_command(workspace, request))
    duplicate = asyncio.create_task(backend.execute_command(workspace, request))
    await holder
    await first
    with pytest.raises(ExecutionError, match="already used"):
        await duplicate
    assert (tmp_path / "count").read_text() == "x"


@pytest.mark.asyncio
async def test_recovery_preserves_orphan_ids_while_daemon_stopped(tmp_path):
    backend = MockDocker()
    workspace = await backend.create_workspace(tmp_path)
    metadata = workspace.root.parent / "workspace.json"
    payload = json.loads(metadata.read_text())
    container = "firik-" + "c" * 32
    payload["containers"] = [container]
    metadata.write_text(json.dumps(payload))
    class Stopped(MockDocker):
        async def availability(self):
            return BackendAvailability(False, "docker", "stopped")
    recovered = await Stopped().recover_workspace(workspace.workspace_id)
    assert recovered.state == "cleanup_unresolved"
    assert json.loads(metadata.read_text())["containers"] == [container]
    await backend.destroy_workspace(workspace)


@pytest.mark.asyncio
async def test_endpoint_selection_is_unambiguous_and_control_is_pinned(monkeypatch):
    backend = DockerExecutionBackend()
    monkeypatch.setenv("DOCKER_CONTEXT", "remote")
    monkeypatch.setenv("DOCKER_HOST", "unix:///local.sock")
    assert not (await backend.availability()).available
    backend._endpoint = "unix:///validated.sock"
    assert backend._docker_argv("start", "test")[1:3] == ["--host", "unix:///validated.sock"]
    assert "DOCKER_CONTEXT" not in backend._docker_environment()
    assert "DOCKER_HOST" not in backend._docker_environment()


@pytest.mark.asyncio
async def test_sensitive_generated_artifacts_prevent_implicit_discard(tmp_path):
    backend = MockDocker()
    workspace = await backend.create_workspace(tmp_path)
    (workspace.root / ".env").write_text("SECRET=generated-by-command")
    records = await backend.collect_artifacts(workspace)
    assert records[0].status == "withheld"
    with pytest.raises(WorkspaceConflict, match="retained"):
        await backend.destroy_workspace(workspace)
    await backend.destroy_workspace(workspace, discard=True)


@pytest.mark.asyncio
async def test_repeated_cancellation_cannot_interrupt_container_cleanup(tmp_path):
    started, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    class SlowCleanup(MockDocker):
        async def _run_process(self, args, request, record, **kwargs):
            started.set()
            record.state = "running"
            await asyncio.Event().wait()
        async def _remove_container(self, record):
            cleaning.set()
            await release.wait()
            return await super()._remove_container(record)
    backend = SlowCleanup()
    workspace = await backend.create_workspace(tmp_path)
    request = CommandRequest("sleep forever")
    task = asyncio.create_task(backend.execute_command(workspace, request))
    await started.wait()
    task.cancel()
    await cleaning.wait()
    for _ in range(4):
        task.cancel()
        await asyncio.sleep(.001)
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert backend.commands[request.command_id].result.cancelled
    assert not backend.commands[request.command_id].container
    assert json.loads((workspace.root.parent / "workspace.json").read_text())["containers"] == []
    await backend.destroy_workspace(workspace)


@pytest.mark.asyncio
async def test_endpoint_is_retained_across_context_changes_and_recovery(tmp_path, monkeypatch):
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    monkeypatch.delenv("DOCKER_CONTEXT", raising=False)
    class EndpointDocker(DockerExecutionBackend):
        context = "unix:///daemon-a.sock"
        async def _docker_call(self, *args, timeout=10):
            if args[0] == "context":
                return 0, json.dumps({"Host": self.context}), ""
            if args[0] == "info":
                return 0, json.dumps({"OSType": "linux", "SecurityOptions": ["name=seccomp,profile=builtin"]}), ""
            return 1, "", "No such container"
    backend = EndpointDocker()
    workspace = await backend.create_workspace(tmp_path)
    backend.context = "unix:///daemon-b.sock"
    assert (await backend.availability()).endpoint == "unix:///daemon-a.sock"
    metadata = json.loads((workspace.root.parent / "workspace.json").read_text())
    assert metadata["endpoint"] == "unix:///daemon-a.sock"
    recovered_backend = EndpointDocker()
    recovered_backend.context = "unix:///daemon-b.sock"
    recovered = await recovered_backend.recover_workspace(workspace.workspace_id)
    assert recovered.endpoint == recovered_backend._endpoint == "unix:///daemon-a.sock"
    await recovered_backend.destroy_workspace(recovered)


@pytest.mark.asyncio
async def test_cleanup_eligibility_accounts_for_retained_artifacts(tmp_path):
    backend = MockDocker()
    workspace = await backend.create_workspace(tmp_path)
    (workspace.root / "output.txt").write_text("retain")
    status = await backend.inspect_status(workspace)
    assert status["workspaces"][0]["not_running"] is True
    assert status["workspaces"][0]["cleanup_eligible"] is False
    await backend.destroy_workspace(workspace, discard=True)


@pytest.mark.asyncio
async def test_credential_directory_ancestor_cannot_become_sandbox_source(tmp_path):
    source = tmp_path / ".ssh" / "friendly-project-name"
    source.mkdir(parents=True)
    (source / "ordinary.txt").write_text("credential material")
    with pytest.raises(ValueError, match="Hassas"):
        await MockDocker().create_workspace(source)


def test_named_pipe_endpoint_must_address_local_machine():
    from firik_agent.core.execution import _local_endpoint
    assert _local_endpoint("npipe:////./pipe/docker_engine")
    assert _local_endpoint("unix:///var/run/docker.sock")
    assert not _local_endpoint("npipe:////remote-server/pipe/docker_engine")
    assert not _local_endpoint("tcp://127.0.0.1:2375")
