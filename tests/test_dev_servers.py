"""Supervised development servers reuse the existing process-group backend."""
from __future__ import annotations
import asyncio
from pathlib import Path
import os
import socket
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from firik_agent.browser.models import BrowserGrant
from firik_agent.core.execution import CommandResult, ExecutionConfig
from firik_agent.projects.dev_servers import DevServerError, DevServerSupervisor


class Backend:
    def __init__(self):self.task=None;self.request=None;self.destroyed=[]
    async def create_workspace(self,root):return SimpleNamespace(workspace_id='server_workspace')
    async def prepare_environment(self,workspace):return SimpleNamespace(available=True)
    async def execute_command(self,workspace,request):
        self.task=asyncio.current_task();self.request=request
        await asyncio.Event().wait()
    async def cancel_command(self,identifier):
        if self.task is not None and not self.task.done():
            self.task.cancel();await asyncio.gather(self.task,return_exceptions=True)
    async def destroy_workspace(self,workspace):self.destroyed.append(workspace.workspace_id)
    async def inspect_status(self,workspace):return {'commands':[{'command_id':self.request.command_id}]}
    async def stream_output(self,identifier):
        yield SimpleNamespace(text='token=private_dev_server_token\nready\n')
        await asyncio.Event().wait()


def mocked(tmp_path):
    grant=BrowserGrant(True,('http://127.0.0.1:8000',),('http://127.0.0.1:8000',))
    supervisor=DevServerSupervisor(tmp_path,grant_provider=lambda:grant)
    supervisor.backend=Backend()
    supervisor._authorized_origin=AsyncMock(return_value='http://127.0.0.1:8000')
    supervisor._ensure_port_unused=AsyncMock()
    supervisor._probe=AsyncMock(return_value=200)
    return supervisor


@pytest.mark.asyncio
async def test_foreground_ready_reuse_stop_and_independent_backend_workspace(tmp_path):
    supervisor=mocked(tmp_path)
    ready=await supervisor.start('python -m http.server 8000',origin='http://127.0.0.1:8000',ready_timeout=1,lifetime=5)
    assert ready['ready'] and ready['passed'] and not ready['reused']
    assert supervisor.backend.request.timeout==5
    assert (await supervisor.start('python -m http.server 8000',origin='http://127.0.0.1:8000',ready_timeout=1,lifetime=5))['reused']
    with pytest.raises(DevServerError) as error:
        await supervisor.start('npm run dev',origin='http://127.0.0.1:8000',ready_timeout=1,lifetime=5)
    assert error.value.code=='server_already_running'
    assert 'private_dev_server_token' not in str(await supervisor.status())
    assert (await supervisor.stop())['stopped']
    assert supervisor.backend.destroyed==['server_workspace']
    assert (await supervisor.close())['stopped']


@pytest.mark.asyncio
async def test_not_ready_merely_because_process_started(tmp_path):
    supervisor=mocked(tmp_path);supervisor._probe=AsyncMock(return_value=503)
    with pytest.raises(DevServerError) as error:
        await supervisor.start('python -m http.server 8000',origin='http://127.0.0.1:8000',ready_timeout=.15,lifetime=5)
    assert error.value.code=='readiness_timeout'
    assert supervisor.backend.task.done()
    assert supervisor.backend.destroyed==['server_workspace']


@pytest.mark.asyncio
async def test_unhealthy_process_exit_is_not_ready(tmp_path):
    supervisor=mocked(tmp_path)
    async def exited(workspace,request):return CommandResult(request.command_id,workspace.workspace_id,'host','host','completed',exit_code=2,stderr='broken app')
    supervisor.backend.execute_command=exited
    with pytest.raises(DevServerError) as error:
        await supervisor.start('python broken.py',origin='http://127.0.0.1:8000',ready_timeout=1,lifetime=5)
    assert error.value.code=='server_exited'
    assert not (await supervisor.status())['ready']


@pytest.mark.asyncio
async def test_cancel_during_readiness_closes_backend_process_group(tmp_path):
    supervisor=mocked(tmp_path);supervisor._probe=AsyncMock(side_effect=asyncio.TimeoutError)
    task=asyncio.create_task(supervisor.start('python -m http.server 8000',origin='http://127.0.0.1:8000',ready_timeout=1,lifetime=5))
    while supervisor.backend.task is None:await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):await task
    assert supervisor.backend.task.done()
    assert supervisor.backend.destroyed==['server_workspace']


@pytest.mark.asyncio
async def test_invalid_command_origin_cwd_and_lifetime_fail_closed(tmp_path):
    supervisor=DevServerSupervisor(tmp_path,grant_provider=lambda:BrowserGrant())
    for command in ['nohup python app.py','python app.py &','cd app && npm run dev','echo $(id)','start app.exe']:
        with pytest.raises(DevServerError):await supervisor.start(command,origin='http://127.0.0.1:8000')
    for origin in ['http://127.0.0.1','http://127.0.0.1:8000/path','http://192.168.1.2:8000','https://example.com:443']:
        with pytest.raises((DevServerError,ValueError)):await supervisor.start('python app.py',origin=origin)
    with pytest.raises(DevServerError):await supervisor.start('python app.py',origin='http://127.0.0.1:8000',lifetime=301)
    with pytest.raises(ValueError):await supervisor.start('python app.py',cwd='../outside',origin='http://127.0.0.1:8000')
    assert supervisor._task is None


@pytest.mark.asyncio
async def test_docker_has_no_silent_host_fallback(tmp_path):
    supervisor=DevServerSupervisor(tmp_path,ExecutionConfig(backend='docker'))
    with pytest.raises(DevServerError) as error:await supervisor.start('npm run dev',origin='http://127.0.0.1:8000')
    assert error.value.code=='docker_ports_unsupported' and supervisor.backend is None


@pytest.mark.asyncio
async def test_origin_grants_must_include_exact_loopback_port(tmp_path):
    origin='http://127.0.0.1:8000'
    for grant in [BrowserGrant(),BrowserGrant(True,(origin,)),BrowserGrant(True,('http://127.0.0.1:8001',),('http://127.0.0.1:8001',))]:
        supervisor=DevServerSupervisor(tmp_path,grant_provider=lambda:grant)
        with pytest.raises(DevServerError):await supervisor._authorized_origin(origin,require_explicit_port=True)


@pytest.mark.asyncio
async def test_watch_stops_server_after_live_revocation(tmp_path):
    supervisor=mocked(tmp_path)
    await supervisor.start('python app.py',origin='http://127.0.0.1:8000',ready_timeout=1,lifetime=5)
    supervisor._authorized_origin=AsyncMock(side_effect=DevServerError('permission_denied','revoked'))
    await asyncio.wait_for(supervisor._monitor,1)
    assert supervisor.backend.task.done() and (await supervisor.status())['status']=='revoked'
    await supervisor.close()


REAL=pytest.mark.skipif(os.getenv('FIRIK_BROWSER_INTEGRATION')!='1',reason='explicit local process/socket integration opt-in required')


def free_port():
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));return sock.getsockname()[1]


@REAL
@pytest.mark.browser_integration
@pytest.mark.asyncio
async def test_real_http_server_ready_reuse_stop_and_port_cleanup(tmp_path, python_command):
    (tmp_path/'index.html').write_text('<h1>Supervised frontend</h1>')
    port=free_port();origin=f'http://127.0.0.1:{port}'
    grant=BrowserGrant(True,(origin,),(origin,))
    supervisor=DevServerSupervisor(tmp_path,grant_provider=lambda:grant)
    command=python_command('-m', 'http.server', str(port), '--bind', '127.0.0.1')
    try:
        ready=await supervisor.start(command,origin=origin,ready_timeout=5,lifetime=15)
        assert ready['ready'] and ready['http_status']==200
        assert (await supervisor.start(command,origin=origin,ready_timeout=5,lifetime=15))['reused']
        status=await supervisor.status()
        assert status['ready'] and any(process['pid'] for process in status['processes'])
        stopped=await supervisor.stop()
        assert stopped['stopped'] and stopped['passed']
        with pytest.raises(ConnectionRefusedError):await asyncio.open_connection('127.0.0.1',port)
    finally:await supervisor.close()


@REAL
@pytest.mark.browser_integration
@pytest.mark.asyncio
async def test_real_server_lifetime_and_run_cancel_are_enforced(tmp_path, python_command):
    import threading
    port=free_port();origin=f'http://127.0.0.1:{port}';grant=BrowserGrant(True,(origin,),(origin,))
    event=threading.Event();supervisor=DevServerSupervisor(tmp_path,grant_provider=lambda:grant,cancelled=event)
    command=python_command('-m', 'http.server', str(port), '--bind', '127.0.0.1')
    try:
        await supervisor.start(command,origin=origin,ready_timeout=2,lifetime=4)
        event.set();await asyncio.wait_for(supervisor._monitor,2)
        assert not (await supervisor.status())['ready']
        with pytest.raises(ConnectionRefusedError):await asyncio.open_connection('127.0.0.1',port)
    finally:await supervisor.close()


@REAL
@pytest.mark.browser_integration
@pytest.mark.asyncio
async def test_real_server_lifetime_expiry_and_occupied_port_refusal(tmp_path, python_command):
    port=free_port();origin=f'http://127.0.0.1:{port}';grant=BrowserGrant(True,(origin,),(origin,))
    supervisor=DevServerSupervisor(tmp_path,grant_provider=lambda:grant)
    unrelated=DevServerSupervisor(tmp_path,grant_provider=lambda:grant)
    command=python_command('-m', 'http.server', str(port), '--bind', '127.0.0.1')
    try:
        await supervisor.start(command,origin=origin,ready_timeout=.8,lifetime=1.5)
        with pytest.raises(DevServerError) as error:await unrelated.start(command,origin=origin,ready_timeout=.5,lifetime=2)
        assert error.value.code=='port_in_use' and unrelated._task is None
        await asyncio.wait_for(supervisor._monitor,3)
        result=await supervisor.status()
        assert result['status']=='expired' and not result['ready']
        with pytest.raises(ConnectionRefusedError):await asyncio.open_connection('127.0.0.1',port)
    finally:
        await supervisor.close();await unrelated.close()


@pytest.mark.asyncio
async def test_cleanup_failure_keeps_uncertainty_and_blocks_relaunch(tmp_path):
    supervisor=mocked(tmp_path)
    await supervisor.start('python app.py',origin='http://127.0.0.1:8000',ready_timeout=1,lifetime=5)
    workspace=supervisor._workspace
    supervisor.backend.destroy_workspace=AsyncMock(side_effect=OSError('cleanup failed'))
    with pytest.raises(DevServerError) as error:await supervisor.stop()
    assert error.value.requires_reconciliation
    assert supervisor._workspace is workspace
    assert (await supervisor.status())['requires_reconciliation']
    with pytest.raises(DevServerError) as error:await supervisor.start('python app.py',origin='http://127.0.0.1:8000',ready_timeout=1,lifetime=5)
    assert error.value.code=='reconciliation_required'
    supervisor.backend.destroy_workspace=AsyncMock()
    result=await supervisor.close()
    assert not result['passed'] and not result['stopped']


@pytest.mark.asyncio
async def test_health_response_cannot_report_ready_after_concurrent_stop(tmp_path):
    supervisor=mocked(tmp_path)
    await supervisor.start('python app.py',origin='http://127.0.0.1:8000',ready_timeout=1,lifetime=5)
    started=asyncio.Event();release=asyncio.Event()
    async def probe():started.set();await release.wait();return 200
    supervisor._probe=probe
    health=asyncio.create_task(supervisor.status());await started.wait()
    await supervisor.stop();release.set()
    result=await health
    assert not result['ready'] and result['status']=='stopped'


@pytest.mark.asyncio
async def test_completed_backend_error_with_live_process_retains_uncertainty(tmp_path):
    supervisor=mocked(tmp_path)
    await supervisor.start('python app.py',origin='http://127.0.0.1:8000',ready_timeout=1,lifetime=5)
    workspace=supervisor._workspace
    process=SimpleNamespace(returncode=None)
    supervisor.backend.commands={supervisor._command_id:SimpleNamespace(process=process)}
    await supervisor.backend.cancel_command(supervisor._command_id)
    async def failed_receipt():
        return CommandResult(supervisor._command_id,workspace.workspace_id,'host','host','error',exit_code=None,cleanup_error='')
    supervisor._task=asyncio.create_task(failed_receipt());await supervisor._task
    with pytest.raises(DevServerError) as error:await supervisor.stop()
    assert error.value.code=='cleanup_unresolved' and error.value.requires_reconciliation
    assert supervisor._workspace is workspace and not supervisor.backend.destroyed
    status=await supervisor.status()
    assert status['status']=='uncertain' and not status['ready']
    with pytest.raises(DevServerError) as error:
        await supervisor.start('python app.py',origin='http://127.0.0.1:8000',ready_timeout=1,lifetime=5)
    assert error.value.code=='reconciliation_required'
    process.returncode=-15
    await supervisor.close()
