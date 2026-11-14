import { contextBridge, ipcRenderer } from 'electron';
import type { DesktopAPI } from '@firik/contracts/desktop-api';
import type { DesktopEvent } from '@firik/contracts';

function subscribe<T>(channel: string, callback: (value: T) => void): () => void {
  const listener = (_event: unknown, value: T) => callback(value);
  ipcRenderer.on(channel, listener);
  return () => {
    ipcRenderer.removeListener(channel, listener);
  };
}

// Each public method has its own fixed channel. IPC events/objects never cross
// the bridge, and renderer code cannot choose arbitrary host channels.
const api: DesktopAPI = {
  snapshot: params => ipcRenderer.invoke('firik:state.snapshot', params),
  chat: {
    create: params => ipcRenderer.invoke('firik:chat.create', params),
    get: params => ipcRenderer.invoke('firik:chat.get', params),
    start: params => ipcRenderer.invoke('firik:chat.start', params),
  },
  runs: { cancel: params => ipcRenderer.invoke('firik:run.cancel', params) },
  settings: {
    get: params => ipcRenderer.invoke('firik:settings.get', params),
    update: params => ipcRenderer.invoke('firik:settings.update', params),
    setKey: params => ipcRenderer.invoke('firik:credentials.set', params),
  },
  models: { list: params => ipcRenderer.invoke('firik:models.list', params) },
  mcp: { set: params => ipcRenderer.invoke('firik:mcp.set', params) },
  approvals: { resolve: params => ipcRenderer.invoke('firik:approval.resolve', params) },
  browser: {
    configure: params => ipcRenderer.invoke('firik:browser.configure', params),
    command: params => ipcRenderer.invoke('firik:browser.userCommand', params),
    input: params => ipcRenderer.invoke('firik:browser.userInput', params),
    preview: params => ipcRenderer.invoke('firik:browser.preview', params),
    setViewLayout: params => ipcRenderer.invoke('firik:browser.setViewLayout', params),
    getEngine: () => ipcRenderer.invoke('firik:browser.getEngine'),
    setEngine: engine => ipcRenderer.invoke('firik:browser.setEngine', engine),
    onPointer: callback => subscribe('firik:browser.pointer', callback),
  },
  openLink: url => ipcRenderer.invoke('firik:link.open', url),
  onEvent: (callback: (event: DesktopEvent) => void) => subscribe('firik:event', callback),
  onDisconnect: callback => subscribe('firik:disconnected', callback),
};
contextBridge.exposeInMainWorld('firik', api);
