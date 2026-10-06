from __future__ import annotations
import asyncio
import json
from pathlib import Path
import sys
import time
from typing import Any, Dict, List, Optional
import pytest
import yaml

from firik_agent.config import load_config
from firik_agent.core.agent import Agent
from firik_agent.core.events import AgentEvent, AgentEventType
from firik_agent.core.session import ChatSession, SessionToolFilter
from firik_agent.core.tools import ToolRegistry
from firik_agent.core.types import (
    Message,
    ModelInfo,
    StreamChunk,
    ToolCall,
    ToolCallFunction,
)
from firik_agent.mcp.client import MCPClient
from firik_agent.mcp.connection import MCPConnection, MCPConnectionManager
from firik_agent.mcp.models import (
    MCPServerConfig,
    MCPStatus,
    MCPTestResult,
    MCPTransport,
    ToolPolicy,
    ToolPolicyMode,
    delete_all_mcp_secrets,
    delete_mcp_secret,
    get_mcp_secret,
    make_secret_ref,
    parse_secret_ref,
    redact_secrets,
    resolve_server_env,
    store_mcp_secret,
)
from firik_agent.providers.base import BaseProvider
from firik_agent.ui.service import FirikService


@pytest.fixture
def mock_server_path() -> str:
    return str(Path(__file__).parent.parent / "examples" / "mock_mcp_server.py")


@pytest.fixture
def temp_config_file(tmp_path: Path) -> Path:
    cfg_file = tmp_path / "config.yaml"
    data = {
        "agent": {"name": "TestAgent"},
        "providers": {
            "firik": {"base_url": "https://test.local/v1", "default_model": "glm-5.3"}
        },
        "mcp_servers": {},
    }
    with open(cfg_file, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f)
    return cfg_file


# --- 1. Models, Secrets & Redaction Tests ---

def test_mcp_secret_storage_and_resolution():
    server = "test_github"
    key = "GITHUB_TOKEN"
    secret_val = "ghp_super_secret_token_12345"

    ref = store_mcp_secret(server, key, secret_val)
    assert ref.startswith("keyring://mcp/test_github/GITHUB_TOKEN")
    parsed = parse_secret_ref(ref)
    assert parsed == (server, key)

    retrieved = get_mcp_secret(server, key)
    assert retrieved == secret_val

    # Test config resolution
    config = MCPServerConfig(
        name=server,
        command="npx",
        args=["-y", "github-server"],
        env={"NODE_ENV": "production"},
        secret_env={key: ref},
    )
    resolved = resolve_server_env(config)
    assert resolved["NODE_ENV"] == "production"
    assert resolved[key] == secret_val

    # Test delete
    ok = delete_mcp_secret(server, key)
    assert ok is True
    assert get_mcp_secret(server, key) == ""


def test_secret_redaction():
    secret = "ghp_9876543210abcdef"
    text = f"Connection failed with token {secret} on port 8000"
    redacted = redact_secrets(text, [secret])
    assert secret not in redacted
    assert "••••••••" in redacted

    pattern_text = "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    redacted_pattern = redact_secrets(pattern_text)
    assert "••••••••" in redacted_pattern


# --- 2. MCP Connection Lifecycle & Test ---

@pytest.mark.asyncio
async def test_mcp_connection_lifecycle(mock_server_path: str):
    config = MCPServerConfig(
        name="test_lifecycle",
        command=sys.executable,
        args=[mock_server_path],
    )
    conn = MCPConnection(config)
    assert conn.status == MCPStatus.CONFIGURED

    # 1. Connect
    await conn.connect()
    assert conn.status == MCPStatus.CONNECTED
    assert conn.client is not None
    assert conn.client.is_connected is True
    assert conn.client.pid is not None
    assert len(conn.cached_tools) == 3

    # 2. Test
    test_res = await conn.test()
    assert test_res.success is True
    assert test_res.process_started is True
    assert test_res.initialize_succeeded is True
    assert test_res.tool_count == 3
    assert "get_system_time" in test_res.tool_names

    # 3. Disconnect
    await conn.disconnect(force=True)
    assert conn.status == MCPStatus.DISCONNECTED
    assert conn.client is None


# --- 3. Requirement #32: Session Tool Filtering & Isolation Scenario ---

@pytest.mark.asyncio
async def test_requirement_32_session_isolation_and_removal(mock_server_path: str):
    """
    Detailed test of the acceptance scenario:
    Configured: GitHub, Filesystem, SQLite
    Chat A: GitHub, Filesystem
    Chat B: SQLite
    Verify model request tool filtering for Chat A and Chat B.
    Remove GitHub from Chat A -> Chat B unaffected, GitHub stays in global config.
    Global removal of GitHub -> All session references cleaned.
    """
    tools = ToolRegistry()
    mgr = MCPConnectionManager(tool_registry=tools)

    # 1. Register 3 servers in Connection Manager
    gh_cfg = MCPServerConfig(name="github", command=sys.executable, args=[mock_server_path])
    fs_cfg = MCPServerConfig(name="filesystem", command=sys.executable, args=[mock_server_path])
    sq_cfg = MCPServerConfig(name="sqlite", command=sys.executable, args=[mock_server_path])

    await mgr.add_server(gh_cfg, connect_now=True)
    await mgr.add_server(fs_cfg, connect_now=True)
    await mgr.add_server(sq_cfg, connect_now=True)

    assert len(mgr.connections) == 3

    # 2. Create ChatSession A (github, filesystem) and ChatSession B (sqlite)
    session_a = ChatSession(
        session_id="chat_a",
        active_mcp_servers={"github", "filesystem"},
    )
    session_b = ChatSession(
        session_id="chat_b",
        active_mcp_servers={"sqlite"},
    )

    filter_layer = SessionToolFilter(tools, mgr)

    # 3. Chat A schemas: must contain github and filesystem tools, NOT sqlite tools
    schemas_a = filter_layer.get_schemas(session_a)
    names_a = [s["function"]["name"] for s in schemas_a]

    assert any(n.startswith("mcp_github_") for n in names_a)
    assert any(n.startswith("mcp_filesystem_") for n in names_a)
    assert not any(n.startswith("mcp_sqlite_") for n in names_a)

    # 4. Chat B schemas: must contain sqlite tools, NOT github or filesystem tools
    schemas_b = filter_layer.get_schemas(session_b)
    names_b = [s["function"]["name"] for s in schemas_b]

    assert any(n.startswith("mcp_sqlite_") for n in names_b)
    assert not any(n.startswith("mcp_github_") for n in names_b)
    assert not any(n.startswith("mcp_filesystem_") for n in names_b)

    # 5. Remove GitHub from Chat A
    session_a.disable_mcp("github")

    # Verify GitHub is still present globally in MCP Connection Manager
    assert "github" in mgr.connections
    assert mgr.connections["github"].status == MCPStatus.CONNECTED

    # Verify Chat A no longer has GitHub tools
    schemas_a_after = filter_layer.get_schemas(session_a)
    names_a_after = [s["function"]["name"] for s in schemas_a_after]
    assert not any(n.startswith("mcp_github_") for n in names_a_after)
    assert any(n.startswith("mcp_filesystem_") for n in names_a_after)

    # Verify Chat B is completely unaffected
    schemas_b_after = filter_layer.get_schemas(session_b)
    names_b_after = [s["function"]["name"] for s in schemas_b_after]
    assert any(n.startswith("mcp_sqlite_") for n in names_b_after)

    # 6. Global removal of GitHub from manager
    # Add GitHub back to Chat A first to test global cleanup
    session_a.enable_mcp("github")
    assert "github" in session_a.active_mcp_servers

    await mgr.remove_server("github")
    session_a.disable_mcp("github")  # Service removes from all sessions
    assert "github" not in mgr.connections
    assert "github" not in session_a.active_mcp_servers

    # Clean up
    await mgr.shutdown()


# --- 4. Ref Counting & Shared Connections ---

@pytest.mark.asyncio
async def test_connection_ref_counting(mock_server_path: str):
    mgr = MCPConnectionManager()
    cfg = MCPServerConfig(name="shared_server", command=sys.executable, args=[mock_server_path], autostart=False)
    await mgr.add_server(cfg, connect_now=False)

    conn = mgr.get_connection("shared_server")
    assert conn.status == MCPStatus.CONFIGURED

    # Session 1 acquires server
    await mgr.acquire_for_session("sess_1", "shared_server")
    assert conn.status == MCPStatus.CONNECTED
    assert "sess_1" in conn.used_by_sessions

    # Session 2 acquires the same server
    await mgr.acquire_for_session("sess_2", "shared_server")
    assert conn.status == MCPStatus.CONNECTED
    assert conn.used_by_sessions == {"sess_1", "sess_2"}

    # Session 1 releases: connection remains open for Session 2
    await mgr.release_for_session("sess_1", "shared_server")
    assert conn.status == MCPStatus.CONNECTED
    assert conn.used_by_sessions == {"sess_2"}

    # Session 2 releases: non-autostart server disconnects
    await mgr.release_for_session("sess_2", "shared_server")
    assert conn.status == MCPStatus.DISCONNECTED
    assert len(conn.used_by_sessions) == 0

    await mgr.shutdown()


# --- 5. Agent Loop with Session Tool Filter ---

class MockStreamProvider(BaseProvider):
    """Simulates streaming LLM responses with tool call generation."""

    def __init__(self):
        super().__init__(name="mock_stream", base_url="http://mock.stream")
        self.step = 0

    async def chat(
        self,
        messages: List[Message],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
    ) -> Message:
        self.step += 1
        last_msg = messages[-1]
        if last_msg.role == "user":
            return Message(
                role="assistant",
                content=None,
                tool_calls=[
                    ToolCall(
                        id="call_mcp_1",
                        type="function",
                        function=ToolCallFunction(
                            name="mcp_mock_text_transformer",
                            arguments='{"text": "firik", "operation": "uppercase"}',
                        ),
                    )
                ],
            )
        elif last_msg.role == "tool":
            return Message(
                role="assistant",
                content=f"Sonuç başarıyla alındı: {last_msg.content}",
            )
        return Message(role="assistant", content="Bitti.")

    async def chat_stream(
        self,
        messages: List[Message],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
    ) -> Any:
        self.step += 1
        last_msg = messages[-1]
        if last_msg.role == "user":
            # Emit tool call
            yield StreamChunk(
                tool_calls=[
                    ToolCall(
                        id="call_mcp_1",
                        type="function",
                        function=ToolCallFunction(
                            name="mcp_mock_text_transformer",
                            arguments='{"text": "firik", "operation": "uppercase"}',
                        ),
                    )
                ],
                is_done=True,
            )
        elif last_msg.role == "tool":
            yield StreamChunk(delta_reasoning="Analiz edildi...")
            yield StreamChunk(delta_content="Dönüştürülen metin: ")
            yield StreamChunk(delta_content=f"{last_msg.content}")
            yield StreamChunk(is_done=True)

    async def list_models(self) -> List[ModelInfo]:
        return [ModelInfo(id="mock-stream-v1", name="Mock Stream")]


@pytest.mark.asyncio
async def test_agent_run_stream_with_mcp_and_events(mock_server_path: str):
    agent = Agent()
    prov = MockStreamProvider()
    agent.providers.register("mock_stream", prov, set_active=True)

    # Add mock MCP server to agent
    await agent.mcp.add_server(
        name="mock",
        command=sys.executable,
        args=[mock_server_path],
    )

    session = ChatSession(
        session_id="stream_session",
        active_mcp_servers={"mock"},
    )
    s_filter = SessionToolFilter(agent.tools)

    events: List[AgentEvent] = []
    async for evt in agent.run_stream(
        prompt="Transform firik to uppercase",
        session=session,
        session_tool_filter=s_filter,
    ):
        events.append(evt)

    event_types = [e.type for e in events]
    assert event_types[0] == AgentEventType.MODEL_REQUEST_STARTED
    requests = [e for e in events if e.type == AgentEventType.MODEL_REQUEST_STARTED]
    assert [e.metadata["iteration"] for e in requests] == [1, 2]
    assert AgentEventType.TOOL_CALL_STARTED in event_types
    assert AgentEventType.TOOL_CALL_RESULT in event_types
    assert AgentEventType.TEXT_DELTA in event_types
    assert AgentEventType.DONE in event_types

    # Verify tool execution result
    tool_results = [e for e in events if e.type == AgentEventType.TOOL_CALL_RESULT]
    assert len(tool_results) == 1
    assert tool_results[0].content == "FIRIK"
    assert tool_results[0].duration_ms is not None
    started = next(e for e in events if e.type == AgentEventType.TOOL_CALL_STARTED)
    assert started.metadata["tool_call_id"] == tool_results[0].metadata["tool_call_id"]
    assert event_types.index(AgentEventType.TOOL_CALL_RESULT) < events.index(requests[1])

    await agent.close()


@pytest.mark.asyncio
async def test_disabled_mcp_tool_execution_blocked(mock_server_path: str):
    agent = Agent()
    prov = MockStreamProvider()
    agent.providers.register("mock_stream", prov, set_active=True)

    await agent.mcp.add_server(
        name="mock",
        command=sys.executable,
        args=[mock_server_path],
    )

    # Session WITHOUT 'mock' enabled
    session = ChatSession(
        session_id="blocked_session",
        active_mcp_servers=set(),  # disabled!
    )
    s_filter = SessionToolFilter(agent.tools)

    # Direct execution check
    tc = ToolCall(
        id="test_tc",
        function=ToolCallFunction(name="mcp_mock_text_transformer", arguments='{"text": "a", "operation": "uppercase"}'),
    )
    result = await s_filter.execute(tc, session)
    assert result.is_error is True
    assert "not enabled" in result.content

    await agent.close()


# --- 6. FirikService MCP API & UI Callbacks ---

def test_service_mcp_integration(temp_config_file: Path, mock_server_path: str, monkeypatch):
    monkeypatch.chdir(temp_config_file.parent)
    # Keep service databases in the test sandbox, away from the user's data.
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: temp_config_file.parent))
    service = FirikService()

    # Add server via service
    cfg = MCPServerConfig(
        name="svc_mock",
        command=sys.executable,
        args=[mock_server_path],
        default_for_chat=True,
    )

    done_event = asyncio.Event()

    from firik_agent.config import add_mcp_server_to_config
    add_mcp_server_to_config("svc_mock", cfg.to_dict())
    service.runtime.run_sync(service.mcp_manager.add_server(cfg, connect_now=True))
    servers = service.get_mcp_servers()
    assert len(servers) >= 1
    svc_entry = next((s for s in servers if s["name"] == "svc_mock"), None)
    assert svc_entry is not None
    assert svc_entry["tool_count"] == 3

    # Test new session receives default MCP
    new_sess = service.get_or_create_session()
    assert "svc_mock" in new_sess.active_mcp_servers

    # Test duplicate server
    dup_cfg = service.duplicate_mcp_server("svc_mock", "svc_mock_clone")
    assert dup_cfg.name == "svc_mock_clone"

    # Test export configs
    exported = service.export_mcp_configs()
    assert "svc_mock" in exported

    service.shutdown()
