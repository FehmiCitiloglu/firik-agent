"""Exercise the screenshot's model switch/paste/error flow through the real CLI."""
import asyncio
from io import StringIO
import json

import httpx
import pytest
from prompt_toolkit import PromptSession
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console

from firik_agent import cli
from firik_agent.core.agent import Agent
from firik_agent.core.types import Message, ToolCall, ToolCallFunction
from firik_agent.providers.firik import FirikProvider


@pytest.mark.asyncio
@pytest.mark.parametrize("status,code,kind", [
    (400, "invalid_request_error", "invalid_request"),
    (500, "internal_error", "unknown"),
    (503, "model_capacity_exhausted", "provider_overloaded"),
])
async def test_model_switch_long_paste_error_is_inspectable_and_repl_remains_usable(
        monkeypatch, tmp_path, status, code, kind):
    config = {"agent": {"recovery": {"request_retries": 0}},
              "skills": {"directory": str(tmp_path / "skills")},
              "plugins": {"directory": str(tmp_path / "plugins")}}
    agent = Agent(config=config)
    agent._is_initialized = True
    agent._build_full_system_prompt = lambda: "Follow the user request."
    provider = FirikProvider(api_key="fixture-key", base_url="https://fixture.invalid/v1",
                             default_model="glm-5.3")
    agent.providers.register("firik", provider, set_active=True)
    call = ToolCall(id="previous-inspection", function=ToolCallFunction(name="git_inspect", arguments="{}"))
    agent.messages = [Message(role="user", content="Inspect this project"),
                      Message(role="assistant", tool_calls=[call]),
                      Message(role="tool", tool_call_id=call.id, name="git_inspect", content="No repository"),
                      Message(role="assistant", content="Inspection complete.")]
    requests = []

    def respond(request):
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"data": [
                {"id": "mimo-v2.6-pro", "context_length": 1_048_576}]})
        if sum(item.method == "POST" for item in requests) == 1:
            return httpx.Response(status, json={"error": {
                "code": code, "message": "password=PRIVATE-PROVIDER-BODY"}},
                headers={"X-Request-ID": "req-cli-long-paste"})
        return httpx.Response(200, json={"choices": [{"message": {
            "role": "assistant", "content": "Recovered diagnostic response."}}]})

    original_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original_client(
        transport=httpx.MockTransport(respond), **kwargs))
    output = StringIO()
    monkeypatch.setattr(cli, "console", Console(file=output, width=240, color_system=None))
    monkeypatch.setattr(cli, "print_banner", lambda agent: None)
    ready = asyncio.Queue()
    pasted = " ".join(f"requirement{index}" for index in range(4713))
    with create_pipe_input() as pipe:
        class ObservedSession:
            def __init__(self, **kwargs):
                kwargs["history"] = InMemoryHistory()
                self.session = PromptSession(input=pipe, output=DummyOutput(), **kwargs)

            async def prompt_async(self, *args, **kwargs):
                ready.put_nowait(True)
                return await self.session.prompt_async(*args, **kwargs)

        monkeypatch.setattr(cli, "PromptSession", ObservedSession)
        task = asyncio.create_task(cli.run_repl(agent))
        try:
            for text in ["/model mimo-v2.6-pro",
                         f"Implement this prompt \x1b[200~{pasted}\x1b[201~",
                         "/doctor --json", "/details", "Continue diagnostic test", "/exit"]:
                await asyncio.wait_for(ready.get(), timeout=5)
                # A 4,713-word paste exceeds the OS pipe buffer. Keep the
                # event loop reading while the producer writes its bytes.
                await asyncio.to_thread(pipe.send_text, text + "\r")
            await asyncio.wait_for(task, timeout=5)
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    posts = [json.loads(request.content) for request in requests if request.method == "POST"]
    assert len(posts) == 2  # No automatic replay of the failed paid request.
    assert all(body["model"] == "mimo-v2.6-pro" for body in posts)
    assert posts[0]["messages"][-1]["content"] == "Implement this prompt " + pasted
    assert posts[1]["messages"][-1]["content"] == "Continue diagnostic test"
    assert agent.messages[4].content == "Implement this prompt " + pasted
    assert agent.compaction.cursor == 0
    shown = output.getvalue()
    assert kind in shown and str(status) in shown and "req-cli-long-paste" in shown
    assert "Recovered diagnostic response." in shown
    assert "PRIVATE-PROVIDER-BODY" not in shown and "fixture-key" not in shown
    assert "last_failure" in shown  # /doctor retains the failed turn's diagnostic.
