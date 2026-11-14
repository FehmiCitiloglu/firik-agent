/* Generated from Python desktop_rpc/models.py. Do not edit. */

export type ProtocolVersion = 1;
export type ProtocolVersion1 = 1;
export type BackendInstanceId = string;
export type AppVersion = string;
export type Capabilities = string[];
export type BackendInstanceId1 = string;
export type Sequence = number;
export type BaseUrl = string;
export type DefaultModel = string;
export type Timeout = number;
export type HasApiKey = boolean;
export type ConversationId = string;
export type Title = string;
export type CreatedAt = number;
export type UpdatedAt = number;
export type Conversations = ChatSummary[];
export type RunId = string;
export type Generation = number;
export type ConversationId1 = string;
export type Status = 'running' | 'completed' | 'cancelled' | 'failed';
export type Error = string | null;
export type Runs = RunState[];
export type ApprovalId = string;
export type RunId1 = string;
export type ConversationId2 = string;
export type ToolName = string;
export type Preview = string;
export type Approvals = ApprovalState[];
export type Models = string[];
export type Name = string;
export type Status1 = string;
export type Enabled = boolean;
export type Mcps = MCPState[];
export type Model = string;
export type ConversationId3 = string;
export type Title1 = string;
export type Model1 = string;
export type ReasoningEffort = string | null;
export type Id = string;
export type Role = 'user' | 'assistant';
export type Content = string;
export type Pending = boolean;
export type Timestamp = string;
export type Messages = ChatMessage[];
export type EarlierMessages = number;
export type ActiveMcpServers = string[];
export type Enabled1 = boolean;
export type Origins = string[];
export type PrivateOrigins = string[];
export type Screenshots = boolean;
export type PublicHttps = boolean;
export type ConversationId4 = string;
export type ConversationId5 = string;
export type Prompt = string;
export type Model2 = string;
export type ReasoningEffort1 = string | null;
export type RunId2 = string;
export type Ok = true;
export type BaseUrl1 = string;
export type DefaultModel1 = string;
export type Timeout1 = number;
export type ApiKey = string;
export type ConversationId6 = string;
export type ServerName = string;
export type Enabled2 = boolean;
export type ApprovalId1 = string;
export type RunId3 = string;
export type Allowed = boolean;
export type ConversationId7 = string;
export type Enabled3 = boolean;
export type Origins1 = string[];
export type PrivateOrigins1 = string[];
export type Screenshots1 = boolean;
export type State = string;
export type ActiveTabId = string;
export type TabCount = number;
export type Revision = number;
export type Url = string;
export type Title2 = string;
export type TabId = string;
export type Url1 = string;
export type Title3 = string;
export type Active = boolean;
export type Tabs = BrowserTabInfo[];
export type ConversationId8 = string;
export type Command =
  | 'start'
  | 'status'
  | 'navigate'
  | 'back'
  | 'forward'
  | 'reload'
  | 'list_tabs'
  | 'new_tab'
  | 'select_tab'
  | 'close_tab'
  | 'close'
  | 'resize'
  | 'request_handoff'
  | 'resume_after_handoff';
export type Url2 = string;
export type TabId1 = string;
export type Width = number;
export type Height = number;
export type ConversationId9 = string;
export type Kind = 'click' | 'wheel' | 'type' | 'key';
export type X = number;
export type Y = number;
export type DeltaX = number;
export type DeltaY = number;
export type Text = string;
export type Key = string;
export type Enabled4 = boolean;
export type Headless = boolean;
export type StrictNetworkIsolation = boolean;
export type MaxSessions = number;
export type MaxTabsPerSession = number;
export type MaxActionsPerTask = number;
export type ActionTimeoutMs = number;
export type NavigationTimeoutMs = number;
export type LaunchTimeoutMs = number;
export type MaxSnapshotChars = number;
export type MaxDiagnostics = number;
export type MaxScreenshotBytes = number;
export type ApprovalTtlSeconds = number;
export type ViewportWidth = number;
export type ViewportHeight = number;
export type Locale = string;
export type TimezoneId = string;
export type ConversationId10 = string;
export type ProjectId = string;
export type RunId4 = string;
export type ActorId = string;
export type ParentAgentId = string;
export type Enabled5 = boolean;
export type Origins2 = string[];
export type AllowPrivateOrigins = string[];
export type AllowScreenshots = boolean;
export type AllowUploads = boolean;
export type AllowDownloads = boolean;
export type AllowPublicHttps = boolean;
export type Operation = string;
export type Ok1 = boolean;
export type ExecutionId = string;
export type Operation1 = string;
export type ApprovedFingerprint = string | null;
export type HostControl = boolean;
export type CloseOwner = boolean;
export type CloseAll = boolean;
export type BackendInstanceId2 = string;
export type Sequence1 = number;
export type Kind1 = 'agent' | 'run' | 'approval' | 'approval_closed' | 'history' | 'warning';
export type ConversationId11 = string | null;
export type RunId5 = string | null;
export type EventType = string | null;
export type Content1 = string | null;
export type ToolName1 = string | null;
export type ApprovalId2 = string | null;
export type ConversationId12 = string;
export type Width1 = number;
export type Height1 = number;

export interface DesktopWireTypes {
  HelloParams: HelloParams;
  HelloResult: HelloResult;
  EmptyParams: EmptyParams;
  Snapshot: Snapshot;
  ChatCreateParams: ChatCreateParams;
  ChatState: ChatState;
  ChatGetParams: ChatGetParams;
  ChatStartParams: ChatStartParams;
  RunState: RunState;
  RunCancelParams: RunCancelParams;
  Ack: Ack;
  SettingsState: SettingsState;
  SettingsUpdateParams: SettingsUpdateParams;
  CredentialsSetParams: CredentialsSetParams;
  ModelsResult: ModelsResult;
  MCPSetParams: MCPSetParams;
  ApprovalResolveParams: ApprovalResolveParams;
  BrowserConfigureParams: BrowserConfigureParams;
  BrowserStatusState: BrowserStatusState;
  BrowserUserCommandParams: BrowserUserCommandParams;
  BrowserUserInputParams: BrowserUserInputParams;
  BrowserExecutorConfigParams: BrowserExecutorConfigParams;
  BrowserPrepareParams: BrowserPrepareParams;
  BrowserHostResult: BrowserHostResult;
  BrowserExecuteParams: BrowserExecuteParams;
  BrowserRevokeParams: BrowserRevokeParams;
  DesktopEvent: DesktopEvent;
  BrowserPreviewParams: BrowserPreviewParams;
}
export interface HelloParams {
  protocol_version: ProtocolVersion;
}
export interface HelloResult {
  protocol_version: ProtocolVersion1;
  backend_instance_id: BackendInstanceId;
  app_version: AppVersion;
  capabilities: Capabilities;
}
export interface EmptyParams {}
export interface Snapshot {
  backend_instance_id: BackendInstanceId1;
  sequence: Sequence;
  settings: SettingsState;
  conversations: Conversations;
  runs: Runs;
  approvals: Approvals;
  models: ModelsResult;
  mcps: Mcps;
}
export interface SettingsState {
  base_url: BaseUrl;
  default_model: DefaultModel;
  timeout: Timeout;
  has_api_key: HasApiKey;
}
export interface ChatSummary {
  conversation_id: ConversationId;
  title: Title;
  created_at: CreatedAt;
  updated_at: UpdatedAt;
}
export interface RunState {
  run_id: RunId;
  generation: Generation;
  conversation_id: ConversationId1;
  status: Status;
  error: Error;
}
export interface ApprovalState {
  approval_id: ApprovalId;
  run_id: RunId1;
  conversation_id: ConversationId2;
  tool_name: ToolName;
  preview: Preview;
}
export interface ModelsResult {
  models: Models;
  effort_levels: EffortLevels;
}
export interface EffortLevels {
  [k: string]: string[];
}
export interface MCPState {
  name: Name;
  status: Status1;
  enabled: Enabled;
}
export interface ChatCreateParams {
  model?: Model;
}
export interface ChatState {
  conversation_id: ConversationId3;
  title: Title1;
  model: Model1;
  reasoning_effort: ReasoningEffort;
  messages: Messages;
  earlier_messages: EarlierMessages;
  active_mcp_servers: ActiveMcpServers;
  run: RunState | null;
  browser?: BrowserGrantState;
}
export interface ChatMessage {
  id: Id;
  role: Role;
  content: Content;
  pending: Pending;
  timestamp: Timestamp;
}
export interface BrowserGrantState {
  enabled?: Enabled1;
  origins?: Origins;
  private_origins?: PrivateOrigins;
  screenshots?: Screenshots;
  public_https?: PublicHttps;
}
export interface ChatGetParams {
  conversation_id: ConversationId4;
}
export interface ChatStartParams {
  conversation_id: ConversationId5;
  prompt: Prompt;
  model: Model2;
  reasoning_effort?: ReasoningEffort1;
}
export interface RunCancelParams {
  run_id: RunId2;
}
export interface Ack {
  ok?: Ok;
}
export interface SettingsUpdateParams {
  base_url: BaseUrl1;
  default_model: DefaultModel1;
  timeout: Timeout1;
}
export interface CredentialsSetParams {
  api_key: ApiKey;
}
export interface MCPSetParams {
  conversation_id: ConversationId6;
  server_name: ServerName;
  enabled: Enabled2;
}
export interface ApprovalResolveParams {
  approval_id: ApprovalId1;
  run_id: RunId3;
  allowed: Allowed;
}
export interface BrowserConfigureParams {
  conversation_id: ConversationId7;
  enabled: Enabled3;
  origins?: Origins1;
  private_origins?: PrivateOrigins1;
  screenshots?: Screenshots1;
}
export interface BrowserStatusState {
  state?: State;
  active_tab_id?: ActiveTabId;
  tab_count?: TabCount;
  revision?: Revision;
  url?: Url;
  title?: Title2;
  tabs?: Tabs;
  grant?: BrowserGrantState;
}
export interface BrowserTabInfo {
  tab_id: TabId;
  url?: Url1;
  title?: Title3;
  active?: Active;
}
export interface BrowserUserCommandParams {
  conversation_id: ConversationId8;
  command: Command;
  url?: Url2;
  tab_id?: TabId1;
  width?: Width;
  height?: Height;
}
export interface BrowserUserInputParams {
  conversation_id: ConversationId9;
  kind: Kind;
  x?: X;
  y?: Y;
  delta_x?: DeltaX;
  delta_y?: DeltaY;
  text?: Text;
  key?: Key;
}
export interface BrowserExecutorConfigParams {
  config: BrowserConfig;
}
export interface BrowserConfig {
  enabled?: Enabled4;
  headless?: Headless;
  strict_network_isolation?: StrictNetworkIsolation;
  max_sessions?: MaxSessions;
  max_tabs_per_session?: MaxTabsPerSession;
  max_actions_per_task?: MaxActionsPerTask;
  action_timeout_ms?: ActionTimeoutMs;
  navigation_timeout_ms?: NavigationTimeoutMs;
  launch_timeout_ms?: LaunchTimeoutMs;
  max_snapshot_chars?: MaxSnapshotChars;
  max_diagnostics?: MaxDiagnostics;
  max_screenshot_bytes?: MaxScreenshotBytes;
  approval_ttl_seconds?: ApprovalTtlSeconds;
  viewport_width?: ViewportWidth;
  viewport_height?: ViewportHeight;
  locale?: Locale;
  timezone_id?: TimezoneId;
}
export interface BrowserPrepareParams {
  identity: BrowserIdentityWire;
  grant: BrowserGrantWire;
  operation: Operation;
  arguments?: Arguments;
}
export interface BrowserIdentityWire {
  conversation_id: ConversationId10;
  project_id?: ProjectId;
  run_id?: RunId4;
  actor_id?: ActorId;
  parent_agent_id?: ParentAgentId;
}
export interface BrowserGrantWire {
  enabled?: Enabled5;
  origins?: Origins2;
  allow_private_origins?: AllowPrivateOrigins;
  allow_screenshots?: AllowScreenshots;
  allow_uploads?: AllowUploads;
  allow_downloads?: AllowDownloads;
  allow_public_https?: AllowPublicHttps;
}
export interface Arguments {
  [k: string]: unknown;
}
export interface BrowserHostResult {
  ok?: Ok1;
  payload?: Payload;
}
export interface Payload {
  [k: string]: unknown;
}
export interface BrowserExecuteParams {
  execution_id: ExecutionId;
  identity: BrowserIdentityWire;
  grant: BrowserGrantWire;
  operation: Operation1;
  arguments?: Arguments1;
  approved_fingerprint?: ApprovedFingerprint;
  host_control?: HostControl;
}
export interface Arguments1 {
  [k: string]: unknown;
}
export interface BrowserRevokeParams {
  identity?: BrowserIdentityWire | null;
  grant?: BrowserGrantWire | null;
  close_owner?: CloseOwner;
  close_all?: CloseAll;
}
export interface DesktopEvent {
  backend_instance_id: BackendInstanceId2;
  sequence: Sequence1;
  kind: Kind1;
  conversation_id?: ConversationId11;
  run_id?: RunId5;
  event_type?: EventType;
  content?: Content1;
  tool_name?: ToolName1;
  run?: RunState | null;
  approval?: ApprovalState | null;
  approval_id?: ApprovalId2;
}
export interface BrowserPreviewParams {
  conversation_id: ConversationId12;
  width?: Width1;
  height?: Height1;
}

export interface DesktopMethods {
  'system.hello': { params: HelloParams; result: HelloResult };
  'state.snapshot': { params: EmptyParams; result: Snapshot };
  'chat.create': { params: ChatCreateParams; result: ChatState };
  'chat.get': { params: ChatGetParams; result: ChatState };
  'chat.start': { params: ChatStartParams; result: RunState };
  'run.cancel': { params: RunCancelParams; result: Ack };
  'settings.get': { params: EmptyParams; result: SettingsState };
  'settings.update': { params: SettingsUpdateParams; result: SettingsState };
  'credentials.set': { params: CredentialsSetParams; result: Ack };
  'models.list': { params: EmptyParams; result: ModelsResult };
  'mcp.set': { params: MCPSetParams; result: ChatState };
  'approval.resolve': { params: ApprovalResolveParams; result: Ack };
  'browser.configure': { params: BrowserConfigureParams; result: BrowserStatusState };
  'browser.userCommand': { params: BrowserUserCommandParams; result: BrowserStatusState };
  'browser.userInput': { params: BrowserUserInputParams; result: BrowserStatusState };
  'system.shutdown': { params: EmptyParams; result: Ack };
}

export interface DesktopHostMethods {
  'browser.configureExecutor': { params: BrowserExecutorConfigParams; result: Ack };
  'browser.prepare': { params: BrowserPrepareParams; result: BrowserHostResult };
  'browser.execute': { params: BrowserExecuteParams; result: BrowserHostResult };
  'browser.revoke': { params: BrowserRevokeParams; result: BrowserHostResult };
}
