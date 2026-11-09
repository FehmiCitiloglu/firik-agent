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
export type Enabled1 = boolean;
export type ApprovalId1 = string;
export type RunId3 = string;
export type Allowed = boolean;
export type BackendInstanceId2 = string;
export type Sequence1 = number;
export type Kind = 'agent' | 'run' | 'approval' | 'approval_closed' | 'history' | 'warning';
export type ConversationId7 = string | null;
export type RunId4 = string | null;
export type EventType = string | null;
export type Content1 = string | null;
export type ToolName1 = string | null;
export type ApprovalId2 = string | null;

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
  DesktopEvent: DesktopEvent;
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
}
export interface ChatMessage {
  id: Id;
  role: Role;
  content: Content;
  pending: Pending;
  timestamp: Timestamp;
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
  enabled: Enabled1;
}
export interface ApprovalResolveParams {
  approval_id: ApprovalId1;
  run_id: RunId3;
  allowed: Allowed;
}
export interface DesktopEvent {
  backend_instance_id: BackendInstanceId2;
  sequence: Sequence1;
  kind: Kind;
  conversation_id?: ConversationId7;
  run_id?: RunId4;
  event_type?: EventType;
  content?: Content1;
  tool_name?: ToolName1;
  run?: RunState | null;
  approval?: ApprovalState | null;
  approval_id?: ApprovalId2;
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
  'system.shutdown': { params: EmptyParams; result: Ack };
}
