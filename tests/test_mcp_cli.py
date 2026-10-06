from __future__ import annotations
import sys
from pathlib import Path
import pytest
import yaml

from firik_agent.config import load_config
from firik_agent.core.agent import Agent
from firik_agent.cli import handle_slash_command
from firik_agent.mcp.cli import mcp_add, mcp_list, mcp_remove, run_mcp
from firik_agent.api.cli import run as api_run


@pytest.fixture
def temp_config(tmp_path: Path) -> Path:
    config_file = tmp_path / "test_config.yaml"
    initial_data = {
        "agent": {"name": "FirikAgent"},
        "providers": {
            "firik": {"base_url": "https://test.example.com", "default_model": "glm-5.3"}
        },
        "mcp_servers": {},
        "skills": {"directory": "skills", "active_skills": []},
    }
    with open(config_file, "w", encoding="utf-8") as f:
        yaml.safe_dump(initial_data, f)
    return config_file


@pytest.fixture
def mock_server_path() -> str:
    p = Path(__file__).parent.parent / "examples" / "mock_mcp_server.py"
    return str(p)


@pytest.mark.asyncio
async def test_mcp_add_and_list_live(temp_config: Path, mock_server_path: str):
    # 1. Add mock MCP server with live connection test
    ret = await mcp_add(
        name="test_live",
        command=sys.executable,
        args=[mock_server_path],
        test=True,
        config_path=str(temp_config),
    )
    assert ret == 0

    # Verify saved in YAML
    cfg = load_config(str(temp_config))
    assert "test_live" in cfg.get("mcp_servers", {})
    entry = cfg["mcp_servers"]["test_live"]
    assert entry["command"] == sys.executable
    assert entry["args"] == [mock_server_path]

    # 2. List with test=False
    list_ret = await mcp_list(config_path=str(temp_config), test=False)
    assert list_ret == 0

    # 3. List with test=True
    list_test_ret = await mcp_list(config_path=str(temp_config), test=True)
    assert list_test_ret == 0

    # 4. Remove server
    rm_ret = await mcp_remove(name="test_live", config_path=str(temp_config))
    assert rm_ret == 0

    # Verify removed from YAML
    cfg_after = load_config(str(temp_config))
    assert "test_live" not in cfg_after.get("mcp_servers", {})


@pytest.mark.asyncio
async def test_mcp_add_no_test_with_env(temp_config: Path):
    ret = await mcp_add(
        name="github_mcp",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-github"],
        env={"GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_mock_123"},
        test=False,
        config_path=str(temp_config),
    )
    assert ret == 0

    cfg = load_config(str(temp_config))
    assert "github_mcp" in cfg["mcp_servers"]
    entry = cfg["mcp_servers"]["github_mcp"]
    assert entry["command"] == "npx"
    assert entry["args"] == ["-y", "@modelcontextprotocol/server-github"]
    assert entry["env"]["GITHUB_PERSONAL_ACCESS_TOKEN"] == "ghp_mock_123"


@pytest.mark.asyncio
async def test_mcp_cli_run_subcommands(temp_config: Path, mock_server_path: str):
    # Test 'add' via run_mcp command line args (handling -y and arbitrary args)
    code = await run_mcp([
        "add",
        "cli_server",
        sys.executable,
        mock_server_path,
        "-c",
        str(temp_config),
    ])
    assert code == 0

    # Test 'list'
    code_list = await run_mcp(["list", "-c", str(temp_config)])
    assert code_list == 0

    # Test 'remove' / 'rm'
    code_rm = await run_mcp(["rm", "cli_server", "-c", str(temp_config)])
    assert code_rm == 0

    # Test removing nonexistent server returns 1
    code_rm_none = await run_mcp(["rm", "nonexistent", "-c", str(temp_config)])
    assert code_rm_none == 1


@pytest.mark.asyncio
async def test_firik_cli_delegation(temp_config: Path):
    # 'firik mcp list' delegation in firik_agent.api.cli
    ret = await api_run(["mcp", "list", "-c", str(temp_config)])
    assert ret == 0


@pytest.mark.asyncio
async def test_repl_mcp_slash_commands(temp_config: Path, mock_server_path: str):
    agent = Agent(config_path=str(temp_config))
    await agent.initialize()

    # 1. /mcp list when empty
    assert await handle_slash_command(agent, "/mcp list") is True

    # 2. /mcp add connects in-memory AND persists to config
    add_cmd = f'/mcp add repl_mock "{sys.executable}" "{mock_server_path}"'
    assert await handle_slash_command(agent, add_cmd) is True

    # Verify tool is available in agent tool registry
    assert agent.tools.get_tool("mcp_repl_mock_text_transformer") is not None

    # Verify saved in YAML config
    cfg = load_config(str(temp_config))
    assert "repl_mock" in cfg.get("mcp_servers", {})

    # 3. /mcp list when connected
    assert await handle_slash_command(agent, "/mcp list") is True

    # 4. /mcp rm removes from in-memory agent AND from config
    assert await handle_slash_command(agent, "/mcp rm repl_mock") is True
    assert agent.tools.get_tool("mcp_repl_mock_text_transformer") is None

    cfg_after = load_config(str(temp_config))
    assert "repl_mock" not in cfg_after.get("mcp_servers", {})

    await agent.close()
