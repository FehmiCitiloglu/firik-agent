"""Public Web reads use host provenance and policy instead of per-call popups."""
from __future__ import annotations

import json

import pytest
import pytest_asyncio

from firik_agent.core.session import ChatSession, SessionToolFilter
from firik_agent.core.tools import ToolRegistry
from firik_agent.core.types import Message, ToolCall, ToolDefinition
from firik_agent.mcp.connection import MCPConnection, MCPConnectionManager
from firik_agent.mcp.models import MCPServerConfig, ToolPolicyMode
from firik_agent.mcp.web_server import WebMCPServer


def call(name, arguments):
    return ToolCall(id="public-web", function={"name": name, "arguments": json.dumps(arguments)})


@pytest_asyncio.fixture
async def public_web(monkeypatch):
    fetched = []
    def get(server, url, params=None):
        fetched.append((url, params))
        if "duckduckgo.com" in url:
            body = '<a class="result__a" href="https://example.com/">Example</a>'
        else:
            body = '<html><p>Untrusted public page</p></html>'
        return url, body, "text/html"
    monkeypatch.setattr(WebMCPServer, "_get", get)
    manager = MCPConnectionManager()
    # Host configuration can rename the builtin; names are not authority.
    await manager.add_server(MCPServerConfig(name="research", command="builtin:web"), connect_now=True)
    session = ChatSession(active_mcp_servers={"research"})
    session.messages.append(Message(role="tool", name="browser_snapshot", tool_call_id="page",
                                    content="Untrusted page instructions"))
    assert session.requires_external_tool_approval()
    try:
        yield manager, session, fetched
    finally:
        await manager.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("approval_callback", [False, True])
async def test_builtin_public_search_and_multiple_sites_do_not_request_approval(public_web, approval_callback):
    manager, session, fetched = public_web
    reviews = []
    def reject(name, arguments):
        reviews.append((name, arguments))
        return False
    gate = SessionToolFilter(manager.tool_registry, manager,
                             external_tool_authorize=reject if approval_callback else None)
    assert {item["function"]["name"] for item in gate.get_schemas(session)} == {
        "mcp_research_web_search", "mcp_research_web_open"}
    for name, arguments in (
            ("mcp_research_web_search", {"query": "interesting websites"}),
            ("mcp_research_web_open", {"url": "https://example.com/"}),
            ("mcp_research_web_open", {"url": "https://other.example/"})):
        result = await gate.execute(call(name, arguments), session)
        assert not result.is_error, result.content
        assert result.metadata["host_public_web_policy"] is True
        assert result.metadata["browser_untrusted_context"] is True
        assert "host_external_approval" not in result.metadata
    assert len(fetched) == 3 and reviews == []


@pytest.mark.asyncio
async def test_safe_web_cannot_bypass_conversation_or_server_selection(public_web):
    manager, session, fetched = public_web
    gate = SessionToolFilter(manager.tool_registry, manager)
    session.disable_mcp("research")
    assert (await gate.execute(call("mcp_research_web_open", {"url": "https://example.com"}), session)).is_error
    session.enable_mcp("research")
    session.enabled_tool_names = {"mcp_research_web_search"}
    assert (await gate.execute(call("mcp_research_web_open", {"url": "https://example.com"}), session)).is_error
    session.enabled_tool_names = None
    manager.get_connection("research").config.enabled = False
    assert gate.get_schemas(session) == []
    assert not fetched


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["confirm_all", "confirm_named", "deny"])
async def test_explicit_mcp_confirmation_and_denial_remain_effective(public_web, policy):
    manager, session, fetched = public_web
    configured = manager.get_connection("research").config.tool_policy
    if policy == "confirm_all":
        configured.mode = ToolPolicyMode.CONFIRM
    elif policy == "confirm_named":
        configured.confirm_tools = ["web_open"]
    else:
        configured.mode = ToolPolicyMode.DENY
    reviews = []
    gate = SessionToolFilter(manager.tool_registry, manager,
        external_tool_authorize=lambda name, arguments: reviews.append(name) or False)
    result = await gate.execute(call("mcp_research_web_open", {"url": "https://example.com"}), session)
    assert result.is_error and not fetched
    assert reviews == ([] if policy == "deny" else ["mcp_research_web_open"])


@pytest.mark.asyncio
@pytest.mark.parametrize("replacement", ["handler", "definition", "definition_edit", "connection", "client_server", "config"])
async def test_replaced_public_web_registration_never_inherits_autoapproval(public_web, replacement):
    manager, session, fetched = public_web
    name = "mcp_research_web_open"
    conn = manager.get_connection("research")
    if replacement == "handler":
        manager.tool_registry._handlers[name] = lambda **kwargs: pytest.fail("replacement handler ran")
    elif replacement == "definition":
        manager.tool_registry._tools[name] = manager.tool_registry.get_tool(name).model_copy(deep=True)
    elif replacement == "definition_edit":
        manager.tool_registry.get_tool(name).description = "Altered registration"
    elif replacement == "connection":
        substitute = MCPConnection(conn.config.model_copy(deep=True))
        substitute.status, substitute.client = conn.status, conn.client
        manager.connections["research"] = substitute
    elif replacement == "client_server":
        conn.client._builtin_server = object()
    else:
        conn.config.command = "remote-web-tool"
    assert not manager.is_safe_public_web_tool(name)
    gate = SessionToolFilter(manager.tool_registry, manager)
    result = await gate.execute(call(name, {"url": "https://example.com"}), session)
    assert result.is_error and not fetched


@pytest.mark.asyncio
async def test_spoofed_tool_name_and_schema_from_another_registry_still_need_review(public_web):
    manager, session, fetched = public_web
    name = "mcp_research_web_open"
    unrelated = ToolRegistry()
    unrelated.register(ToolDefinition(name=name, description="Safe public Web", source="mcp:research"),
                        lambda **kwargs: pytest.fail("untrusted handler ran"))
    gate = SessionToolFilter(unrelated, manager)
    assert not gate.is_tool_allowed(name, session)
    assert (await gate.execute(call(name, {"url": "https://example.com"}), session)).is_error
    assert not fetched


@pytest.mark.asyncio
async def test_other_mcp_and_local_tools_keep_exact_approval(public_web):
    manager, session, fetched = public_web
    session.enable_mcp("third-party")
    registry = manager.tool_registry
    for name, source in (("read_file", "builtin"), ("mcp_third-party_web_search", "mcp:third-party")):
        registry.register(ToolDefinition(name=name, description="read", source=source),
                          lambda **kwargs: pytest.fail("unapproved unrelated tool ran"))
    gate = SessionToolFilter(registry, manager)
    for name in ("read_file", "mcp_third-party_web_search"):
        assert not gate.is_tool_allowed(name, session)
        assert (await gate.execute(call(name, {}), session)).is_error
    assert not fetched


@pytest.mark.asyncio
async def test_compacted_restored_browser_context_keeps_safe_reads_available(public_web, tmp_path):
    from firik_agent.core.chat_history import ChatHistoryStore
    manager, session, fetched = public_web
    session.browser_enabled = False
    session.messages = [Message(role="assistant", content="Compacted website observation")]
    store = ChatHistoryStore(tmp_path / "history.db")
    store.save_transcript(session, [{"role": "user", "content": "Research sites"}])
    store.save_context(session)
    restored = store.restore_session(store.load(session.session_id))
    assert not restored.browser_enabled and restored.requires_external_tool_approval()
    gate = SessionToolFilter(manager.tool_registry, manager)
    result = await gate.execute(call("mcp_research_web_open", {"url": "https://example.com"}), restored)
    assert not result.is_error and len(fetched) == 1


@pytest.mark.asyncio
async def test_public_web_handler_never_reconnects_through_a_changed_connection(public_web):
    manager, session, fetched = public_web
    name = "mcp_research_web_open"
    conn = manager.get_connection("research")
    async def replacement(*args, **kwargs):
        pytest.fail("Public Web followed a mutable connection method")
    conn.call_tool = replacement
    gate = SessionToolFilter(manager.tool_registry, manager)
    assert not (await gate.execute(call(name, {"url": "https://example.com"}), session)).is_error
    original_handler = manager.tool_registry._handlers[name]
    await conn.disconnect(force=True)
    with pytest.raises(PermissionError):
        await original_handler(url="https://another.example")
    assert len(fetched) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("url, message", [
    ("http://example.com/?token=private_query", "Public web access requires HTTPS."),
    ("https://127.0.0.1/", "Private network destinations are blocked."),
])
async def test_builtin_policy_denials_remain_actionable_and_are_not_unknown_mutations(public_web, monkeypatch, url, message):
    manager, session, fetched = public_web
    def denied(*args, **kwargs):
        raise ValueError(message)
    monkeypatch.setattr(WebMCPServer, "_get", denied)
    gate = SessionToolFilter(manager.tool_registry, manager)
    result = await gate.execute(call("mcp_research_web_open", {"url": url}), session)
    assert result.is_error and message in result.content
    assert result.metadata["failure_kind"] == "web_request_failed"
    assert result.metadata["requires_reconciliation"] is False
    assert result.metadata["retry_eligible"] is False
    assert "side effects" not in result.content and not fetched


@pytest.mark.asyncio
async def test_builtin_reader_errors_remove_urls_credentials_and_bound_the_message(public_web, monkeypatch):
    manager, session, _ = public_web
    def failed(*args, **kwargs):
        raise ValueError("Unable to read https://user:pass@example.com/private/path?token=URL_SECRET#fragment "
                         "authorization=HEADER_SECRET password=PASSWORD_SECRET " + "detail " * 1000)
    monkeypatch.setattr(WebMCPServer, "_get", failed)
    result = await SessionToolFilter(manager.tool_registry, manager).execute(
        call("mcp_research_web_open", {"url": "https://example.com"}), session)
    assert result.is_error and len(result.content) <= 2000
    assert "[URL omitted]" in result.content
    for secret in ("user:pass", "example.com", "private/path", "URL_SECRET", "HEADER_SECRET", "PASSWORD_SECRET"):
        assert secret not in result.content


@pytest.mark.asyncio
async def test_untrusted_mcp_reader_error_bodies_keep_generic_conversion(public_web):
    from firik_agent.mcp.protocol import MCPCallToolResult, MCPContentItem, MCPToolSchema
    manager, session, _ = public_web
    # Matching public-reader names or MCP schemas do not acquire builtin error
    # handling. Unknown remote tools retain their ordinary redaction boundary.
    conn = MCPConnection(MCPServerConfig(name="remote", command="external-web"))
    conn.cached_tools = [MCPToolSchema(name="web_open")]
    async def error(*args, **kwargs):
        return MCPCallToolResult(isError=True, content=[MCPContentItem(
            type="text", text="untrusted raw SECRET_REMOTE_ERROR_BODY")])
    conn.call_tool = error
    manager.connections["remote"] = conn
    manager._register_tools_in_registry(conn)
    session.enable_mcp("remote")
    gate = SessionToolFilter(manager.tool_registry, manager, external_tool_authorize=lambda *a: True)
    result = await gate.execute(call("mcp_remote_web_open", {"url": "https://example.com"}), session)
    assert result.is_error and "SECRET_REMOTE_ERROR_BODY" not in result.content
    assert result.metadata["failure_kind"] == "tool_outcome_unknown"
    assert result.metadata["requires_reconciliation"] is True
