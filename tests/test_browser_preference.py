"""Verify browser routing in actual provider requests without live inference."""
from types import SimpleNamespace

import pytest

from firik_agent.browser.config import BrowserConfig
from firik_agent.browser.instructions import browser_instructions
from firik_agent.browser.models import BrowserGrant, BrowserIdentity
from firik_agent.browser.tool_adapter import BrowserToolAdapter
from firik_agent.core.agent import Agent
from firik_agent.core.events import AgentEventType
from firik_agent.core.session import ChatSession, SessionToolFilter
from firik_agent.core.tools import ToolRegistry
from firik_agent.core.types import Message, StreamChunk, ToolDefinition
from firik_agent.projects.coding_runtime import CodingRunControl, CodingToolFilter
from firik_agent.projects.harness_profiles import resolve_harness_profile


class Provider:
    name, current_model, model_metadata = "fixture", "offline", {}

    def __init__(self):
        self.requests = []

    async def chat(self, **kwargs):
        self.requests.append(kwargs)
        return Message(role="assistant", content="Hazır.")

    async def chat_stream(self, **kwargs):
        self.requests.append(kwargs)
        yield StreamChunk(delta_content="Hazır.")


def runtime():
    agent = Agent(config={"agent": {"context_compaction": {"enabled": False}},
        "providers": {}, "plugins": {}, "skills": {}, "self_improvement": {"mode": "off"}})
    agent._is_initialized = True
    provider = Provider()
    agent.providers.register("fixture", provider, set_active=True)
    return agent, provider


def browser(session):
    manager = SimpleNamespace(config=BrowserConfig(enabled=True))
    return BrowserToolAdapter(manager, BrowserIdentity(session.session_id),
        lambda: BrowserGrant(enabled=session.browser_enabled, allow_public_https=True))


async def execute(agent, session, gate, streaming, prompt):
    if streaming:
        events = [event async for event in agent.run_stream(prompt, session=session, session_tool_filter=gate)]
        assert events[-1].type == AgentEventType.DONE
    else:
        assert await agent.run(prompt, session=session, session_tool_filter=gate) == "Hazır."


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("prompt", ["Tarayıcıda siteyi aç", "Sistem Chrome tarayıcısındaki açık sekmemi kullan"])
async def test_provider_gets_owned_browser_priority_and_explicit_user_choice(streaming, prompt):
    agent, provider = runtime()
    session = ChatSession(learning_enabled=False)
    session.browser_enabled = True
    agent.tools.register(ToolDefinition(name="computer_screenshot", description="system screen", source="builtin"), lambda: "screen")
    gate = SessionToolFilter(agent.tools, browser_adapter=browser(session), external_tool_authorize=lambda *_: True)
    await execute(agent, session, gate, streaming, prompt)
    request = provider.requests[0]
    system = request["messages"][0].content
    assert "önce bu araçları kullan" in system
    assert "Chrome/Safari" in system and "bu açık tercihi izle" in system
    assert "Engellenen HTTP/özel ağ" in system
    assert "Erişilemeyen siteyi atlayıp" in system
    names = {schema["function"]["name"] for schema in request["tools"]}
    assert {"browser_start", "browser_navigate", "computer_screenshot"} <= names
    assert request["messages"][-1].content == prompt


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("enabled,selected", [(False, None), (True, {"computer_screenshot"})])
async def test_disabled_or_filtered_browser_is_not_advertised(streaming, enabled, selected):
    agent, provider = runtime()
    session = ChatSession(learning_enabled=False, enabled_tool_names=selected)
    session.browser_enabled = enabled
    gate = SessionToolFilter(agent.tools, browser_adapter=browser(session))
    await execute(agent, session, gate, streaming, "Tarayıcıda siteyi aç")
    assert "Tarayıcı seçimi:" not in provider.requests[0]["messages"][0].content
    assert not any(schema["function"]["name"].startswith("browser_") for schema in provider.requests[0]["tools"] or [])


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_no_tools_model_does_not_receive_browser_guidance(streaming):
    agent, provider = runtime()
    provider.model_metadata = {"offline": {"source": "provider", "supports_tools": False}}
    session = ChatSession(learning_enabled=False)
    session.browser_enabled = True
    gate = SessionToolFilter(agent.tools, browser_adapter=browser(session))
    await execute(agent, session, gate, streaming, "Tarayıcıda siteyi aç")
    assert "Tarayıcı seçimi:" not in provider.requests[0]["messages"][0].content
    assert not provider.requests[0]["tools"]


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_coding_provider_receives_same_owned_browser_priority(tmp_path, streaming):
    agent, provider = runtime()
    control = CodingRunControl("coding", "project", mode="ask", permissions={"allow_browser": True},
        browser_grant=BrowserGrant(enabled=True, allow_public_https=True))
    manager = SimpleNamespace(config=BrowserConfig(enabled=True))
    gate = CodingToolFilter(tmp_path, control, browser_manager=manager)
    session = ChatSession(session_id="coding", learning_enabled=False)
    await execute(agent, session, gate, streaming, "Tarayıcıda açık sayfayı incele")
    system = provider.requests[0]["messages"][0].content
    assert "önce bu araçları kullan" in system
    # Ask mode permits observation only. Do not instruct unavailable navigation.
    assert "browser_navigate ile URL'yi aç" not in system
    assert "browser_snapshot" in {schema["function"]["name"] for schema in provider.requests[0]["tools"]}


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("prompt,expected", [
    ("Tarayıcıda sayfayı incele", True), ("Siteyi aç", True), ("Web sayfasını incele", True),
    ("Dosyaları incele", False), ("Compositeyi açıkla", False),
])
async def test_profile_guidance_matches_final_selected_inventory(tmp_path, streaming, prompt, expected):
    agent, provider = runtime()
    control = CodingRunControl("coding", "project", mode="ask", permissions={"allow_browser": True},
        browser_grant=BrowserGrant(enabled=True, allow_public_https=True))
    control.harness_profile = resolve_harness_profile(session_override="minimal")
    control.capabilities = {"source": "provider", "context_window": 32000}
    manager = SimpleNamespace(config=BrowserConfig(enabled=True))
    gate = CodingToolFilter(tmp_path, control, browser_manager=manager)
    session = ChatSession(session_id="coding", learning_enabled=False)
    await execute(agent, session, gate, streaming, prompt)
    request = provider.requests[0]
    assert ("Tarayıcı seçimi:" in request["messages"][0].content) is expected
    assert ("browser_snapshot" in {schema["function"]["name"] for schema in request["tools"]}) is expected


def test_helper_does_not_promote_unavailable_navigation():
    assert not browser_instructions([])
    assert not browser_instructions([{"function": {"name": "browser_verify"}}])
    guidance = browser_instructions([{"function": {"name": "browser_snapshot"}}])
    assert "önce bu araçları kullan" in guidance
    assert "browser_start kullan" not in guidance
