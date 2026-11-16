import type { BrowserPreviewParams, DesktopEvent, DesktopMethods } from './wire.js';

export type BrowserEngineMode = 'embedded' | 'playwright';

export interface SelectProjectDirectoryParams {
  default_path?: string;
}

export interface ProjectDirectorySelection {
  canceled: boolean;
  path: string | null;
  name: string | null;
}

export type BrowserPointerAction =
  | 'move'
  | 'hover'
  | 'click'
  | 'fill'
  | 'type'
  | 'select'
  | 'check'
  | 'scroll'
  | 'idle';

export interface BrowserPointerState {
  conversation_id: string;
  visible: boolean;
  x: number;
  y: number;
  viewport_width: number;
  viewport_height: number;
  action: BrowserPointerAction;
  pressing: boolean;
  label?: string;
}

export interface BrowserViewLayoutParams {
  conversation_id: string | null;
  visible: boolean;
  obscured: boolean;
  x: number;
  y: number;
  width: number;
  height: number;
  fit_viewport: boolean;
}

type Call<M extends keyof DesktopMethods> = (params: DesktopMethods[M]['params']) => Promise<DesktopMethods[M]['result']>;
export interface DesktopAPI {
  desktop: {
    selectProjectDirectory: (params: SelectProjectDirectoryParams) => Promise<ProjectDirectorySelection>;
  };
  snapshot: Call<'state.snapshot'>;
  chat: { create: Call<'chat.create'>; get: Call<'chat.get'>; start: Call<'chat.start'> };
  runs: { cancel: Call<'run.cancel'> };
  settings: { get: Call<'settings.get'>; update: Call<'settings.update'>; setKey: Call<'credentials.set'> };
  models: { list: Call<'models.list'> };
  mcp: { set: Call<'mcp.set'>; list: Call<'mcp.list'>; save: Call<'mcp.save'>;
    remove: Call<'mcp.remove'>; command: Call<'mcp.command'> };
  approvals: { resolve: Call<'approval.resolve'> };
  projects: {
    list: Call<'projects.list'>; create: Call<'projects.create'>;
    files: Call<'projects.files'>; read: Call<'projects.read'>;
  };
  coding: {
    open: Call<'coding.open'>; get: Call<'coding.get'>; history: Call<'coding.history'>;
    start: Call<'coding.start'>; control: Call<'coding.control'>;
    preferences: Call<'coding.preferences'>; updatePreferences: Call<'coding.preferences.update'>;
  };
  terminal: { list: Call<'terminal.list'>; start: Call<'terminal.start'>; cancel: Call<'terminal.cancel'> };
  git: { overview: Call<'git.overview'>; preview: Call<'git.preview'>; execute: Call<'git.execute'> };
  voice: { get: Call<'voice.get'>; command: Call<'voice.command'>; settings: Call<'voice.settings'> };
  browser: {
    configure: Call<'browser.configure'>;
    command: Call<'browser.userCommand'>;
    input: Call<'browser.userInput'>;
    preview: (params: BrowserPreviewParams) => Promise<Uint8Array | null>;
    setViewLayout: (params: BrowserViewLayoutParams) => Promise<void>;
    getEngine: () => Promise<BrowserEngineMode>;
    setEngine: (engine: BrowserEngineMode) => Promise<BrowserEngineMode>;
    onPointer?: (callback: (pointer: BrowserPointerState) => void) => () => void;
  };
  openLink: (url: string) => Promise<void>;
  onEvent: (callback: (event: DesktopEvent) => void) => () => void;
  onDisconnect: (callback: (message: string) => void) => () => void;
}
