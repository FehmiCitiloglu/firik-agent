import type { BrowserPreviewParams, DesktopEvent, DesktopMethods } from './wire.js';

export type BrowserEngineMode = 'embedded' | 'playwright';

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
  snapshot: Call<'state.snapshot'>;
  chat: { create: Call<'chat.create'>; get: Call<'chat.get'>; start: Call<'chat.start'> };
  runs: { cancel: Call<'run.cancel'> };
  settings: { get: Call<'settings.get'>; update: Call<'settings.update'>; setKey: Call<'credentials.set'> };
  models: { list: Call<'models.list'> };
  mcp: { set: Call<'mcp.set'> };
  approvals: { resolve: Call<'approval.resolve'> };
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
