from __future__ import annotations
import pytest
from firik_agent.core.tools import ToolRegistry
from firik_agent.core.types import Message, ToolCall, ToolCallFunction, ToolResult
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


@pytest.mark.asyncio
async def test_logger_failure_exposes_diagnostics_without_raw_exception_text(caplog):
    from firik_agent.plugins.builtins.logger import LoggerPlugin
    from firik_agent.core.failures import FailureKind, RuntimeFailure

    error = RuntimeFailure(FailureKind.REQUEST_TOO_LARGE, http_status=413, request_id="req-log-123",
                           source_type="HTTPStatusError")
    error.args = ("password=TOP-SECRET private provider body",)
    await LoggerPlugin().on_error(error)
    assert "failure_kind=request_too_large" in caplog.text
    assert "http_status=413" in caplog.text
    assert "provider_request_id=req-log-123" in caplog.text
    assert "TOP-SECRET" not in caplog.text


@pytest.mark.asyncio
async def test_logger_never_logs_unknown_exception_messages(caplog):
    from firik_agent.plugins.builtins.logger import LoggerPlugin

    await LoggerPlugin().on_error(ValueError("private prompt TOP-SECRET"))
    assert "error_type=ValueError" in caplog.text
    assert "TOP-SECRET" not in caplog.text


@pytest.mark.asyncio
async def test_default_logger_records_counts_without_private_prompt_arguments_or_results(caplog):
    from firik_agent.plugins.builtins.logger import LoggerPlugin

    caplog.set_level("DEBUG", logger="firik.plugins.logger")
    plugin = LoggerPlugin()
    private = "Private transcript and password=TOP-SECRET"
    call = ToolCall(id="call-private", function=ToolCallFunction(name="read_file", arguments=private))
    await plugin.on_user_message(private)
    await plugin.on_model_request([Message(role="user", content=private)], [])
    await plugin.on_model_response(Message(role="assistant", content=private))
    await plugin.on_tool_call(call)
    await plugin.on_tool_result(ToolResult(tool_call_id=call.id, name="read_file", content=private))
    assert "characters=" in caplog.text and "read_file" in caplog.text
    assert private not in caplog.text and "TOP-SECRET" not in caplog.text


@pytest.mark.asyncio
async def test_failing_plugin_hook_keeps_private_exception_text_out_of_logs(tmp_path, caplog):
    class FailingPlugin(BasePlugin):
        name = "failing"

        async def on_user_message(self, text):
            raise RuntimeError(text)

    manager = PluginManager(ToolRegistry(), plugins_dir=tmp_path)
    manager.register_plugin(FailingPlugin())
    private = "Private microphone transcript TOP-SECRET"
    assert await manager.dispatch_user_message(private) == private
    assert "error_type=RuntimeError" in caplog.text
    assert private not in caplog.text and "TOP-SECRET" not in caplog.text
