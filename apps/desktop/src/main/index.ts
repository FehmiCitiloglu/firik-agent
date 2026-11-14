import {
  app,
  BrowserWindow,
  ipcMain,
  Menu,
  MessageChannelMain,
  net,
  protocol,
  session,
  shell,
  utilityProcess,
} from 'electron';
import { existsSync } from 'node:fs';
import { join, resolve, sep } from 'node:path';
import { pathToFileURL } from 'node:url';
import { parseParams, parsePreviewParams, PROTOCOL_VERSION, type Method } from '@firik/contracts';
import type { BrowserViewLayoutParams } from '@firik/contracts/desktop-api';
import { BrowserHost, type BrowserEngine } from './browser-host.js';
import { ElectronBrowserExecutor } from './electron-browser-executor.js';
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
let browserHost: BrowserHost;
let ready: Promise<unknown>;
let closing = false;

function trustedURL(value: string): boolean {
  try {
    const url = new URL(value);
    return devURL ? url.origin === devURL : url.protocol === 'firik:' && url.hostname === 'app';
  } catch { return false; }
}

function assertTrustedSender(event: Electron.IpcMainInvokeEvent): void {
  if (!window || event.sender !== window.webContents || event.senderFrame !== window.webContents.mainFrame
      || !trustedURL(event.senderFrame.url)) {
    throw new Error('Geçersiz masaüstü istemcisi.');
  }
}

function clampLayoutInt(value: unknown, min: number, max: number): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    throw new Error('Geçersiz tarayıcı görünüm boyutu.');
  }
  return Math.max(min, Math.min(max, Math.round(value)));
}

function parseViewLayoutParams(input: unknown): BrowserViewLayoutParams {
  if (!input || typeof input !== 'object' || Array.isArray(input)) {
    throw new Error('Geçersiz görünüm yerleşimi.');
  }
  const obj = input as Record<string, unknown>;
  const convId = obj.conversation_id;
  if (convId !== null && (typeof convId !== 'string' || !convId.trim() || convId.length > 128)) {
    throw new Error('Geçersiz sohbet kimliği.');
  }
  if (typeof obj.visible !== 'boolean' || typeof obj.obscured !== 'boolean' || typeof obj.fit_viewport !== 'boolean') {
    throw new Error('Geçersiz görünüm bayrakları.');
  }
  return {
    conversation_id: convId,
    visible: obj.visible,
    obscured: obj.obscured,
    x: clampLayoutInt(obj.x, -4096, 8192),
    y: clampLayoutInt(obj.y, -4096, 8192),
    width: clampLayoutInt(obj.width, 0, 8192),
    height: clampLayoutInt(obj.height, 0, 8192),
    fit_viewport: obj.fit_viewport,
  };
}

function startBackend() {
  const executable = process.platform === 'win32' ? 'firik-backend.exe' : 'firik-backend';
  const localPython = join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
  const command = app.isPackaged ? join(process.resourcesPath, 'backend', executable)
    : process.env.FIRIK_DESKTOP_PYTHON || (existsSync(localPython) ? localPython : process.platform === 'win32' ? 'python' : 'python3');
  const args = app.isPackaged ? [] : ['-m', 'firik_agent.desktop_rpc'];
  if (process.env.FIRIK_DESKTOP_CONFIG) args.push('--config', process.env.FIRIK_DESKTOP_CONFIG);
  const workerPath = resolve(__dirname, '../browser-worker/index.cjs').replace('app.asar', 'app.asar.unpacked');
  const systemLocale = app.getPreferredSystemLanguages?.()[0] || app.getLocale?.() || undefined;
  if (systemLocale && !process.env.FIRIK_BROWSER_LOCALE) {
    process.env.FIRIK_BROWSER_LOCALE = systemLocale;
  }
  const sendBrowserPointer = (pointer: unknown): void => {
    if (!window || window.isDestroyed()) return;
    window.webContents.send('firik:browser.pointer', pointer);
  };
  const embeddedExecutor = new ElectronBrowserExecutor({
    getParentWindow: () => window,
    onPointer: pointer => {
      if (browserHost?.getEngine() === 'embedded') {
        sendBrowserPointer(pointer);
      }
    },
  });
  const initialEngine: BrowserEngine = process.env.FIRIK_BROWSER_ENGINE === 'playwright' ? 'playwright' : 'embedded';
  browserHost = new BrowserHost({
    engine: initialEngine,
    embeddedExecutor,
    workerPath,
    browsersPath: app.isPackaged ? join(process.resourcesPath, 'browser') : join(root, 'build/hybrid-browser'),
    defaultLocale: systemLocale,
    utilityProcess,
    MessageChannelMain,
    onPointer: pointer => {
      if (browserHost.getEngine() === 'playwright') {
        sendBrowserPointer(pointer);
      }
    },
  });
  backend = new BackendClient(command, args, app.isPackaged ? app.getPath('userData') : root,
    {
      ...process.env,
      PYTHONUNBUFFERED: '1',
      ...(systemLocale && !process.env.FIRIK_BROWSER_LOCALE ? { FIRIK_BROWSER_LOCALE: systemLocale } : {}),
    });
  backend.setHostHandler(async (method, params) => {
    if (method === 'browser.configureExecutor') {
      return browserHost.configure((params as { config: Parameters<BrowserHost['configure']>[0] }).config);
    }
    if (method === 'browser.prepare') {
      const p = params as Parameters<BrowserHost['handlePrepare']>[0];
      return { ok: true, payload: await browserHost.handlePrepare(p) };
    }
    if (method === 'browser.execute') {
      const p = params as Parameters<BrowserHost['handleExecute']>[0];
      return { ok: true, payload: await browserHost.handleExecute(p) };
    }
    if (method === 'browser.revoke') {
      const p = params as Parameters<BrowserHost['handleRevoke']>[0];
      return { ok: true, payload: await browserHost.handleRevoke(p) };
    }
    throw new Error('Desteklenmeyen host yöntemi.');
  });
  backend.on('event', event => {
    if (window && !window.isDestroyed()) window.webContents.send('firik:event', event);
  });
  backend.on('disconnected', (message: string) => {
    void browserHost.shutdown();
    if (window && !window.isDestroyed()) window.webContents.send('firik:disconnected', message);
  });
  ready = backend.request('system.hello', { protocol_version: PROTOCOL_VERSION });
  ready.catch(() => {}); // The UI's snapshot request presents the startup error.
}

function registerIPC() {
  const methods: Method[] = [
    'state.snapshot',
    'chat.create',
    'chat.get',
    'chat.start',
    'run.cancel',
    'settings.get',
    'settings.update',
    'credentials.set',
    'models.list',
    'mcp.set',
    'approval.resolve',
    'browser.configure',
    'browser.userCommand',
    'browser.userInput',
  ];
  for (const method of methods) {
    ipcMain.handle(`firik:${method}`, async (event, input: unknown) => {
      if (closing) throw new Error('Uygulama kapatılıyor.');
      assertTrustedSender(event);
      const params = parseParams(method, input);
      await ready;
      return backend.request(method, params);
    });
  }
  ipcMain.handle('firik:browser.preview', async (event, input: unknown) => {
    if (closing) return null;
    assertTrustedSender(event);
    const params = parsePreviewParams(input);
    await ready;
    return browserHost.captureUserPreview(params.conversation_id);
  });
  ipcMain.handle('firik:browser.setViewLayout', async (event, input: unknown) => {
    if (closing) return;
    assertTrustedSender(event);
    const params = parseViewLayoutParams(input);
    browserHost?.setEmbeddedLayout(params);
  });
  ipcMain.handle('firik:browser.getEngine', async event => {
    assertTrustedSender(event);
    return browserHost?.getEngine() ?? 'embedded';
  });
  ipcMain.handle('firik:browser.setEngine', async (event, input: unknown) => {
    if (closing) throw new Error('Uygulama kapatılıyor.');
    assertTrustedSender(event);
    if (input !== 'embedded' && input !== 'playwright') {
      throw new Error('Geçersiz tarayıcı motoru.');
    }
    await ready;
    const applied = await browserHost.setEngine(input);
    if (window && !window.isDestroyed()) {
      window.webContents.send('firik:browser.pointer', {
        conversation_id: '',
        visible: false,
        x: 0,
        y: 0,
        viewport_width: 0,
        viewport_height: 0,
        action: 'idle',
        pressing: false,
      });
    }
    return applied;
  });
  ipcMain.handle('firik:link.open', async (event, input: unknown) => {
    assertTrustedSender(event);
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
    void backend.shutdown().finally(() => browserHost?.shutdown()).finally(() => {
      window?.destroy();
      app.quit();
    });
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
  }).catch(() => {
    closing = true;
    void Promise.allSettled([backend?.shutdown(), browserHost?.shutdown()]).finally(() => app.quit());
  });
}
