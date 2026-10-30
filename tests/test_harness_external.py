"""Offline official-protocol fixtures. These children never call a provider."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys

import pytest

from firik_agent.harness.external import CodexBackend, PI_FLAGS, PiBackend, clean_environment
from firik_agent.harness.models import HarnessError, HarnessExecutionPolicy, HarnessSession, HarnessTask
from firik_agent.harness.transport import JsonlProcess, TransportError

PROVIDER = {"name": "openai", "model": "fixture-model", "api_key": "explicit-fixture-key", "allow_live": True}

# The external transport intentionally fails closed on Windows until process-tree
# cleanup is supported. These protocol fixtures require executable POSIX scripts.
pytestmark = pytest.mark.skipif(os.name == "nt", reason="External harness execution is disabled on Windows")


@pytest.fixture
def fake_pi(tmp_path):
    def create(mode="normal", version="1.4.0"):
        path = tmp_path / ("pi-" + mode)
        source = '''#!PYTHON
import json, os, sys, time
MODE=MODE_VALUE
if '--version' in sys.argv:
 print(VERSION_VALUE)
 raise SystemExit
assert all(flag in sys.argv for flag in ['--no-tools','--no-extensions','--no-mcp','--no-context-files','--offline'])
assert os.getcwd() == os.environ['HOME']
assert 'HOST_SECRET_FIXTURE' not in os.environ
assert 'NODE_OPTIONS' not in os.environ
assert 'SSH_AUTH_SOCK' not in os.environ
assert 'ANTHROPIC_API_KEY' not in os.environ
assert os.environ['OPENAI_API_KEY'] == 'explicit-fixture-key'
def emit(obj):
 data=(json.dumps(obj)+'\\n').encode()
 if MODE == 'fragmented':
  for part in (data[:3],data[3:10],data[10:]):
   sys.stdout.buffer.write(part); sys.stdout.buffer.flush(); time.sleep(.002)
 else:
  sys.stdout.buffer.write(data); sys.stdout.buffer.flush()
for line in sys.stdin:
 command=json.loads(line)
 kind=command['type']
 if kind == 'get_state':
  emit({'type':'response','id':command['id'],'success':True,'data':{'model':{'id':'wrong' if MODE == 'wrong_model' else 'fixture-model','provider':'openai'}}})
 elif kind == 'prompt':
  emit({'type':'response','command':'prompt','id':command['id'],'success':True,'data':{'disposition':'started'}})
  if MODE == 'crash':
   sys.stderr.write('fixture startup detail');sys.stderr.flush();os._exit(19)
  if MODE == 'malformed':
   sys.stdout.write('{oops}\\n');sys.stdout.flush();continue
  if MODE == 'partial':
   sys.stdout.write('{"type":');sys.stdout.flush();os._exit(3)
  if MODE == 'hang':
   continue
  if MODE == 'tool':
   emit({'type':'tool_execution_start','toolName':'bash','args':{'command':'forbidden'}});continue
  if MODE == 'overflow':
   for i in range(100):emit({'type':'turn_start','eventId':str(i)})
   continue
  emit({'type':'agent_start'})
  emit({'type':'message_update','assistantMessageEvent':{'type':'text_delta','delta':'hello\\u2028world'}})
  message={'role':'assistant','model':'fixture-model','content':[{'type':'text','text':'hello\\u2028world'}],'timestamp':1}
  if MODE != 'unknown_usage':
   message['usage']={'input':12,'output':3,'cacheRead':2,'cost':{'total':0.99}}
  if MODE == 'provider_error':
   message['stopReason']='error';message['errorMessage']='provider rejected explicit-fixture-key'
  emit({'type':'message_end','message':message})
  if MODE == 'duplicate':
   emit({'type':'message_end','message':message})
  emit({'type':'agent_end','messages':[],'willRetry':True})
  if MODE == 'retry_after_end':
   emit({'type':'auto_retry_start','attempt':1})
  emit({'type':'agent_settled','aborted':False})
 elif kind == 'clear_queue':
  emit({'type':'response','command':kind,'id':command['id'],'success':True})
 elif kind == 'abort':
  emit({'type':'agent_settled','aborted':True})
  emit({'type':'response','command':kind,'id':command['id'],'success':True})
'''.replace('PYTHON',sys.executable).replace('MODE_VALUE',repr(mode)).replace('VERSION_VALUE',repr(version))
        path.write_text(source)
        path.chmod(0o700)
        return path
    return create


def policy(**kwargs):
    return HarnessExecutionPolicy(allow_network=True, timeout_seconds=2, **kwargs)


async def run_fixture(path, **options):
    backend = PiBackend(str(path))
    session = await backend.initialize_session(policy=policy(**options), provider=PROVIDER)
    run_id = await backend.execute_task(session, HarnessTask("fixture task", model="fixture-model"))
    events = [event async for event in backend.stream_events(session, run_id)]
    result = await backend.inspect_run(session, run_id)
    await backend.close_session(session)
    return result, events


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["normal", "fragmented", "duplicate", "retry_after_end"])
async def test_pi_official_rpc_completion_and_authoritative_usage(fake_pi, mode):
    result, events = await run_fixture(fake_pi(mode))
    assert result.status == "completed"
    assert result.text == "hello\u2028world"
    assert result.model == "fixture-model"
    assert result.usage.input_tokens == 12
    assert result.usage.output_tokens == 3
    assert result.usage.cost_usd is None  # Pi model pricing is not a verified invoice.
    assert not result.completion.verified
    assert events[-1].type == "finished"
    assert result.metadata["process"]["closed"] is True
    assert result.metadata["process"]["returncode"] is not None
    if mode == "retry_after_end":
        assert any(event.type == "recovery" for event in events)


@pytest.mark.asyncio
async def test_unknown_usage_stays_unknown(fake_pi):
    result, _ = await run_fixture(fake_pi("unknown_usage"))
    assert result.usage.input_tokens is None
    assert result.usage.cost_usd is None


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,code", [("crash","disconnected"),("malformed","malformed_frame"),
                                        ("partial","partial_frame"),("tool","policy_violation"),
                                        ("provider_error","provider_error")])
async def test_external_failures_preserve_codes_and_teardown(fake_pi, mode, code):
    result, events = await run_fixture(fake_pi(mode))
    assert result.status == "failed"
    assert result.errors[0].code == code
    assert result.metadata["process"]["closed"]
    assert events[-1].metadata["status"] == "failed"
    assert "explicit-fixture-key" not in json.dumps(result.to_dict())


@pytest.mark.asyncio
async def test_timeout_and_bounded_event_buffer(fake_pi):
    backend = PiBackend(str(fake_pi("hang")))
    session = await backend.initialize_session(policy=HarnessExecutionPolicy(allow_network=True,timeout_seconds=.1), provider=PROVIDER)
    run_id = await backend.execute_task(session,HarnessTask("task"))
    events = [event async for event in backend.stream_events(session,run_id)]
    assert (await backend.inspect_run(session,run_id)).errors[0].code == "timeout"
    assert events[-1].type == "finished"
    await backend.close_session(session)
    result, _ = await run_fixture(fake_pi("overflow"),max_events=2)
    assert result.status == "failed"
    assert result.errors[0].code == "event_overflow"


@pytest.mark.asyncio
async def test_cancel_propagates_and_is_idempotent(fake_pi):
    backend = PiBackend(str(fake_pi("hang")))
    session = await backend.initialize_session(policy=policy(),provider=PROVIDER)
    run_id=await backend.execute_task(session,HarnessTask("task"))
    await asyncio.sleep(.03)
    await backend.request_cancellation(session,run_id)
    await backend.request_cancellation(session,run_id)
    result=await backend.inspect_run(session,run_id)
    assert result.status == "cancelled"
    assert result.metadata["process"]["closed"]
    await backend.close_session(session)
    await backend.close_session(session)


@pytest.mark.asyncio
async def test_credential_and_workspace_isolation(fake_pi, monkeypatch, tmp_path):
    monkeypatch.setenv("HOST_SECRET_FIXTURE","never-forward")
    monkeypatch.setenv("NODE_OPTIONS","--untrusted-loader")
    monkeypatch.setenv("SSH_AUTH_SOCK","/private/secret/socket")
    monkeypatch.setenv("ANTHROPIC_API_KEY","never-forward")
    backend=PiBackend(str(fake_pi()))
    session=await backend.initialize_session(policy=policy(project_id="a"),provider=PROVIDER)
    state=backend._state(session)
    root=state.temporary.name
    assert Path(root).exists()
    forged=HarnessSession(session.id,"pi","b")
    with pytest.raises(HarnessError,match="belongs to another"):
        await backend.execute_task(forged,HarnessTask("task"))
    await backend.close_session(session)
    assert not Path(root).exists()
    with pytest.raises(HarnessError):
        backend.validate_configuration(policy(workspace=str(tmp_path)),PROVIDER)


@pytest.mark.asyncio
async def test_session_limits_and_no_active_model_switch(fake_pi):
    backend=PiBackend(str(fake_pi("hang")))
    session=await backend.initialize_session(policy=policy(max_sessions=1),provider=PROVIDER)
    with pytest.raises(HarnessError,match="limit"):
        await backend.initialize_session(policy=policy(max_sessions=1),provider=PROVIDER)
    with pytest.raises(HarnessError,match="models"):
        await backend.execute_task(session,HarnessTask("task",model="another"))
    await backend.close_session(session)


@pytest.mark.asyncio
async def test_model_negotiation_fails_closed(fake_pi):
    backend=PiBackend(str(fake_pi("wrong_model")))
    with pytest.raises(HarnessError) as caught:
        await backend.initialize_session(policy=policy(),provider=PROVIDER)
    assert caught.value.code == "model_mismatch"
    assert not backend._sessions


def test_missing_unsupported_or_invalid_external_configuration(fake_pi):
    missing=PiBackend("/no/such/pi")
    assert not missing.get_metadata().available
    incompatible=PiBackend(str(fake_pi(version="999.0.0")))
    assert not incompatible.get_metadata().available
    backend=PiBackend(str(fake_pi()))
    for config in ({}, {**PROVIDER,"allow_live":False}, {**PROVIDER,"endpoint":"evil"}):
        with pytest.raises(HarnessError):backend.validate_configuration(policy(),config)
    for config in (policy(require_sandbox=True),policy(tool_mode="native"),policy(token_budget=5),HarnessExecutionPolicy()):
        with pytest.raises(HarnessError):backend.validate_configuration(config,PROVIDER)
    codex=CodexBackend()
    assert not codex.get_metadata().available
    assert codex.get_capabilities()["sandboxing"].supported is None
    with pytest.raises(HarnessError):codex.validate_configuration(policy(),PROVIDER)


@pytest.mark.asyncio
async def test_transport_startup_failure_and_frame_limit(tmp_path):
    process=JsonlProcess()
    with pytest.raises(TransportError) as caught:
        await process.start(["/missing/backend"],cwd=str(tmp_path),env=clean_environment(str(tmp_path)))
    assert caught.value.code == "startup_failed"
    await process.close()
    process=JsonlProcess(max_frame_bytes=32)
    await process.start([sys.executable,"-c","print('x'*100)"] ,cwd=str(tmp_path),env=clean_environment(str(tmp_path)))
    with pytest.raises(TransportError) as caught:
        await process.read(timeout=2)
    assert caught.value.code in {"output_limit","frame_too_large"}
    await process.close()


@pytest.mark.asyncio
async def test_transport_hung_process_group_killed_and_diagnostics_bounded(tmp_path):
    process=JsonlProcess(shutdown_timeout=.02)
    await process.start([sys.executable,"-c","import sys,time;sys.stderr.write('x'*50000);sys.stderr.flush();time.sleep(20)"] ,
                        cwd=str(tmp_path),env=clean_environment(str(tmp_path)))
    async def wait_for_diagnostics():
        while not process.stderr_truncated:
            await asyncio.sleep(.01)
    await asyncio.wait_for(wait_for_diagnostics(), timeout=5)
    await process.close()
    assert process.inspect()["returncode"] is not None
    assert len(process.stderr) <= 16384
    assert process.stderr_truncated


@pytest.mark.asyncio
async def test_immediate_cancellation_never_starts_a_prompt(fake_pi):
    backend=PiBackend(str(fake_pi("hang")))
    session=await backend.initialize_session(policy=policy(),provider=PROVIDER)
    run_id=await backend.execute_task(session,HarnessTask("task"))
    await backend.request_cancellation(session,run_id)
    async def collect():
        return [event async for event in backend.stream_events(session,run_id)]
    events=await asyncio.wait_for(collect(),timeout=3)
    assert (await backend.inspect_run(session,run_id)).status == "cancelled"
    assert not any(event.type == "started" for event in events)
    await backend.close_session(session)


@pytest.mark.asyncio
async def test_transport_concurrent_close_waiters_cannot_cancel_disposal(tmp_path):
    process=JsonlProcess(shutdown_timeout=.02)
    await process.start([sys.executable,"-c","import time;time.sleep(20)"],
                        cwd=str(tmp_path),env=clean_environment(str(tmp_path)))
    entered, release=asyncio.Event(),asyncio.Event()
    original=process._close_impl
    async def delayed():
        entered.set()
        await release.wait()
        await original()
    process._close_impl=delayed
    first=asyncio.create_task(process.close())
    await entered.wait()
    second=asyncio.create_task(process.close())
    first.cancel()
    with pytest.raises(asyncio.CancelledError):await first
    await asyncio.sleep(0)
    assert not second.done()
    assert not process.inspect()["closed"]
    release.set()
    await second
    assert process.inspect()["closed"]
    assert process.inspect()["returncode"] is not None
    await process.close()


@pytest.mark.asyncio
async def test_pi_concurrent_cancel_and_close_share_uncancellable_cleanup(fake_pi):
    backend=PiBackend(str(fake_pi("hang")))
    session=await backend.initialize_session(policy=policy(),provider=PROVIDER)
    state=backend._state(session)
    root=state.temporary.name
    run_id=await backend.execute_task(session,HarnessTask("task"))
    entered,release=asyncio.Event(),asyncio.Event()
    original=state.transport._close_impl
    async def delayed():
        entered.set()
        await release.wait()
        await original()
    state.transport._close_impl=delayed
    cancellations=[asyncio.create_task(backend.request_cancellation(session,run_id)) for _ in range(2)]
    await asyncio.sleep(0)
    first=asyncio.create_task(backend.close_session(session))
    await entered.wait()
    second=asyncio.create_task(backend.close_session(session))
    first.cancel()
    with pytest.raises(asyncio.CancelledError):await first
    await asyncio.sleep(0)
    assert not second.done()
    assert not state.transport.inspect()["closed"]
    release.set()
    await asyncio.wait_for(asyncio.gather(second,*cancellations),timeout=5)
    assert state.transport.inspect()["closed"]
    assert state.transport.inspect()["returncode"] is not None
    assert not Path(root).exists()
    assert not backend._sessions
