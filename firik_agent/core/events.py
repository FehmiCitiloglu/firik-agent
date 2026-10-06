from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
import time
from typing import Any, Dict, Optional


class AgentEventType(str, Enum):
    CONTEXT_COMPACTION_STARTED = "context_compaction_started"
    CONTEXT_COMPACTED = "context_compacted"
    MODEL_REQUEST_STARTED = "model_request_started"
    TEXT_DELTA = "text_delta"
    REASONING_DELTA = "reasoning_delta"
    TOOL_CALL_STARTED = "tool_call_started"
    TOOL_CALL_ARGUMENTS = "tool_call_arguments"
    TOOL_CALL_RESULT = "tool_call_result"
    TOOL_CALL_ERROR = "tool_call_error"
    MCP_CONNECTING = "mcp_connecting"
    MCP_CONNECTED = "mcp_connected"
    MCP_ERROR = "mcp_error"
    TASK_STATE = "task_state"
    PLAN_UPDATED = "plan_updated"
    APPROVAL_REQUIRED = "approval_required"
    APPROVAL_DECIDED = "approval_decided"
    FILE_CHANGED = "file_changed"
    VERIFICATION = "verification"
    MODEL_USAGE = "model_usage"
    SUBAGENT_EVENT = "subagent_event"
    DONE = "done"
    ERROR = "error"


@dataclass
class AgentEvent:
    type: AgentEventType
    session_id: str
    timestamp: float = field(default_factory=time.time)
    tool_name: Optional[str] = None
    server_name: Optional[str] = None
    content: Optional[str] = None
    arguments: Optional[Dict[str, Any]] = None
    duration_ms: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    project_id: Optional[str] = None
    task_id: Optional[str] = None
