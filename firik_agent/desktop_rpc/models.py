"""Wire models. Exported JSON Schema is also used by the TypeScript client."""
from __future__ import annotations

from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator
from firik_agent.browser.config import BrowserConfig


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


class BrowserGrantState(WireModel):
    enabled: bool = False
    origins: list[str] = Field(default_factory=list)
    private_origins: list[str] = Field(default_factory=list)
    screenshots: bool = False
    public_https: bool = True


class BrowserTabInfo(WireModel):
    tab_id: str
    url: str = ""
    title: str = ""
    active: bool = False


class BrowserStatusState(WireModel):
    state: str = "uninitialized"
    active_tab_id: str = ""
    tab_count: int = 0
    revision: int = 0
    url: str = ""
    title: str = ""
    tabs: list[BrowserTabInfo] = Field(default_factory=list)
    grant: BrowserGrantState = Field(default_factory=BrowserGrantState)


class BrowserConfigureParams(ChatGetParams):
    enabled: bool
    origins: list[str] = Field(default_factory=list)
    private_origins: list[str] = Field(default_factory=list)
    screenshots: bool = False


class BrowserUserCommandParams(ChatGetParams):
    command: Literal[
        "start",
        "status",
        "navigate",
        "back",
        "forward",
        "reload",
        "list_tabs",
        "new_tab",
        "select_tab",
        "close_tab",
        "close",
        "resize",
        "request_handoff",
        "resume_after_handoff",
    ]
    url: str = Field(default="", max_length=2048)
    tab_id: str = Field(default="", max_length=64)
    width: int = Field(default=0, ge=0, le=2560)
    height: int = Field(default=0, ge=0, le=1440)


class BrowserUserInputParams(ChatGetParams):
    kind: Literal["click", "wheel", "type", "key"]
    x: float = Field(default=0.0, ge=0.0, le=1.0)
    y: float = Field(default=0.0, ge=0.0, le=1.0)
    delta_x: float = Field(default=0.0, ge=-2400.0, le=2400.0)
    delta_y: float = Field(default=0.0, ge=-2400.0, le=2400.0)
    text: str = Field(default="", max_length=512)
    key: str = Field(default="", max_length=64)


class BrowserPreviewParams(ChatGetParams):
    width: int = Field(default=960, ge=240, le=1440)
    height: int = Field(default=600, ge=180, le=900)


class BrowserIdentityWire(WireModel):
    conversation_id: str = Field(min_length=1, max_length=256)
    project_id: str = Field(default="", max_length=256)
    run_id: str = Field(default="", max_length=256)
    actor_id: str = Field(default="parent", min_length=1, max_length=256)
    parent_agent_id: str = Field(default="", max_length=256)


class BrowserGrantWire(WireModel):
    enabled: bool = False
    origins: list[str] = Field(default_factory=list)
    allow_private_origins: list[str] = Field(default_factory=list)
    allow_screenshots: bool = False
    allow_uploads: bool = False
    allow_downloads: bool = False
    allow_public_https: bool = True


class BrowserPrepareParams(WireModel):
    identity: BrowserIdentityWire
    grant: BrowserGrantWire
    operation: str = Field(min_length=1, max_length=128)
    arguments: dict[str, Any] = Field(default_factory=dict)


class BrowserExecuteParams(WireModel):
    execution_id: str = Field(min_length=1, max_length=128)
    identity: BrowserIdentityWire
    grant: BrowserGrantWire
    operation: str = Field(min_length=1, max_length=128)
    arguments: dict[str, Any] = Field(default_factory=dict)
    approved_fingerprint: str | None = Field(default=None, max_length=256)
    host_control: bool = False


class BrowserRevokeParams(WireModel):
    identity: BrowserIdentityWire | None = None
    grant: BrowserGrantWire | None = None
    close_owner: bool = False
    close_all: bool = False


class BrowserHostResult(WireModel):
    ok: bool = True
    payload: dict[str, Any] = Field(default_factory=dict)


class BrowserExecutorConfigParams(WireModel):
    config: BrowserConfig


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
    browser: BrowserGrantState = Field(default_factory=BrowserGrantState)


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
    "browser.configure": (BrowserConfigureParams, BrowserStatusState),
    "browser.userCommand": (BrowserUserCommandParams, BrowserStatusState),
    "browser.userInput": (BrowserUserInputParams, BrowserStatusState),
    "system.shutdown": (EmptyParams, Ack),
}

HOST_METHODS: dict[str, tuple[type[WireModel], type[WireModel]]] = {
    "browser.configureExecutor": (BrowserExecutorConfigParams, Ack),
    "browser.prepare": (BrowserPrepareParams, BrowserHostResult),
    "browser.execute": (BrowserExecuteParams, BrowserHostResult),
    "browser.revoke": (BrowserRevokeParams, BrowserHostResult),
}
