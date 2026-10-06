from __future__ import annotations
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, Field


class JSONRPCRequest(BaseModel):
    jsonrpc: str = "2.0"
    id: Union[str, int]
    method: str
    params: Optional[Dict[str, Any]] = None


class JSONRPCNotification(BaseModel):
    jsonrpc: str = "2.0"
    method: str
    params: Optional[Dict[str, Any]] = None


class JSONRPCResponse(BaseModel):
    jsonrpc: str = "2.0"
    id: Optional[Union[str, int]] = None
    result: Optional[Any] = None
    error: Optional[Dict[str, Any]] = None


class MCPToolSchema(BaseModel):
    name: str
    description: Optional[str] = ""
    inputSchema: Dict[str, Any] = Field(default_factory=lambda: {"type": "object"})


class MCPContentItem(BaseModel):
    type: str
    text: Optional[str] = None
    data: Optional[str] = None
    mimeType: Optional[str] = None


class MCPCallToolResult(BaseModel):
    content: List[MCPContentItem] = Field(default_factory=list)
    isError: bool = False
