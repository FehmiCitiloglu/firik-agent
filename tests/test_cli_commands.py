from __future__ import annotations
import pytest
from prompt_toolkit.document import Document

from firik_agent.core.agent import Agent
from firik_agent.cli import (
    COMMANDS_CATALOG,
    AgentCompleter,
    handle_slash_command,
    print_commands_table,
)


def test_commands_catalog_structure():
    assert len(COMMANDS_CATALOG) >= 15
    for item in COMMANDS_CATALOG:
        assert "command" in item
        assert "category" in item
        assert "syntax" in item
        assert "description" in item
        assert item["command"].startswith("/")


def test_print_commands_table_runs():
    # Verify execution without errors
    print_commands_table()
    print_commands_table(category_filter="skills")
    print_commands_table(category_filter="nonexistent_filter_xyz")


@pytest.mark.asyncio
async def test_slash_command_list_and_commands():
    agent = Agent()
    await agent.initialize()

    # /commands and /list should return True (keep going in REPL)
    assert await handle_slash_command(agent, "/commands") is True
    assert await handle_slash_command(agent, "/list") is True
    assert await handle_slash_command(agent, "/list mcp") is True

    await agent.close()


@pytest.mark.asyncio
async def test_agent_completer():
    agent = Agent()
    await agent.initialize()
    completer = AgentCompleter(agent)

    # 1. Non-slash input should yield no completions
    doc_normal = Document("Hello agent, write a script")
    assert list(completer.get_completions(doc_normal, None)) == []

    # 2. Root completion for /sk
    doc_sk = Document("/sk")
    c_sk = [c.text for c in completer.get_completions(doc_sk, None)]
    assert "/skill" in c_sk

    # 3. Root completion for /co
    doc_co = Document("/co")
    c_co = [c.text for c in completer.get_completions(doc_co, None)]
    assert "/commands" in c_co

    # 4. Subcommands for /skill <space>
    doc_skill_sub = Document("/skill ")
    c_skill_sub = [c.text for c in completer.get_completions(doc_skill_sub, None)]
    assert "on" in c_skill_sub
    assert "off" in c_skill_sub
    assert "list" in c_skill_sub
    assert "info" in c_skill_sub

    # 5. Subcommands for /mcp <space>
    doc_mcp_sub = Document("/mcp ")
    c_mcp_sub = [c.text for c in completer.get_completions(doc_mcp_sub, None)]
    assert "list" in c_mcp_sub
    assert "add" in c_mcp_sub
    assert "remove" in c_mcp_sub
    assert "rm" in c_mcp_sub

    # 6. Provider completions
    doc_prov = Document("/provider ")
    c_prov = [c.text for c in completer.get_completions(doc_prov, None)]
    assert "firik" in c_prov

    # 7. Skill name completions for /skill on
    doc_skill_on = Document("/skill on ")
    c_skill_on = [c.text for c in completer.get_completions(doc_skill_on, None)]
    assert len(c_skill_on) > 0

    await agent.close()
