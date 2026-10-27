"""Host evaluator and native file gate remain outside fixture control."""
import json

import httpx
import pytest

from firik_agent.benchmarks.evaluator import evaluate_sources
from firik_agent.benchmarks.fixtures import offline_tasks
from firik_agent.benchmarks.runner import run_trial
from firik_agent.providers.firik import FirikProvider
from firik_agent.providers.openai_provider import OpenAIProvider


@pytest.mark.asyncio
async def test_evaluator_path_and_sandbox_capabilities_cannot_be_granted_by_fixture():
    row = await run_trial(next(task for task in offline_tasks() if task.id == "evaluator-tampering"))
    assert row["fixture_passed"]
    assert row["verification"]["source_match"]
    assert row["metrics"]["tool_errors"] == 1
    assert row["safety_violations"] == 0


def test_evaluator_rejects_symlink_to_hidden_reference(tmp_path):
    root = tmp_path / "repository"
    root.mkdir()
    hidden = tmp_path / "hidden.py"
    hidden.write_text("value = 2\n")
    (root / "main.py").symlink_to(hidden)
    passed, _ = evaluate_sources(root, {"main.py": "value = 2\n"})
    assert not passed
    assert hidden.read_text() == "value = 2\n"


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_type", [OpenAIProvider, FirikProvider])
@pytest.mark.parametrize("ending", ["", "data: [DONE]\n\n"])
async def test_real_adapter_rejects_incomplete_tool_stream_before_native_completion(monkeypatch, provider_type, ending):
    frame = {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "incomplete",
             "function": {"name": "write_file", "arguments": '{"path":'}}]}}]}
    payload = "data: " + json.dumps(frame) + "\n\n" + ending
    original_client = httpx.AsyncClient
    transport = httpx.MockTransport(lambda _: httpx.Response(200, text=payload))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original_client(transport=transport, **kw))
    provider = provider_type(api_key="unused", base_url="https://offline.invalid/v1", default_model="test")
    with pytest.raises(RuntimeError, match="Incomplete streamed tool call"):
        _ = [chunk async for chunk in provider.chat_stream([])]


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_type", [OpenAIProvider, FirikProvider])
async def test_repeated_finish_frame_cannot_emit_duplicate_tool_side_effects(monkeypatch, provider_type):
    first = {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "once",
             "function": {"name": "read_file", "arguments": '{"path":"main.py"}'}}]}}]}
    finish = {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}
    payload = "".join("data: " + json.dumps(frame) + "\n\n" for frame in (first, finish, finish))
    payload += "data: [DONE]\n\n"
    original_client = httpx.AsyncClient
    transport = httpx.MockTransport(lambda _: httpx.Response(200, text=payload))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original_client(transport=transport, **kw))
    provider = provider_type(api_key="unused", base_url="https://offline.invalid/v1", default_model="test")
    chunks = [chunk async for chunk in provider.chat_stream([])]
    assert len([call for chunk in chunks for call in chunk.tool_calls or []]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_type", [OpenAIProvider, FirikProvider])
async def test_clean_eof_after_partial_text_is_not_completed_response(monkeypatch, provider_type):
    payload = 'data: {"choices":[{"delta":{"content":"I changed"}}]}\n\n'
    original_client = httpx.AsyncClient
    transport = httpx.MockTransport(lambda _: httpx.Response(200, text=payload))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original_client(transport=transport, **kw))
    provider = provider_type(api_key="unused", base_url="https://offline.invalid/v1", default_model="test")
    chunks = []
    with pytest.raises(RuntimeError, match="Incomplete streamed tool call or truncated SSE"):
        async for chunk in provider.chat_stream([]):
            chunks.append(chunk)
    assert any(chunk.delta_content == "I changed" for chunk in chunks)
