import type { DesktopMethods, DesktopEvent } from './wire.js';

type Call<M extends keyof DesktopMethods> = (params: DesktopMethods[M]['params']) => Promise<DesktopMethods[M]['result']>;
export interface DesktopAPI {
  snapshot: Call<'state.snapshot'>;
  chat: { create: Call<'chat.create'>; get: Call<'chat.get'>; start: Call<'chat.start'> };
  runs: { cancel: Call<'run.cancel'> };
  settings: { get: Call<'settings.get'>; update: Call<'settings.update'>; setKey: Call<'credentials.set'> };
  models: { list: Call<'models.list'> };
  mcp: { set: Call<'mcp.set'> };
  approvals: { resolve: Call<'approval.resolve'> };
  openLink: (url: string) => Promise<void>;
  onEvent: (callback: (event: DesktopEvent) => void) => () => void;
  onDisconnect: (callback: (message: string) => void) => () => void;
}
