import type { BrowserPreviewParams, DesktopEvent, DesktopMethods } from './wire.js';

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
  };
  openLink: (url: string) => Promise<void>;
  onEvent: (callback: (event: DesktopEvent) => void) => () => void;
  onDisconnect: (callback: (message: string) => void) => () => void;
}
