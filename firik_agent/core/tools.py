from __future__ import annotations
import asyncio
import inspect
import json
import logging
from typing import Any, Callable, Dict, List, Optional
from firik_agent.core.types import ToolCall, ToolDefinition, ToolResult
from firik_agent.core.failures import FailureKind, RequestPhase, RuntimeFailure, normalize_failure
from firik_agent.core.recovery import SideEffectRisk, tool_semantics

logger = logging.getLogger(__name__)


class ToolRegistry:
    def __init__(self):
        self._tools: Dict[str, ToolDefinition] = {}
        self._handlers: Dict[str, Callable[..., Any]] = {}

    def register(
        self,
        tool_def: ToolDefinition,
        handler: Callable[..., Any],
    ) -> None:
        if tool_def.source.startswith("mcp:"):
            tool_def.execution_boundary = "remote_mcp"
        elif tool_def.source == "computer-use":
            tool_def.execution_boundary = "host"
        elif tool_def.execution_boundary == "unknown" and tool_def.source.startswith(("builtin", "meta:")):
            tool_def.execution_boundary = "host"
        self._tools[tool_def.name] = tool_def
        self._handlers[tool_def.name] = handler
        logger.debug("Registered tool: %s (%s)", tool_def.name, tool_def.source)

    def unregister(self, name: str) -> bool:
        if name in self._tools:
            del self._tools[name]
            self._handlers.pop(name, None)
            logger.debug("Unregistered tool: %s", name)
            return True
        return False

    def unregister_by_source(self, source: str) -> List[str]:
        removed = []
        for name, tool in list(self._tools.items()):
            if tool.source == source:
                self.unregister(name)
                removed.append(name)
        return removed

    def get_tool(self, name: str) -> Optional[ToolDefinition]:
        return self._tools.get(name)

    def list_tools(self) -> List[ToolDefinition]:
        return list(self._tools.values())

    def get_schemas(self) -> List[Dict[str, Any]]:
        return [tool.to_schema() for tool in self._tools.values()]

    async def execute(self, tool_call: ToolCall) -> ToolResult:
        result = await self._execute(tool_call)
        definition = self.get_tool(tool_call.function.name)
        result.metadata["execution_boundary"] = definition.execution_boundary if definition else "unknown"
        return result

    async def _execute(self, tool_call: ToolCall) -> ToolResult:
        name = tool_call.function.name
        args_str = tool_call.function.arguments

        handler = self._handlers.get(name)
        if not handler:
            return ToolResult(
                tool_call_id=tool_call.id,
                name=name,
                content=f"Error: Unknown tool '{name}'",
                is_error=True,
                metadata={"failure_kind": "unsupported_capability", "request_phase": "tool_not_dispatched"},
            )

        try:
            if isinstance(args_str, str) and args_str.strip():
                kwargs = json.loads(args_str)
            elif isinstance(args_str, dict):
                kwargs = args_str
            else:
                kwargs = {}
        except json.JSONDecodeError as e:
            return ToolResult(
                tool_call_id=tool_call.id,
                name=name,
                content=f"Error parsing JSON arguments for tool '{name}': {str(e)}",
                is_error=True,
                metadata={"failure_kind": "invalid_tool_arguments", "request_phase": "tool_not_dispatched"},
            )

        if not isinstance(kwargs, dict):
            return ToolResult(tool_call_id=tool_call.id, name=name, content="Tool arguments must be a JSON object.",
                              is_error=True, metadata={"failure_kind": "invalid_tool_arguments", "request_phase": "tool_not_dispatched"})
        try:
            signature = inspect.signature(handler)
        except (ValueError, TypeError):
            signature = None
        if signature is not None:
            try:
                signature.bind(**kwargs)
            except TypeError:
                return ToolResult(tool_call_id=tool_call.id, name=name, content="Tool parameters do not match the required arguments.",
                                  is_error=True, metadata={"failure_kind": "invalid_tool_arguments", "request_phase": "tool_not_dispatched"})

        try:
            if inspect.iscoroutinefunction(handler):
                result = await handler(**kwargs)
            else:
                result = await asyncio.to_thread(handler, **kwargs)
            if inspect.isawaitable(result):
                result = await result

            if isinstance(result, ToolResult):
                result.tool_call_id = tool_call.id
                if not result.name:
                    result.name = name
                result.metadata["execution_boundary"] = self._tools[name].execution_boundary
                return result

            if isinstance(result, (dict, list)):
                content = json.dumps(result, ensure_ascii=False, indent=2)
            else:
                content = str(result)

            return ToolResult(
                tool_call_id=tool_call.id,
                name=name,
                content=content,
                is_error=False,
                metadata={"execution_boundary": self._tools[name].execution_boundary},
            )

        except Exception as e:
            failure = normalize_failure(e, phase=RequestPhase.TOOL_DISPATCHED)
            risk = tool_semantics(name, self.get_tool(name))
            if risk not in {SideEffectRisk.PURE_READ, SideEffectRisk.STATE_INSPECTION} and failure.kind not in {
                    FailureKind.PERMISSION_DENIED, FailureKind.POLICY_REFUSAL, FailureKind.AUTHENTICATION_FAILED}:
                failure = RuntimeFailure(FailureKind.TOOL_OUTCOME_UNKNOWN, phase=RequestPhase.TOOL_DISPATCHED,
                                         source_type=type(e).__name__)
            logger.warning("Tool failed (%s): %s", name, failure.kind.value)
            return ToolResult(
                tool_call_id=tool_call.id,
                name=name,
                content=str(failure),
                is_error=True,
                metadata=failure.metadata,
            )


def tool(
    name: Optional[str] = None,
    description: Optional[str] = None,
    parameters: Optional[Dict[str, Any]] = None,
    source: str = "builtin",
):
    """Decorator to mark a function as an agent tool with auto-inferred schema."""
    def decorator(fn: Callable[..., Any]):
        tool_name = name or fn.__name__
        tool_desc = description or (fn.__doc__ or "").strip() or f"Executes {tool_name}"

        # If parameters not explicitly provided, infer from type hints
        if parameters is None:
            sig = inspect.signature(fn)
            props: Dict[str, Any] = {}
            required: List[str] = []

            type_map = {
                str: "string",
                int: "integer",
                float: "number",
                bool: "boolean",
                list: "array",
                dict: "object",
            }

            for p_name, param in sig.parameters.items():
                if p_name in ("self", "cls"):
                    continue
                p_type = type_map.get(param.annotation, "string")
                props[p_name] = {"type": p_type}
                if param.default is inspect.Parameter.empty:
                    required.append(p_name)

            tool_params = {
                "type": "object",
                "properties": props,
                "required": required,
            }
        else:
            tool_params = parameters

        fn._tool_definition = ToolDefinition(
            name=tool_name,
            description=tool_desc,
            parameters=tool_params,
            source=source,
        )
        return fn

    return decorator
