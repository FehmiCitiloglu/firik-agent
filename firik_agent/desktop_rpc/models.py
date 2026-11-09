"""Wire models. Exported JSON Schema is also used by the TypeScript client."""
from __future__ import annotations

from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator


class WireModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class EmptyParams(WireModel):
    pass


class HelloParams(WireModel):
    protocol_version: Literal[1]

    @field_validator("protocol_version", mode="before")
    @classmethod
    def exact_protocol(cls, value):
        if type(value) is not int or value != 1:
            raise ValueError("Unsupported protocol")
        return value


class ChatCreateParams(WireModel):
    model: str = Field(default="", max_length=256)


class ChatGetParams(WireModel):
    conversation_id: str = Field(min_length=1, max_length=256)


class ChatStartParams(ChatGetParams):
    prompt: str = Field(min_length=1, max_length=65536)
    model: str = Field(min_length=1, max_length=256)
    reasoning_effort: str | None = Field(default=None, max_length=32)

    @field_validator("prompt", "model")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Blank input")
        return value


class RunCancelParams(WireModel):
    run_id: str = Field(min_length=1, max_length=256)


class SettingsUpdateParams(WireModel):
    base_url: str = Field(min_length=1, max_length=2048)
    default_model: str = Field(min_length=1, max_length=256)
    timeout: float = Field(ge=5, le=1800)

    @field_validator("base_url")
    @classmethod
    def valid_endpoint(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (parsed.scheme not in {"https", "http"} or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise ValueError("Invalid provider URL")
        return value.rstrip("/")


class CredentialsSetParams(WireModel):
    api_key: str = Field(min_length=1, max_length=8192)


class ApprovalResolveParams(WireModel):
    approval_id: str = Field(min_length=1, max_length=256)
    run_id: str = Field(min_length=1, max_length=256)
    allowed: bool


class MCPSetParams(ChatGetParams):
    server_name: str = Field(min_length=1, max_length=256)
    enabled: bool


class SettingsState(WireModel):
    base_url: str
    default_model: str
    timeout: float
    has_api_key: bool


class HelloResult(WireModel):
    protocol_version: Literal[1]
    backend_instance_id: str
    app_version: str
    capabilities: list[str]


class ChatSummary(WireModel):
    conversation_id: str
    title: str
    created_at: float
    updated_at: float


class ChatMessage(WireModel):
    id: str
    role: Literal["user", "assistant"]
    content: str
    pending: bool
    timestamp: str


class RunState(WireModel):
    run_id: str
    generation: int = Field(ge=1)
    conversation_id: str
    status: Literal["running", "completed", "cancelled", "failed"]
    error: str | None


class ApprovalState(WireModel):
    approval_id: str
    run_id: str
    conversation_id: str
    tool_name: str
    preview: str


class ChatState(WireModel):
    conversation_id: str
    title: str
    model: str
    reasoning_effort: str | None
    messages: list[ChatMessage]
    earlier_messages: int
    active_mcp_servers: list[str]
    run: RunState | None


class ModelsResult(WireModel):
    models: list[str]
    effort_levels: dict[str, list[str]]


class MCPState(WireModel):
    name: str
    status: str
    enabled: bool


class Snapshot(WireModel):
    backend_instance_id: str
    sequence: int
    settings: SettingsState
    conversations: list[ChatSummary]
    runs: list[RunState]
    approvals: list[ApprovalState]
    models: ModelsResult
    mcps: list[MCPState]


class DesktopEvent(WireModel):
    backend_instance_id: str
    sequence: int
    kind: Literal["agent", "run", "approval", "approval_closed", "history", "warning"]
    conversation_id: str | None = None
    run_id: str | None = None
    event_type: str | None = None
    content: str | None = None
    tool_name: str | None = None
    run: RunState | None = None
    approval: ApprovalState | None = None
    approval_id: str | None = None


class Ack(WireModel):
    ok: Literal[True] = True


METHODS: dict[str, tuple[type[WireModel], type[WireModel]]] = {
    "system.hello": (HelloParams, HelloResult),
    "state.snapshot": (EmptyParams, Snapshot),
    "chat.create": (ChatCreateParams, ChatState),
    "chat.get": (ChatGetParams, ChatState),
    "chat.start": (ChatStartParams, RunState),
    "run.cancel": (RunCancelParams, Ack),
    "settings.get": (EmptyParams, SettingsState),
    "settings.update": (SettingsUpdateParams, SettingsState),
    "credentials.set": (CredentialsSetParams, Ack),
    "models.list": (EmptyParams, ModelsResult),
    "mcp.set": (MCPSetParams, ChatState),
    "approval.resolve": (ApprovalResolveParams, Ack),
    "system.shutdown": (EmptyParams, Ack),
}
