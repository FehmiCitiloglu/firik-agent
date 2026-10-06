from __future__ import annotations
from typing import Any, Callable, Dict, List, Literal, Optional, Union
from pydantic import BaseModel, Field


class ToolCallFunction(BaseModel):
    name: str
    arguments: str


class ToolCall(BaseModel):
    id: str
    type: Literal["function"] = "function"
    function: ToolCallFunction


class ContentPart(BaseModel):
    type: Literal["text", "image", "resource"] = "text"
    text: Optional[str] = None
    data: Optional[str] = None  # Base64 string for images/binaries
    mime_type: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class Message(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: Optional[Union[str, List[Dict[str, Any]]]] = None
    parts: Optional[List[ContentPart]] = None
    reasoning: Optional[str] = None
    name: Optional[str] = None
    tool_call_id: Optional[str] = None
    tool_calls: Optional[List[ToolCall]] = None

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {"role": self.role}
        if self.content is not None:
            data["content"] = self.content
        if self.name is not None:
            data["name"] = self.name
        if self.tool_call_id is not None:
            data["tool_call_id"] = self.tool_call_id
        if self.tool_calls:
            data["tool_calls"] = [tc.model_dump() for tc in self.tool_calls]
        return data


class ToolResult(BaseModel):
    tool_call_id: str
    name: str
    content: str
    is_error: bool = False
    parts: Optional[List[ContentPart]] = None

    def to_message(self) -> Message:
        return Message(
            role="tool",
            name=self.name,
            tool_call_id=self.tool_call_id,
            content=self.content,
            parts=self.parts,
        )



class ToolDefinition(BaseModel):
    name: str
    description: str
    parameters: Dict[str, Any] = Field(default_factory=dict)
    source: str = "builtin"  # "builtin", "mcp", "skill", "plugin"

    def to_schema(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters or {
                    "type": "object",
                    "properties": {},
                },
            },
        }


class StreamChunk(BaseModel):
    delta_content: Optional[str] = None
    delta_reasoning: Optional[str] = None
    tool_calls: Optional[List[ToolCall]] = None
    is_done: bool = False
    # Providers include nested cache/reasoning token details in usage frames.
    usage: Optional[Dict[str, Any]] = None


class ModelInfo(BaseModel):
    id: str
    name: str
    context_window: int = 128000
    supports_tools: bool = True
    supports_vision: bool = False
    description: Optional[str] = None
