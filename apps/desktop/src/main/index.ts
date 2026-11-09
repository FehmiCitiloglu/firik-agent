import { app, BrowserWindow, ipcMain, Menu, net, protocol, session, shell } from 'electron';
import { existsSync } from 'node:fs';
import { join, resolve, sep } from 'node:path';
import { pathToFileURL } from 'node:url';
import { parseParams, PROTOCOL_VERSION, type Method } from '@firik/contracts';
import { BackendClient } from './rpc-client.js';

protocol.registerSchemesAsPrivileged([{ scheme: 'firik', privileges: {
  standard: true, secure: true, supportFetchAPI: true, corsEnabled: true,
} }]);

const root = resolve(__dirname, '../../../..');
const rendererRoot = resolve(__dirname, '../renderer');
const devURL = !app.isPackaged && process.env.FIRIK_DESKTOP_DEV_URL === 'http://127.0.0.1:5173'
  ? 'http://127.0.0.1:5173' : null;
let window: BrowserWindow | null = null;
let backend: BackendClient;
let ready: Promise<unknown>;
let closing = false;

function trustedURL(value: string): boolean {
  try {
    const url = new URL(value);
    return devURL ? url.origin === devURL : url.protocol === 'firik:' && url.hostname === 'app';
  } catch { return false; }
}

function startBackend() {
  const executable = process.platform === 'win32' ? 'firik-backend.exe' : 'firik-backend';
  const localPython = join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
  const command = app.isPackaged ? join(process.resourcesPath, 'backend', executable)
    : process.env.FIRIK_DESKTOP_PYTHON || (existsSync(localPython) ? localPython : process.platform === 'win32' ? 'python' : 'python3');
  const args = app.isPackaged ? [] : ['-m', 'firik_agent.desktop_rpc'];
  if (process.env.FIRIK_DESKTOP_CONFIG) args.push('--config', process.env.FIRIK_DESKTOP_CONFIG);
  backend = new BackendClient(command, args, app.isPackaged ? app.getPath('userData') : root,
    { ...process.env, PYTHONUNBUFFERED: '1' });
  backend.on('event', event => {
    if (window && !window.isDestroyed()) window.webContents.send('firik:event', event);
  });
  backend.on('disconnected', (message: string) => {
    if (window && !window.isDestroyed()) window.webContents.send('firik:disconnected', message);
  });
  ready = backend.request('system.hello', { protocol_version: PROTOCOL_VERSION });
  ready.catch(() => {}); // The UI's snapshot request presents the startup error.
}

function registerIPC() {
  const methods: Method[] = ['state.snapshot', 'chat.create', 'chat.get', 'chat.start', 'run.cancel',
    'settings.get', 'settings.update', 'credentials.set', 'models.list', 'mcp.set', 'approval.resolve'];
  for (const method of methods) {
    ipcMain.handle(`firik:${method}`, async (event, input: unknown) => {
      if (!window || event.sender !== window.webContents || event.senderFrame !== window.webContents.mainFrame
          || !trustedURL(event.senderFrame.url)) throw new Error('Geçersiz masaüstü istemcisi.');
      const params = parseParams(method, input);
      await ready;
      return backend.request(method, params);
    });
  }
  ipcMain.handle('firik:link.open', async (event, input: unknown) => {
    if (!window || event.sender !== window.webContents || event.senderFrame !== window.webContents.mainFrame
        || !trustedURL(event.senderFrame.url)) throw new Error('Geçersiz istemci.');
    if (typeof input !== 'string' || input.length > 4096) throw new Error('Geçersiz bağlantı.');
    const url = new URL(input);
    if (url.protocol !== 'https:' || !url.hostname || url.username || url.password)
      throw new Error('Yalnız HTTPS bağlantıları açılabilir.');
    await shell.openExternal(url.href);
  });
}

async function createWindow() {
  window = new BrowserWindow({
    title: 'Firik Agent', width: 1240, height: 850, minWidth: 940, minHeight: 640,
    backgroundColor: '#0d1117', show: false,
    icon: app.isPackaged ? join(process.resourcesPath, 'icon.png') : join(root, 'firik_agent/ui/assets/icon.png'),
    webPreferences: { preload: join(__dirname, '../preload/index.cjs'),
      nodeIntegration: false, contextIsolation: true, sandbox: true, webviewTag: false },
  });
  window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  window.webContents.on('will-navigate', (event, url) => { if (!trustedURL(url)) event.preventDefault(); });
  window.webContents.on('will-attach-webview', event => event.preventDefault());
  window.once('ready-to-show', () => window?.show());
  window.on('close', event => {
    if (closing) return;
    event.preventDefault();
    closing = true;
    void backend.shutdown().finally(() => { window?.destroy(); app.quit(); });
  });
  await window.loadURL(devURL ?? 'firik://app/index.html');
}

if (!app.requestSingleInstanceLock()) app.quit();
else {
  app.on('second-instance', () => { window?.restore(); window?.focus(); });
  app.on('before-quit', event => {
    if (!closing && backend) { event.preventDefault(); window?.close(); }
  });
  void app.whenReady().then(async () => {
    protocol.handle('firik', request => {
      const url = new URL(request.url);
      const candidate = resolve(rendererRoot, `.${decodeURIComponent(url.pathname)}`);
      if (url.hostname !== 'app' || !candidate.startsWith(rendererRoot + sep))
        return new Response('Not found', { status: 404 });
      return net.fetch(pathToFileURL(candidate).href);
    });
    session.defaultSession.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
    session.defaultSession.setPermissionCheckHandler(() => false);
    session.defaultSession.webRequest.onHeadersReceived((details, callback) => {
      const csp = `default-src 'self'; script-src 'self'${devURL ? " 'unsafe-inline'" : ''}; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'${devURL ? ' ws://127.0.0.1:5173' : ''}; object-src 'none'; frame-src 'none'; base-uri 'none'; form-action 'none'`;
      callback({ responseHeaders: { ...details.responseHeaders, 'Content-Security-Policy': [csp] } });
    });
    Menu.setApplicationMenu(Menu.buildFromTemplate([
      ...(process.platform === 'darwin' ? [{ role: 'appMenu' as const }] : []),
      { role: 'editMenu' }, { role: 'viewMenu' }, { role: 'windowMenu' },
    ]));
    registerIPC();
    startBackend();
    await createWindow();
  }).catch(() => { closing = true; void backend?.shutdown().finally(() => app.quit()); });
}
