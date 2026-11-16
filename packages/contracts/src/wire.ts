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
export type Silent = boolean;
export type ConversationId6 = string;
export type ServerName = string;
export type Enabled2 = boolean;
export type Name1 = string;
export type Transport = 'stdio' | 'http' | 'sse';
export type Command = string;
/**
 * @maxItems 256
 */
export type Args = string[];
export type Cwd = string;
export type Url = string;
export type Autostart = boolean;
export type DefaultForChat = boolean;
export type Enabled3 = boolean;
export type Mode = 'allow' | 'confirm' | 'deny';
export type AllowedTools = string[];
export type ConfirmTools = string[];
export type Status2 = string;
export type ToolCount = number;
export type Error1 = string | null;
export type Servers = MCPServerInfo[];
export type Mode1 = 'create' | 'update';
export type Name2 = string;
export type Name3 = string;
export type Command1 = 'connect' | 'disconnect' | 'restart' | 'test';
export type Success = boolean;
export type Message = string;
export type ToolCount1 = number;
export type ToolNames = string[];
export type ApprovalId1 = string;
export type RunId3 = string;
export type Allowed = boolean;
export type ConversationId7 = string;
export type Enabled4 = boolean;
export type Origins1 = string[];
export type PrivateOrigins1 = string[];
export type Screenshots1 = boolean;
export type State = string;
export type ActiveTabId = string;
export type TabCount = number;
export type Revision = number;
export type Url1 = string;
export type Title2 = string;
export type TabId = string;
export type Url2 = string;
export type Title3 = string;
export type Active = boolean;
export type Tabs = BrowserTabInfo[];
export type ConversationId8 = string;
export type Command2 =
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
export type Url3 = string;
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
export type ProjectId = string;
export type Name4 = string;
export type LocalPath = string;
export type Projects = ProjectInfo[];
export type Name5 = string;
export type LocalPath1 = string;
export type ProjectId1 = string;
export type Path = string;
export type Query = string;
export type Path1 = string;
export type Path2 = string;
export type Name6 = string;
export type IsDir = boolean;
export type Entries = ProjectFileEntry[];
export type Limited = boolean;
export type ProjectId2 = string;
export type Path3 = string;
export type Path4 = string;
export type Content1 = string;
export type Truncated = boolean;
export type ProjectId3 = string;
export type AllowReads = boolean;
export type AllowEdits = boolean;
export type AllowTerminal = boolean;
export type AllowTests = boolean;
export type AllowReads1 = boolean;
export type AllowEdits1 = boolean;
export type AllowTerminal1 = boolean;
export type AllowTests1 = boolean;
export type ProjectId4 = string;
export type ProjectId5 = string;
export type RecordId = string | null;
export type NewSession = boolean;
export type ProjectId6 = string;
export type SessionId = string;
export type RunId4 = string;
export type RecordId1 = string;
export type Status3 = string;
export type Busy = boolean;
export type Model3 = string;
export type Mode2 = string;
export type ReasoningEffort2 = string | null;
export type Messages1 = ChatMessage[];
export type Plan = string;
export type Changes = string;
export type Tests = string;
export type RequestId = string;
export type ToolName1 = string;
export type Preview1 = string;
export type Approvals1 = CodingApproval[];
export type Error2 = string | null;
export type ProjectId7 = string;
export type SessionId1 = string;
export type RecordId2 = string;
export type SessionId2 = string;
export type Title4 = string;
export type Status4 = string;
export type CreatedAt1 = string;
export type Records = CodingRecordInfo[];
export type ProjectId8 = string;
export type SessionId3 = string;
export type Prompt1 = string;
export type Model4 = string;
export type Mode3 = 'ask' | 'plan' | 'approved' | 'assisted';
export type ReasoningEffort3 = string | null;
export type ContextPaths = string[] | null;
export type ProjectId9 = string;
export type SessionId4 = string;
export type RunId5 = string;
export type Action = 'pause' | 'resume' | 'cancel' | 'approve';
export type RequestId1 = string | null;
export type Allowed1 = boolean;
export type CommandId = string;
export type ProjectId10 = string;
export type Source = 'user' | 'agent';
export type Command3 = string;
export type Cwd1 = string;
export type Status5 = string;
export type SessionId5 = string;
export type RunId6 = string;
export type Stdout = string;
export type Stderr = string;
export type ExitCode = number | null;
export type Truncated1 = boolean;
export type TimedOut = boolean;
export type Backend = string;
export type Boundary = string;
export type DurationSeconds = number;
export type Error3 = string;
export type Commands = TerminalCommand[];
export type Busy1 = boolean;
export type Backend1 = string;
export type Boundary1 = string;
export type MaxTimeout = number;
export type ProjectId11 = string;
export type Command4 = string;
export type Cwd2 = string;
export type Timeout2 = number;
export type ProjectId12 = string;
export type CommandId1 = string;
export type Branch = string;
export type Status6 = string;
export type Diff = string;
export type Commits = string;
export type Id1 = string;
export type Title5 = string;
export type Category = string;
export type Description = string;
export type Risk = string;
export type Mutates = boolean;
export type Name7 = string;
export type Label = string;
export type Default = string | number | boolean | string[] | null;
export type Required = boolean;
export type Fields = GitField[];
export type Tools = GitTool[];
export type Path5 = string;
export type Index = string;
export type Worktree = string;
export type OriginalPath = string;
export type Files = GitFileInfo[];
export type ProjectId13 = string;
export type ToolId = string;
export type Token = string;
export type Title6 = string;
export type Command5 = string;
export type RequiresConfirmation = boolean;
export type Warnings = string[];
export type ProjectId14 = string;
export type Token1 = string;
export type Confirmed = boolean;
export type Ok1 = boolean;
export type Command6 = string;
export type Stdout1 = string;
export type Stderr1 = string;
export type Returncode = number;
export type Conflicts = string[];
export type Truncated2 = boolean;
export type ConversationId10 = string;
export type ScopeId = string;
export type State1 = string;
export type ConversationActive = boolean;
export type Paused = boolean;
export type Transcript = string;
export type ReviewId = string;
export type Error4 = string | null;
export type Enabled5 = boolean;
export type AsrProvider = 'evren' | 'local';
export type TtsProvider = 'evren' | 'system';
export type Language = 'auto' | 'tr' | 'en';
export type ReadReplies = boolean;
export type PlaybackSpeed = number;
export type ConversationId11 = string;
export type ScopeId1 = string;
export type Action1 =
  | 'record'
  | 'stop_recording'
  | 'conversation'
  | 'pause'
  | 'resume'
  | 'cancel'
  | 'speak'
  | 'stop_speaking'
  | 'discard'
  | 'submit';
export type Text1 = string;
export type ReviewId1 = string;
export type Enabled6 = boolean;
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
export type ConversationId12 = string;
export type ProjectId15 = string;
export type RunId7 = string;
export type ActorId = string;
export type ParentAgentId = string;
export type Enabled7 = boolean;
export type Origins2 = string[];
export type AllowPrivateOrigins = string[];
export type AllowScreenshots = boolean;
export type AllowUploads = boolean;
export type AllowDownloads = boolean;
export type AllowPublicHttps = boolean;
export type Operation = string;
export type Ok2 = boolean;
export type ExecutionId = string;
export type Operation1 = string;
export type ApprovedFingerprint = string | null;
export type HostControl = boolean;
export type CloseOwner = boolean;
export type CloseAll = boolean;
export type BackendInstanceId2 = string;
export type Sequence1 = number;
export type Kind1 =
  'agent' | 'run' | 'approval' | 'approval_closed' | 'history' | 'warning' | 'coding' | 'voice' | 'terminal';
export type ProjectId16 = string | null;
export type ConversationId13 = string | null;
export type RunId8 = string | null;
export type EventType = string | null;
export type Content2 = string | null;
export type ToolName2 = string | null;
export type ApprovalId2 = string | null;
export type CommandId2 = string | null;
export type ConversationId14 = string;
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
  ModelsListParams: ModelsListParams;
  ModelsResult: ModelsResult;
  MCPSetParams: MCPSetParams;
  MCPListResult: MCPListResult;
  MCPSaveParams: MCPSaveParams;
  MCPNameParams: MCPNameParams;
  MCPCommandParams: MCPCommandParams;
  MCPOperationResult: MCPOperationResult;
  ApprovalResolveParams: ApprovalResolveParams;
  BrowserConfigureParams: BrowserConfigureParams;
  BrowserStatusState: BrowserStatusState;
  BrowserUserCommandParams: BrowserUserCommandParams;
  BrowserUserInputParams: BrowserUserInputParams;
  ProjectsResult: ProjectsResult;
  ProjectCreateParams: ProjectCreateParams;
  ProjectInfo: ProjectInfo;
  ProjectFilesParams: ProjectFilesParams;
  ProjectFilesResult: ProjectFilesResult;
  ProjectReadParams: ProjectReadParams;
  ProjectReadResult: ProjectReadResult;
  ProjectParams: ProjectParams;
  CodingPreferences: CodingPreferences;
  CodingPreferencesUpdate: CodingPreferencesUpdate;
  CodingOpenParams: CodingOpenParams;
  CodingState: CodingState;
  CodingSessionParams: CodingSessionParams;
  CodingHistoryResult: CodingHistoryResult;
  CodingStartParams: CodingStartParams;
  CodingControlParams: CodingControlParams;
  TerminalListResult: TerminalListResult;
  TerminalStartParams: TerminalStartParams;
  TerminalCommand: TerminalCommand;
  TerminalCancelParams: TerminalCancelParams;
  GitOverview: GitOverview;
  GitPreviewParams: GitPreviewParams;
  GitPreview: GitPreview;
  GitExecuteParams: GitExecuteParams;
  GitResult: GitResult;
  VoiceState: VoiceState;
  VoiceCommandParams: VoiceCommandParams;
  VoicePreferences: VoicePreferences;
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
export interface ModelsListParams {
  silent?: Silent;
}
export interface MCPSetParams {
  conversation_id: ConversationId6;
  server_name: ServerName;
  enabled: Enabled2;
}
export interface MCPListResult {
  servers: Servers;
}
export interface MCPServerInfo {
  config: MCPConfig;
  status: Status2;
  tool_count: ToolCount;
  error?: Error1;
}
export interface MCPConfig {
  name: Name1;
  transport?: Transport;
  command?: Command;
  args?: Args;
  cwd?: Cwd;
  url?: Url;
  env?: Env;
  secret_env?: SecretEnv;
  headers?: Headers;
  autostart?: Autostart;
  default_for_chat?: DefaultForChat;
  enabled?: Enabled3;
  tool_policy?: MCPToolPolicy;
}
export interface Env {
  [k: string]: string;
}
export interface SecretEnv {
  [k: string]: string;
}
export interface Headers {
  [k: string]: string;
}
export interface MCPToolPolicy {
  mode?: Mode;
  allowed_tools?: AllowedTools;
  confirm_tools?: ConfirmTools;
}
export interface MCPSaveParams {
  mode: Mode1;
  config: MCPConfig;
}
export interface MCPNameParams {
  name: Name2;
}
export interface MCPCommandParams {
  name: Name3;
  command: Command1;
}
export interface MCPOperationResult {
  success?: Success;
  message: Message;
  tool_count?: ToolCount1;
  tool_names?: ToolNames;
}
export interface ApprovalResolveParams {
  approval_id: ApprovalId1;
  run_id: RunId3;
  allowed: Allowed;
}
export interface BrowserConfigureParams {
  conversation_id: ConversationId7;
  enabled: Enabled4;
  origins?: Origins1;
  private_origins?: PrivateOrigins1;
  screenshots?: Screenshots1;
}
export interface BrowserStatusState {
  state?: State;
  active_tab_id?: ActiveTabId;
  tab_count?: TabCount;
  revision?: Revision;
  url?: Url1;
  title?: Title2;
  tabs?: Tabs;
  grant?: BrowserGrantState;
}
export interface BrowserTabInfo {
  tab_id: TabId;
  url?: Url2;
  title?: Title3;
  active?: Active;
}
export interface BrowserUserCommandParams {
  conversation_id: ConversationId8;
  command: Command2;
  url?: Url3;
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
export interface ProjectsResult {
  projects: Projects;
}
export interface ProjectInfo {
  project_id: ProjectId;
  name: Name4;
  local_path: LocalPath;
}
export interface ProjectCreateParams {
  name: Name5;
  local_path: LocalPath1;
}
export interface ProjectFilesParams {
  project_id: ProjectId1;
  path?: Path;
  query?: Query;
}
export interface ProjectFilesResult {
  path: Path1;
  entries: Entries;
  limited?: Limited;
}
export interface ProjectFileEntry {
  path: Path2;
  name: Name6;
  is_dir: IsDir;
}
export interface ProjectReadParams {
  project_id: ProjectId2;
  path: Path3;
}
export interface ProjectReadResult {
  path: Path4;
  content: Content1;
  truncated?: Truncated;
}
export interface ProjectParams {
  project_id: ProjectId3;
}
export interface CodingPreferences {
  allow_reads: AllowReads;
  allow_edits: AllowEdits;
  allow_terminal: AllowTerminal;
  allow_tests: AllowTests;
}
export interface CodingPreferencesUpdate {
  allow_reads: AllowReads1;
  allow_edits: AllowEdits1;
  allow_terminal: AllowTerminal1;
  allow_tests: AllowTests1;
  project_id: ProjectId4;
}
export interface CodingOpenParams {
  project_id: ProjectId5;
  record_id?: RecordId;
  new_session?: NewSession;
}
export interface CodingState {
  project_id: ProjectId6;
  session_id: SessionId;
  run_id?: RunId4;
  record_id?: RecordId1;
  status?: Status3;
  busy?: Busy;
  model: Model3;
  mode?: Mode2;
  reasoning_effort?: ReasoningEffort2;
  messages?: Messages1;
  plan?: Plan;
  changes?: Changes;
  tests?: Tests;
  approvals?: Approvals1;
  error?: Error2;
}
export interface CodingApproval {
  request_id: RequestId;
  tool_name: ToolName1;
  preview: Preview1;
}
export interface CodingSessionParams {
  project_id: ProjectId7;
  session_id: SessionId1;
}
export interface CodingHistoryResult {
  records: Records;
}
export interface CodingRecordInfo {
  record_id: RecordId2;
  session_id: SessionId2;
  title: Title4;
  status: Status4;
  created_at: CreatedAt1;
}
export interface CodingStartParams {
  project_id: ProjectId8;
  session_id: SessionId3;
  prompt: Prompt1;
  model: Model4;
  mode?: Mode3;
  reasoning_effort?: ReasoningEffort3;
  context_paths?: ContextPaths;
}
export interface CodingControlParams {
  project_id: ProjectId9;
  session_id: SessionId4;
  run_id: RunId5;
  action: Action;
  request_id?: RequestId1;
  allowed?: Allowed1;
}
export interface TerminalListResult {
  commands: Commands;
  busy: Busy1;
  backend?: Backend1;
  boundary?: Boundary1;
  max_timeout?: MaxTimeout;
}
export interface TerminalCommand {
  command_id: CommandId;
  project_id: ProjectId10;
  source: Source;
  command: Command3;
  cwd: Cwd1;
  status?: Status5;
  session_id?: SessionId5;
  run_id?: RunId6;
  stdout?: Stdout;
  stderr?: Stderr;
  exit_code?: ExitCode;
  truncated?: Truncated1;
  timed_out?: TimedOut;
  backend?: Backend;
  boundary?: Boundary;
  duration_seconds?: DurationSeconds;
  error?: Error3;
}
export interface TerminalStartParams {
  project_id: ProjectId11;
  command: Command4;
  cwd?: Cwd2;
  timeout?: Timeout2;
}
export interface TerminalCancelParams {
  project_id: ProjectId12;
  command_id: CommandId1;
}
export interface GitOverview {
  branch: Branch;
  status: Status6;
  diff: Diff;
  commits: Commits;
  tools: Tools;
  files?: Files;
}
export interface GitTool {
  id: Id1;
  title: Title5;
  category: Category;
  description: Description;
  risk: Risk;
  mutates: Mutates;
  fields: Fields;
}
export interface GitField {
  name: Name7;
  label: Label;
  default?: Default;
  required?: Required;
}
export interface GitFileInfo {
  path: Path5;
  index: Index;
  worktree: Worktree;
  original_path?: OriginalPath;
}
export interface GitPreviewParams {
  project_id: ProjectId13;
  tool_id: ToolId;
  arguments?: Arguments;
}
export interface Arguments {
  [k: string]: string | number | boolean | string[];
}
export interface GitPreview {
  token: Token;
  title: Title6;
  command: Command5;
  requires_confirmation: RequiresConfirmation;
  warnings: Warnings;
}
export interface GitExecuteParams {
  project_id: ProjectId14;
  token: Token1;
  confirmed?: Confirmed;
}
export interface GitResult {
  ok: Ok1;
  command: Command6;
  stdout: Stdout1;
  stderr: Stderr1;
  returncode: Returncode;
  conflicts: Conflicts;
  truncated: Truncated2;
}
export interface VoiceState {
  conversation_id: ConversationId10;
  scope_id: ScopeId;
  state: State1;
  conversation_active: ConversationActive;
  paused: Paused;
  transcript?: Transcript;
  review_id?: ReviewId;
  error?: Error4;
  settings: VoicePreferences;
}
export interface VoicePreferences {
  enabled: Enabled5;
  asr_provider: AsrProvider;
  tts_provider: TtsProvider;
  language: Language;
  read_replies: ReadReplies;
  playback_speed: PlaybackSpeed;
}
export interface VoiceCommandParams {
  conversation_id: ConversationId11;
  scope_id: ScopeId1;
  action: Action1;
  text?: Text1;
  review_id?: ReviewId1;
}
export interface BrowserExecutorConfigParams {
  config: BrowserConfig;
}
export interface BrowserConfig {
  enabled?: Enabled6;
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
  arguments?: Arguments1;
}
export interface BrowserIdentityWire {
  conversation_id: ConversationId12;
  project_id?: ProjectId15;
  run_id?: RunId7;
  actor_id?: ActorId;
  parent_agent_id?: ParentAgentId;
}
export interface BrowserGrantWire {
  enabled?: Enabled7;
  origins?: Origins2;
  allow_private_origins?: AllowPrivateOrigins;
  allow_screenshots?: AllowScreenshots;
  allow_uploads?: AllowUploads;
  allow_downloads?: AllowDownloads;
  allow_public_https?: AllowPublicHttps;
}
export interface Arguments1 {
  [k: string]: unknown;
}
export interface BrowserHostResult {
  ok?: Ok2;
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
  arguments?: Arguments2;
  approved_fingerprint?: ApprovedFingerprint;
  host_control?: HostControl;
}
export interface Arguments2 {
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
  project_id?: ProjectId16;
  conversation_id?: ConversationId13;
  run_id?: RunId8;
  event_type?: EventType;
  content?: Content2;
  tool_name?: ToolName2;
  run?: RunState | null;
  approval?: ApprovalState | null;
  approval_id?: ApprovalId2;
  command_id?: CommandId2;
}
export interface BrowserPreviewParams {
  conversation_id: ConversationId14;
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
  'models.list': { params: ModelsListParams; result: ModelsResult };
  'mcp.set': { params: MCPSetParams; result: ChatState };
  'mcp.list': { params: EmptyParams; result: MCPListResult };
  'mcp.save': { params: MCPSaveParams; result: MCPListResult };
  'mcp.remove': { params: MCPNameParams; result: MCPListResult };
  'mcp.command': { params: MCPCommandParams; result: MCPOperationResult };
  'approval.resolve': { params: ApprovalResolveParams; result: Ack };
  'browser.configure': { params: BrowserConfigureParams; result: BrowserStatusState };
  'browser.userCommand': { params: BrowserUserCommandParams; result: BrowserStatusState };
  'browser.userInput': { params: BrowserUserInputParams; result: BrowserStatusState };
  'projects.list': { params: EmptyParams; result: ProjectsResult };
  'projects.create': { params: ProjectCreateParams; result: ProjectInfo };
  'projects.files': { params: ProjectFilesParams; result: ProjectFilesResult };
  'projects.read': { params: ProjectReadParams; result: ProjectReadResult };
  'coding.preferences': { params: ProjectParams; result: CodingPreferences };
  'coding.preferences.update': { params: CodingPreferencesUpdate; result: CodingPreferences };
  'coding.open': { params: CodingOpenParams; result: CodingState };
  'coding.get': { params: CodingSessionParams; result: CodingState };
  'coding.history': { params: ProjectParams; result: CodingHistoryResult };
  'coding.start': { params: CodingStartParams; result: CodingState };
  'coding.control': { params: CodingControlParams; result: Ack };
  'terminal.list': { params: ProjectParams; result: TerminalListResult };
  'terminal.start': { params: TerminalStartParams; result: TerminalCommand };
  'terminal.cancel': { params: TerminalCancelParams; result: Ack };
  'git.overview': { params: ProjectParams; result: GitOverview };
  'git.preview': { params: GitPreviewParams; result: GitPreview };
  'git.execute': { params: GitExecuteParams; result: GitResult };
  'voice.get': { params: ChatGetParams; result: VoiceState };
  'voice.command': { params: VoiceCommandParams; result: VoiceState };
  'voice.settings': { params: VoicePreferences; result: VoicePreferences };
  'system.shutdown': { params: EmptyParams; result: Ack };
}

export interface DesktopHostMethods {
  'browser.configureExecutor': { params: BrowserExecutorConfigParams; result: Ack };
  'browser.prepare': { params: BrowserPrepareParams; result: BrowserHostResult };
  'browser.execute': { params: BrowserExecuteParams; result: BrowserHostResult };
  'browser.revoke': { params: BrowserRevokeParams; result: BrowserHostResult };
}
