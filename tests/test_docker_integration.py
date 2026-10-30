"""Opt-in real Docker evidence. Mock tests do not prove these boundaries.

Run FIRIK_DOCKER_INTEGRATION=1 pytest tests/test_docker_integration.py after
explicitly pulling the approved image (default python:3.12-slim).
"""
import os
from pathlib import Path

import pytest

from firik_agent.core.execution import CommandRequest, DockerExecutionBackend, ExecutionConfig, SandboxUnavailable

pytestmark = pytest.mark.skipif(os.environ.get("FIRIK_DOCKER_INTEGRATION") != "1", reason="Real Docker integration requires explicit opt-in")


@pytest.mark.asyncio
async def test_real_docker_boundaries(tmp_path, monkeypatch):
    monkeypatch.setenv("FIRIK_TEST_HOST_SECRET", "must-not-enter-container")
    (tmp_path / ".env").write_text("REAL_SECRET=must-not-enter-container")
    (tmp_path / "build.sh").write_text("touch /etc/firik-host-write; touch /workspace/proof; env; id -u")
    backend = DockerExecutionBackend(ExecutionConfig(backend="docker", image=os.environ.get("FIRIK_DOCKER_TEST_IMAGE", "python:3.12-slim")))
    if not (await backend.availability()).available:
        pytest.skip("Docker daemon unavailable")
    workspace = await backend.create_workspace(tmp_path)
    try:
        try:
            await backend.prepare_environment(workspace)
        except SandboxUnavailable as exc:
            pytest.skip(str(exc))
        command = "python -c \"import os,socket; assert os.getuid()!=0; assert not os.path.exists('/workspace/.env'); assert 'FIRIK_TEST_HOST_SECRET' not in os.environ; assert not os.path.exists('/var/run/docker.sock'); assert not os.path.exists('/root/.ssh'); print(open('/proc/self/status').read()); print(open('/sys/fs/cgroup/memory.max').read()); print(open('/sys/fs/cgroup/pids.max').read()); print(open('/sys/fs/cgroup/cpu.max').read()); s=socket.socket(); s.settimeout(.1); r=s.connect_ex(('1.1.1.1',443)); assert r!=0\""
        result = await backend.execute_command(workspace, CommandRequest(command))
        assert result.exit_code == 0, result.format_shell()
        assert "NoNewPrivs:\t1" in result.stdout and "Seccomp:\t2" in result.stdout
        assert "536870912" in result.stdout and "128" in result.stdout
        denied = await backend.execute_command(workspace, CommandRequest("touch /etc/firik-test-denied"))
        assert denied.exit_code != 0
        built = await backend.execute_command(workspace, CommandRequest("/bin/sh /workspace/build.sh"))
        assert "Read-only file system" in built.stderr
        assert (workspace.root / "proof").exists() and not (tmp_path / "proof").exists()
        timeout = await backend.execute_command(workspace, CommandRequest("sleep 30 & wait", timeout=.1))
        assert timeout.timed_out and not timeout.cleanup_error
        assert not any(item["state"] == "running" for item in (await backend.inspect_status())["commands"])
    finally:
        await backend.destroy_workspace(workspace, discard=True)
