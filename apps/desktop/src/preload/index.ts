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
  desktop: {
    selectProjectDirectory: params => ipcRenderer.invoke('firik:desktop.selectProjectDirectory', params),
  },
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
  mcp: {
    set: params => ipcRenderer.invoke('firik:mcp.set', params),
    list: params => ipcRenderer.invoke('firik:mcp.list', params),
    save: params => ipcRenderer.invoke('firik:mcp.save', params),
    remove: params => ipcRenderer.invoke('firik:mcp.remove', params),
    command: params => ipcRenderer.invoke('firik:mcp.command', params),
  },
  approvals: { resolve: params => ipcRenderer.invoke('firik:approval.resolve', params) },
  projects: {
    list: params => ipcRenderer.invoke('firik:projects.list', params),
    create: params => ipcRenderer.invoke('firik:projects.create', params),
    files: params => ipcRenderer.invoke('firik:projects.files', params),
    read: params => ipcRenderer.invoke('firik:projects.read', params),
  },
  coding: {
    open: params => ipcRenderer.invoke('firik:coding.open', params),
    get: params => ipcRenderer.invoke('firik:coding.get', params),
    history: params => ipcRenderer.invoke('firik:coding.history', params),
    start: params => ipcRenderer.invoke('firik:coding.start', params),
    control: params => ipcRenderer.invoke('firik:coding.control', params),
    preferences: params => ipcRenderer.invoke('firik:coding.preferences', params),
    updatePreferences: params => ipcRenderer.invoke('firik:coding.preferences.update', params),
  },
  terminal: {
    list: params => ipcRenderer.invoke('firik:terminal.list', params),
    start: params => ipcRenderer.invoke('firik:terminal.start', params),
    cancel: params => ipcRenderer.invoke('firik:terminal.cancel', params),
  },
  git: {
    overview: params => ipcRenderer.invoke('firik:git.overview', params),
    preview: params => ipcRenderer.invoke('firik:git.preview', params),
    execute: params => ipcRenderer.invoke('firik:git.execute', params),
  },
  voice: {
    get: params => ipcRenderer.invoke('firik:voice.get', params),
    command: params => ipcRenderer.invoke('firik:voice.command', params),
    settings: params => ipcRenderer.invoke('firik:voice.settings', params),
  },
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
