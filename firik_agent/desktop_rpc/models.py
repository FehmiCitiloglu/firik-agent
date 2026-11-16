"""Wire models. Exported JSON Schema is also used by the TypeScript client."""
from __future__ import annotations

from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
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


class ModelsListParams(WireModel):
    silent: bool = False


class ApprovalResolveParams(WireModel):
    approval_id: str = Field(min_length=1, max_length=256)
    run_id: str = Field(min_length=1, max_length=256)
    allowed: bool


class MCPSetParams(ChatGetParams):
    server_name: str = Field(min_length=1, max_length=256)
    enabled: bool


class MCPNameParams(WireModel):
    name: str = Field(min_length=1, max_length=256)


class MCPToolPolicy(WireModel):
    mode: Literal["allow", "confirm", "deny"] = "allow"
    allowed_tools: list[str] = Field(default_factory=list)
    confirm_tools: list[str] = Field(default_factory=list)


class MCPConfig(MCPNameParams):
    transport: Literal["stdio", "http", "sse"] = "stdio"
    command: str = Field(default="", max_length=4096)
    args: list[str] = Field(default_factory=list, max_length=256)
    cwd: str = Field(default="", max_length=4096)
    url: str = Field(default="", max_length=8192)
    env: dict[str, str] = Field(default_factory=dict, max_length=256)
    secret_env: dict[str, str] = Field(default_factory=dict, max_length=256)
    headers: dict[str, str] = Field(default_factory=dict, max_length=256)
    autostart: bool = False
    default_for_chat: bool = False
    enabled: bool = True
    tool_policy: MCPToolPolicy = Field(default_factory=MCPToolPolicy)


class MCPSaveParams(WireModel):
    mode: Literal["create", "update"]
    config: MCPConfig

    @model_validator(mode="after")
    def valid_config(self):
        c = self.config
        if (c.name != c.name.strip() or any(ch in c.name for ch in "/\\")
                or any(ord(ch) < 32 for ch in c.name)):
            raise ValueError("Invalid MCP name")
        if c.transport == "stdio":
            if not c.command.strip():
                raise ValueError("MCP command is required")
        else:
            parsed = urlsplit(c.url)
            if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                    or parsed.username or parsed.password or parsed.fragment):
                raise ValueError("Invalid MCP URL")
        return self


class MCPCommandParams(MCPNameParams):
    command: Literal["connect", "disconnect", "restart", "test"]


class MCPServerInfo(WireModel):
    config: MCPConfig
    status: str
    tool_count: int
    error: str | None = None


class MCPListResult(WireModel):
    servers: list[MCPServerInfo]


class MCPOperationResult(WireModel):
    success: bool = True
    message: str
    tool_count: int = 0
    tool_names: list[str] = Field(default_factory=list)


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


class ProjectParams(WireModel):
    project_id: str = Field(min_length=1, max_length=256)


class ProjectCreateParams(WireModel):
    name: str = Field(min_length=1, max_length=200)
    local_path: str = Field(min_length=1, max_length=4096)


class ProjectInfo(WireModel):
    project_id: str
    name: str
    local_path: str


class ProjectsResult(WireModel):
    projects: list[ProjectInfo]


class ProjectFilesParams(ProjectParams):
    path: str = Field(default="", max_length=4096)
    query: str = Field(default="", max_length=200)


class ProjectFileEntry(WireModel):
    path: str
    name: str
    is_dir: bool


class ProjectFilesResult(WireModel):
    path: str
    entries: list[ProjectFileEntry]
    limited: bool = False


class ProjectReadParams(ProjectParams):
    path: str = Field(min_length=1, max_length=4096)


class ProjectReadResult(WireModel):
    path: str
    content: str
    truncated: bool = False


class CodingPreferences(WireModel):
    allow_reads: bool
    allow_edits: bool
    allow_terminal: bool
    allow_tests: bool


class CodingPreferencesUpdate(ProjectParams, CodingPreferences):
    pass


class CodingOpenParams(ProjectParams):
    record_id: str | None = Field(default=None, max_length=256)
    new_session: bool = False


class CodingSessionParams(ProjectParams):
    session_id: str = Field(min_length=1, max_length=256)


class CodingStartParams(CodingSessionParams):
    prompt: str = Field(min_length=1, max_length=65536)
    model: str = Field(min_length=1, max_length=256)
    mode: Literal["ask", "plan", "approved", "assisted"] = "approved"
    reasoning_effort: str | None = Field(default=None, max_length=32)
    context_paths: list[str] | None = Field(default=None, max_length=24)
    _nonblank = field_validator("prompt", "model")(ChatStartParams.nonblank.__func__)

    @field_validator("context_paths")
    @classmethod
    def bounded_context_paths(cls, value):
        if value is not None and any(not path.strip() or len(path) > 4096 or "\x00" in path for path in value):
            raise ValueError("Bağlam yolları geçerli proje yolları olmalıdır.")
        return value


class CodingControlParams(CodingSessionParams):
    run_id: str = Field(min_length=1, max_length=256)
    action: Literal["pause", "resume", "cancel", "approve"]
    request_id: str | None = Field(default=None, max_length=256)
    allowed: bool = False


class CodingRecordInfo(WireModel):
    record_id: str
    session_id: str
    title: str
    status: str
    created_at: str


class CodingHistoryResult(WireModel):
    records: list[CodingRecordInfo]


class CodingApproval(WireModel):
    request_id: str
    tool_name: str
    preview: str


class CodingState(WireModel):
    project_id: str
    session_id: str
    run_id: str = ""
    record_id: str = ""
    status: str = "idle"
    busy: bool = False
    model: str
    mode: str = "approved"
    reasoning_effort: str | None = None
    messages: list[ChatMessage] = Field(default_factory=list)
    plan: str = ""
    changes: str = ""
    tests: str = ""
    approvals: list[CodingApproval] = Field(default_factory=list)
    error: str | None = None


class TerminalStartParams(ProjectParams):
    command: str = Field(min_length=1, max_length=8000)
    cwd: str = Field(default=".", min_length=1, max_length=4096)
    timeout: float = Field(default=300, gt=0, le=3600)
    _nonblank = field_validator("command", "cwd")(ChatStartParams.nonblank.__func__)


class TerminalCancelParams(ProjectParams):
    command_id: str = Field(min_length=1, max_length=256)


class TerminalCommand(WireModel):
    command_id: str
    project_id: str
    source: Literal["user", "agent"]
    command: str
    cwd: str
    status: str = "starting"
    session_id: str = ""
    run_id: str = ""
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    truncated: bool = False
    timed_out: bool = False
    backend: str = "host"
    boundary: str = "host"
    duration_seconds: float = 0
    error: str = ""


class TerminalListResult(WireModel):
    commands: list[TerminalCommand]
    busy: bool
    backend: str = "host"
    boundary: str = "host"
    max_timeout: float = 300


class GitField(WireModel):
    name: str
    label: str
    default: str | int | bool | list[str] | None = None
    required: bool = False


class GitTool(WireModel):
    id: str
    title: str
    category: str
    description: str
    risk: str
    mutates: bool
    fields: list[GitField]


class GitFileInfo(WireModel):
    path: str
    index: str
    worktree: str
    original_path: str = ""


class GitOverview(WireModel):
    branch: str
    status: str
    diff: str
    commits: str
    tools: list[GitTool]
    files: list[GitFileInfo] = Field(default_factory=list)


class GitPreviewParams(ProjectParams):
    tool_id: str = Field(min_length=1, max_length=128)
    arguments: dict[str, str | int | bool | list[str]] = Field(default_factory=dict, max_length=20)


class GitPreview(WireModel):
    token: str
    title: str
    command: str
    requires_confirmation: bool
    warnings: list[str]


class GitExecuteParams(ProjectParams):
    token: str = Field(min_length=1, max_length=256)
    confirmed: bool = False


class GitResult(WireModel):
    ok: bool
    command: str
    stdout: str
    stderr: str
    returncode: int
    conflicts: list[str]
    truncated: bool


class VoicePreferences(WireModel):
    enabled: bool
    asr_provider: Literal["evren", "local"]
    tts_provider: Literal["evren", "system"]
    language: Literal["auto", "tr", "en"]
    read_replies: bool
    playback_speed: float = Field(ge=0.25, le=4)


class VoiceState(WireModel):
    conversation_id: str
    scope_id: str
    state: str
    conversation_active: bool
    paused: bool
    transcript: str = ""
    review_id: str = ""
    error: str | None = None
    settings: VoicePreferences


class VoiceCommandParams(ChatGetParams):
    scope_id: str = Field(min_length=1, max_length=256)
    action: Literal["record", "stop_recording", "conversation", "pause", "resume", "cancel", "speak", "stop_speaking", "discard", "submit"]
    text: str = Field(default="", max_length=32000)
    review_id: str = Field(default="", max_length=256)


class DesktopEvent(WireModel):
    backend_instance_id: str
    sequence: int
    kind: Literal["agent", "run", "approval", "approval_closed", "history", "warning", "coding", "voice", "terminal"]
    project_id: str | None = None
    conversation_id: str | None = None
    run_id: str | None = None
    event_type: str | None = None
    content: str | None = None
    tool_name: str | None = None
    run: RunState | None = None
    approval: ApprovalState | None = None
    approval_id: str | None = None
    command_id: str | None = None


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
    "models.list": (ModelsListParams, ModelsResult),
    "mcp.set": (MCPSetParams, ChatState),
    "mcp.list": (EmptyParams, MCPListResult),
    "mcp.save": (MCPSaveParams, MCPListResult),
    "mcp.remove": (MCPNameParams, MCPListResult),
    "mcp.command": (MCPCommandParams, MCPOperationResult),
    "approval.resolve": (ApprovalResolveParams, Ack),
    "browser.configure": (BrowserConfigureParams, BrowserStatusState),
    "browser.userCommand": (BrowserUserCommandParams, BrowserStatusState),
    "browser.userInput": (BrowserUserInputParams, BrowserStatusState),
    "projects.list": (EmptyParams, ProjectsResult),
    "projects.create": (ProjectCreateParams, ProjectInfo),
    "projects.files": (ProjectFilesParams, ProjectFilesResult),
    "projects.read": (ProjectReadParams, ProjectReadResult),
    "coding.preferences": (ProjectParams, CodingPreferences),
    "coding.preferences.update": (CodingPreferencesUpdate, CodingPreferences),
    "coding.open": (CodingOpenParams, CodingState),
    "coding.get": (CodingSessionParams, CodingState),
    "coding.history": (ProjectParams, CodingHistoryResult),
    "coding.start": (CodingStartParams, CodingState),
    "coding.control": (CodingControlParams, Ack),
    "terminal.list": (ProjectParams, TerminalListResult),
    "terminal.start": (TerminalStartParams, TerminalCommand),
    "terminal.cancel": (TerminalCancelParams, Ack),
    "git.overview": (ProjectParams, GitOverview),
    "git.preview": (GitPreviewParams, GitPreview),
    "git.execute": (GitExecuteParams, GitResult),
    "voice.get": (ChatGetParams, VoiceState),
    "voice.command": (VoiceCommandParams, VoiceState),
    "voice.settings": (VoicePreferences, VoicePreferences),
    "system.shutdown": (EmptyParams, Ack),
}

HOST_METHODS: dict[str, tuple[type[WireModel], type[WireModel]]] = {
    "browser.configureExecutor": (BrowserExecutorConfigParams, Ack),
    "browser.prepare": (BrowserPrepareParams, BrowserHostResult),
    "browser.execute": (BrowserExecuteParams, BrowserHostResult),
    "browser.revoke": (BrowserRevokeParams, BrowserHostResult),
}
