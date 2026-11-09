import { contextBridge, ipcRenderer } from 'electron';
import type { DesktopAPI } from '@firik/contracts/desktop-api';
import type { DesktopEvent } from '@firik/contracts';

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
  openLink: url => ipcRenderer.invoke('firik:link.open', url),
  onEvent(callback: (event: DesktopEvent) => void) {
    const listener = (_event: unknown, value: DesktopEvent) => callback(value);
    ipcRenderer.on('firik:event', listener);
    return () => { ipcRenderer.removeListener('firik:event', listener); };
  },
  onDisconnect(callback) {
    const listener = (_event: unknown, message: string) => callback(message);
    ipcRenderer.on('firik:disconnected', listener);
    return () => { ipcRenderer.removeListener('firik:disconnected', listener); };
  },
};
contextBridge.exposeInMainWorld('firik', api);
