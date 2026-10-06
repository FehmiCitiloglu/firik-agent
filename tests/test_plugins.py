from __future__ import annotations
import pytest
from firik_agent.core.tools import ToolRegistry
from firik_agent.core.types import Message, ToolCall, ToolCallFunction
from firik_agent.plugins.base import BasePlugin
from firik_agent.plugins.manager import PluginManager


class SamplePlugin(BasePlugin):
    name = "sample"
    description = "A sample testing plugin"

    def __init__(self):
        super().__init__()
        self.message_hook_called = False

    async def on_user_message(self, text: str) -> str:
        self.message_hook_called = True
        return f"[MODIFIED] {text}"


@pytest.mark.asyncio
async def test_plugin_lifecycle_and_tools(tmp_path):
    registry = ToolRegistry()
    manager = PluginManager(registry, plugins_dir=tmp_path)

    # 1. Load builtins
    manager.load_builtins(["calculator"])
    assert "calculator" in manager.plugins
    assert registry.get_tool("calculate") is not None

    # Test calculator tool execution
    tc = ToolCall(
        id="call_calc",
        function=ToolCallFunction(
            name="calculate",
            arguments='{"expression": "100 * 2.5 + 50"}',
        ),
    )
    result = await registry.execute(tc)
    assert result.is_error is False
    assert result.content == "300.0"

    # 2. Register custom plugin
    sample = SamplePlugin()
    manager.register_plugin(sample)
    processed = await manager.dispatch_user_message("hello")
    assert sample.message_hook_called is True
    assert processed == "[MODIFIED] hello"

    # 3. Dynamic plugin from code
    dyn_code = """
from firik_agent.plugins.base import BasePlugin
from firik_agent.core.types import ToolDefinition

class DynamicEchoPlugin(BasePlugin):
    name = "dynamic_echo"
    description = "Echoes inputs"

    def get_tools(self):
        t = ToolDefinition(
            name="echo_text",
            description="Echoes back text",
            parameters={"type": "object", "properties": {"val": {"type": "string"}}, "required": ["val"]},
            source="plugin:dynamic_echo"
        )
        return [(t, lambda val: f"ECHO: {val}")]
"""
    plugin = manager.load_plugin_from_code("dynamic_echo", dyn_code)
    assert plugin.name == "dynamic_echo"
    assert registry.get_tool("echo_text") is not None

    tc_echo = ToolCall(
        id="call_echo",
        function=ToolCallFunction(
            name="echo_text",
            arguments='{"val": "FirikAgent Rocks"}',
        ),
    )
    res_echo = await registry.execute(tc_echo)
    assert res_echo.content == "ECHO: FirikAgent Rocks"
