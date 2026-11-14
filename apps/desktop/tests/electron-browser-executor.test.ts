import { EventEmitter } from 'node:events';
import { describe, expect, it } from 'vitest';
import {
  ElectronBrowserExecutor,
  type ElectronParentWindowLike,
  type ElectronSessionLike,
  type ElectronWebContentsLike,
  type ElectronWebContentsViewLike,
} from '../src/main/electron-browser-executor.js';
import { BrowserHost } from '../src/main/browser-host.js';
import {
  validateGrant,
  validateIdentity,
  type BrowserGrant,
  type BrowserIdentity,
} from '../src/browser-worker/models.js';
import { originOf } from '../src/browser-worker/security.js';
import { BrowserSecurityPolicy } from '../src/browser-worker/security.js';
import { WorkerRequestDispatcher } from '../src/browser-worker/index.js';
import type { CdpTransport } from '../src/main/cdp-driver.js';

class MinimalCdpStub extends EventEmitter implements CdpTransport {
  private attached = false;
  public passwordTarget = false;
  public hostPreviewPng = 'HOST_PNG';
  public agentPreviewPng = 'AGENT_PNG';

  constructor(private readonly getUrl: () => string, private readonly getTitle: () => string) {
    super();
  }

  isAttached(): boolean {
    return this.attached;
  }

  attach(): void {
    this.attached = true;
  }

  detach(): void {
    this.attached = false;
  }

  async sendCommand<T = unknown>(method: string, params: Record<string, unknown> = {}): Promise<T> {
    switch (method) {
      case 'Page.getFrameTree':
        return { frameTree: { frame: { id: 'f1', url: this.getUrl(), name: 'main' }, childFrames: [] } } as T;
      case 'Page.createIsolatedWorld':
        return { executionContextId: 1 } as T;
      case 'Accessibility.getFullAXTree':
        return {
          nodes: [
            { nodeId: 'r', role: { value: 'RootWebArea' }, name: { value: this.getTitle() }, childIds: ['b1'] },
            {
              nodeId: 'b1',
              role: { value: this.passwordTarget ? 'textbox' : 'button' },
              name: { value: this.passwordTarget ? 'Password' : 'Continue' },
              backendDOMNodeId: 101,
            },
          ],
        } as T;
      case 'DOM.resolveNode':
        return { object: { objectId: 'obj-101' } } as T;
      case 'DOM.describeNode':
        return { node: { backendNodeId: 101, nodeName: this.passwordTarget ? 'INPUT' : 'BUTTON' } } as T;
      case 'DOM.getBoxModel':
        return { model: { content: [10, 10, 50, 10, 50, 30, 10, 30] } } as T;
      case 'Runtime.callFunctionOn': {
        const decl = String(params.functionDeclaration ?? '');
        if (decl.includes('formFieldCount')) {
          return {
            result: {
              value: {
                tag: this.passwordTarget ? 'input' : 'button',
                role: this.passwordTarget ? 'textbox' : 'button',
                type: this.passwordTarget ? 'password' : 'button',
                name: this.passwordTarget ? 'Password' : 'Continue',
                label: this.passwordTarget ? 'Password' : 'Continue',
                html: this.passwordTarget ? '<input type="password">' : '<button>Continue</button>',
                value: '',
                valueLength: 0,
                formFieldCount: 1,
                fields: [],
                sensitive: this.passwordTarget,
                visible: true,
                disabled: false,
              },
            },
          } as T;
        }
        return { result: { value: { ready: true, visible: true, enabled: true } } } as T;
      }
      case 'Runtime.evaluate': {
        const expr = String(params.expression ?? '');
        if (expr.includes('document.querySelectorAll')) {
          return { result: { value: `${this.getUrl()}|${this.getTitle()}|${this.passwordTarget}` } } as T;
        }
        return { result: { value: true } } as T;
      }
      case 'Page.captureScreenshot':
        return { data: Buffer.from(this.agentPreviewPng).toString('base64') } as T;
      default:
        return {} as T;
    }
  }
}

function createFakeElectronEnv() {
  const partitions = new Map<string, FakeSession>();
  const views: FakeWebContentsView[] = [];
  let nextWcId = 1;

  class FakeSession extends EventEmitter implements ElectronSessionLike {
    public beforeRequest: ((details: any, cb: (r: { cancel?: boolean }) => void) => void) | null = null;
    public beforeSendHeaders: ((details: any, cb: (r: { requestHeaders?: Record<string, string> }) => void) => void) | null = null;
    public headersReceived: ((details: any, cb: (r: { cancel?: boolean }) => void) => void) | null = null;
    public completedListener: ((details: any) => void) | null = null;
    public errorListener: ((details: any) => void) | null = null;
    public permCheckHandler: (() => boolean) | null = null;
    public permRequestHandler: ((_wc: unknown, _perm: string, cb: (ok: boolean) => void) => void) | null = null;
    public clearedCount = 0;

    constructor(public readonly name: string) {
      super();
    }

    webRequest = {
      onBeforeRequest: (fn: ((details: any, cb: (r: { cancel?: boolean }) => void) => void) | null) => {
        this.beforeRequest = fn;
      },
      onBeforeSendHeaders: (fn: ((details: any, cb: (r: { requestHeaders?: Record<string, string> }) => void) => void) | null) => {
        this.beforeSendHeaders = fn;
      },
      onHeadersReceived: (fn: ((details: any, cb: (r: { cancel?: boolean }) => void) => void) | null) => {
        this.headersReceived = fn;
      },
      onCompleted: (fn: ((details: any) => void) | null) => {
        this.completedListener = fn;
      },
      onErrorOccurred: (fn: ((details: any) => void) | null) => {
        this.errorListener = fn;
      },
    };

    setPermissionCheckHandler(fn: (() => boolean) | null): void {
      this.permCheckHandler = fn;
    }

    setPermissionRequestHandler(fn: ((_wc: unknown, _perm: string, cb: (ok: boolean) => void) => void) | null): void {
      this.permRequestHandler = fn;
    }

    getUserAgent(): string {
      return 'Mozilla/5.0 Chrome/135.0.0.0 firik-desktop/0.2.52 Electron/45.2.3 Safari/537.36';
    }

    async clearStorageData(): Promise<void> {
      this.clearedCount += 1;
    }
  }

  class FakeWebContents extends EventEmitter implements ElectronWebContentsLike {
    public readonly id = nextWcId++;
    public url = 'about:blank';
    public title = '';
    public ua = 'Mozilla/5.0 Chrome/135.0.0.0 firik-desktop/0.2.52 Electron/45.2.3 Safari/537.36';
    public destroyed = false;
    public windowOpenHandler: ((details: { url: string }) => { action: 'deny' | 'allow' }) | null = null;
    public readonly cdp: MinimalCdpStub;
    private history: string[] = [];
    private historyIndex = -1;

    constructor(private readonly session: FakeSession) {
      super();
      this.cdp = new MinimalCdpStub(() => this.url, () => this.title);
    }

    get debugger(): CdpTransport {
      return this.cdp;
    }

    navigationHistory = {
      canGoBack: () => this.historyIndex > 0,
      canGoForward: () => this.historyIndex >= 0 && this.historyIndex < this.history.length - 1,
      goBack: () => {
        if (this.historyIndex > 0) {
          this.historyIndex -= 1;
          void this.loadURL(this.history[this.historyIndex]);
        }
      },
      goForward: () => {
        if (this.historyIndex < this.history.length - 1) {
          this.historyIndex += 1;
          void this.loadURL(this.history[this.historyIndex]);
        }
      },
    };

    getURL(): string {
      return this.url;
    }

    getTitle(): string {
      return this.title;
    }

    getUserAgent(): string {
      return this.ua;
    }

    setUserAgent(ua: string): void {
      this.ua = ua;
    }

    setWindowOpenHandler(fn: (details: { url: string }) => { action: 'deny' | 'allow' }): void {
      this.windowOpenHandler = fn;
    }

    isDestroyed(): boolean {
      return this.destroyed;
    }

    stop(): void {}

    focus(): void {}

    setZoomFactor(_factor: number): void {}

    close(): void {
      this.destroy();
    }

    destroy(): void {
      if (!this.destroyed) {
        this.destroyed = true;
        this.emit('destroyed');
      }
    }

    sendInputEvent(_inputEvent: Record<string, unknown>): void {}

    async capturePage(): Promise<{ toPNG(): Buffer }> {
      return { toPNG: () => Buffer.from(this.cdp.hostPreviewPng) };
    }

    async loadURL(target: string): Promise<void> {
      if (this.session.beforeRequest) {
        const res = await new Promise<{ cancel?: boolean }>(resolve => {
          this.session.beforeRequest!(
            { id: 1, url: target, resourceType: 'mainFrame', webContentsId: this.id },
            resolve,
          );
        });
        if (res.cancel) {
          this.emit('did-fail-load', {}, -20, 'ERR_BLOCKED_BY_CLIENT', target, true);
          throw new Error('ERR_BLOCKED_BY_CLIENT');
        }
      }
      this.url = target;
      this.title = `Title:${target}`;
      if (this.history[this.historyIndex] !== target) {
        this.history = [...this.history.slice(0, this.historyIndex + 1), target];
        this.historyIndex = this.history.length - 1;
      }
      this.emit('did-commit-navigation', {}, target);
      this.emit('did-navigate', {}, target);
      this.emit('dom-ready', {});
      this.emit('did-finish-load', {});
    }

    reload(): void {
      void this.loadURL(this.url);
    }
  }

  class FakeWebContentsView implements ElectronWebContentsViewLike {
    public readonly webContents: FakeWebContents;
    public readonly initOptions: unknown;
    public bounds = { x: 0, y: 0, width: 0, height: 0 };
    public visible = false;

    constructor(options: any = {}) {
      this.initOptions = options;
      const provided = options?.webPreferences?.session as FakeSession | undefined;
      let ses = provided;
      if (!ses) {
        const partition = options?.webPreferences?.partition ?? 'default';
        ses = partitions.get(partition);
        if (!ses) {
          ses = new FakeSession(partition);
          partitions.set(partition, ses);
        }
      }
      this.webContents = new FakeWebContents(ses);
      views.push(this);
    }

    setBounds(bounds: { x: number; y: number; width: number; height: number }): void {
      this.bounds = bounds;
    }

    setVisible(visible: boolean): void {
      this.visible = visible;
    }
  }

  const children: FakeWebContentsView[] = [];
  const parentWindow: ElectronParentWindowLike = {
    isDestroyed: () => false,
    contentView: {
      children,
      addChildView(view: unknown) {
        const v = view as FakeWebContentsView;
        if (!children.includes(v)) children.push(v);
      },
      removeChildView(view: unknown) {
        const idx = children.indexOf(view as FakeWebContentsView);
        if (idx >= 0) children.splice(idx, 1);
      },
    },
  };

  return {
    partitions,
    views,
    parentWindow,
    WebContentsView: FakeWebContentsView,
    fromPartition: (partition: string) => {
      let ses = partitions.get(partition);
      if (!ses) {
        ses = new FakeSession(partition);
        partitions.set(partition, ses);
      }
      return ses;
    },
  };
}

function testPolicy(dnsCalls?: string[]) {
  return new BrowserSecurityPolicy(async host => {
    dnsCalls?.push(host);
    if (host === 'rebind.example.test') {
      return ['127.0.0.1'];
    }
    return ['93.184.216.34'];
  });
}

function makeId(conversationId: string): BrowserIdentity {
  return validateIdentity({ conversation_id: conversationId, actor_id: 'parent' });
}

function makeGrant(overrides: Partial<BrowserGrant> = {}): BrowserGrant {
  return validateGrant(
    {
      enabled: true,
      allow_screenshots: true,
      origins: ['https://example.test'],
      allow_private_origins: [],
      ...overrides,
    },
    originOf,
  );
}

describe('ElectronBrowserExecutor', () => {
  it('isolates partitions per identity, locks down permissions/downloads/popups, and cleans UA', async () => {
    const env = createFakeElectronEnv();
    const executor = new ElectronBrowserExecutor({
      config: { enabled: true },
      policy: testPolicy(),
      WebContentsView: env.WebContentsView,
      fromPartition: env.fromPartition,
      getParentWindow: () => env.parentWindow,
    });

    const idA = makeId('conv-a');
    const idB = makeId('conv-b');
    const grant = makeGrant();

    await executor.hostCommand(idA, grant, 'start', {});
    await executor.hostCommand(idA, grant, 'navigate', { url: 'https://example.test/a' });
    await executor.hostCommand(idB, grant, 'start', {});
    await executor.hostCommand(idB, grant, 'navigate', { url: 'https://example.test/b' });

    expect(env.partitions.size).toBe(2);
    const [partA, partB] = [...env.partitions.keys()];
    expect(partA).not.toBe(partB);
    expect(partA.startsWith('persist:')).toBe(false);
    expect(partB.startsWith('persist:')).toBe(false);

    const viewA = env.views[0];
    expect(viewA.webContents.getUserAgent()).not.toContain('Electron/');
    expect(viewA.webContents.getUserAgent()).not.toContain('firik-desktop/');
    expect(viewA.webContents.windowOpenHandler?.({ url: 'https://example.test/popup' })).toEqual({ action: 'deny' });

    const sesA = env.partitions.get(partA)!;
    expect(sesA.permCheckHandler?.()).toBe(false);
    let permAllowed = true;
    sesA.permRequestHandler?.(viewA.webContents, 'media', granted => {
      permAllowed = granted;
    });
    expect(permAllowed).toBe(false);

    let prevented = false;
    let canceled = false;
    sesA.emit('will-download', { preventDefault: () => { prevented = true; } }, { cancel: () => { canceled = true; } });
    expect(prevented).toBe(true);
    expect(canceled).toBe(true);
  });

  it('enforces webRequest URL/DNS filtering, redirect hop/origin blocking, and Accept-Language header', async () => {
    const env = createFakeElectronEnv();
    const dnsCalls: string[] = [];
    const policy = new BrowserSecurityPolicy(async host => {
      dnsCalls.push(host);
      if (host === 'rebind.example.test') {
        return ['127.0.0.1'];
      }
      return ['93.184.216.34'];
    });
    const executor = new ElectronBrowserExecutor({
      config: { enabled: true, locale: 'tr-TR' },
      policy,
      WebContentsView: env.WebContentsView,
      fromPartition: env.fromPartition,
      getParentWindow: () => env.parentWindow,
    });
    const id = makeId('conv-sec');
    const grant = makeGrant({
      origins: ['https://example.test', 'https://rebind.example.test'],
    });

    await executor.hostCommand(id, grant, 'start', {});
    await executor.hostCommand(id, grant, 'navigate', { url: 'https://example.test/start' });
    const ses = [...env.partitions.values()][0];
    const wcId = env.views[0].webContents.id;

    // 1. Disallowed protocols / private IP / DNS rebind must be cancelled in onBeforeRequest
    const checkReq = (url: string, resourceType = 'xhr') =>
      new Promise<{ cancel?: boolean }>(resolve => {
        ses.beforeRequest!({ id: Math.random(), url, resourceType, webContentsId: wcId }, resolve);
      });

    expect((await checkReq('ws://example.test/socket', 'webSocket')).cancel).toBe(true);
    expect((await checkReq('file:///etc/passwd', 'mainFrame')).cancel).toBe(true);
    expect((await checkReq('https://evil.test/steal', 'xhr')).cancel).toBe(true);
    expect((await checkReq('https://rebind.example.test/api', 'xhr')).cancel).toBe(true);
    expect(dnsCalls).toContain('rebind.example.test');
    expect((await checkReq('https://example.test/ok.js', 'script')).cancel).toBe(false);

    // 2. Accept-Language header should reflect tr-TR
    const hdrResult = await new Promise<{ requestHeaders?: Record<string, string> }>(resolve => {
      ses.beforeSendHeaders!(
        { webContentsId: wcId, requestHeaders: { 'User-Agent': 'test' } },
        resolve,
      );
    });
    expect(hdrResult.requestHeaders?.['Accept-Language']).toContain('tr-TR');

    // 3. 3xx redirect to denied or private target must be cancelled before Chromium follows
    const checkHeader = (idNum: number, url: string, statusCode: number, location: string) =>
      new Promise<{ cancel?: boolean }>(resolve => {
        ses.headersReceived!(
          {
            id: idNum,
            url,
            statusCode,
            responseHeaders: { Location: [location] },
            resourceType: 'mainFrame',
            webContentsId: wcId,
          },
          resolve,
        );
      });

    expect((await checkHeader(99, 'https://example.test/go', 302, 'http://127.0.0.1/admin')).cancel).toBe(true);
    expect((await checkHeader(100, 'https://example.test/go', 302, 'https://evil.test/login')).cancel).toBe(true);
    expect((await checkHeader(101, 'https://example.test/go', 302, 'https://example.test/next')).cancel).toBeFalsy();
    // >10 redirect hops with same Chromium request id must be blocked
    for (let hop = 1; hop <= 10; hop++) {
      expect((await checkHeader(102, `https://example.test/hop/${hop}`, 302, `https://example.test/hop/${hop + 1}`)).cancel).toBeFalsy();
    }
    expect((await checkHeader(102, 'https://example.test/hop/11', 302, 'https://example.test/hop/12')).cancel).toBe(true);
  });

  it('synchronizes WebContentsView bounds/visibility and hides inactive/obscured/unpermitted views', async () => {
    const env = createFakeElectronEnv();
    const executor = new ElectronBrowserExecutor({
      config: { enabled: true },
      policy: testPolicy(),
      WebContentsView: env.WebContentsView,
      fromPartition: env.fromPartition,
      getParentWindow: () => env.parentWindow,
    });
    const id = makeId('conv-ui');
    const grant = makeGrant();

    await executor.hostCommand(id, grant, 'start', {});
    await executor.hostCommand(id, grant, 'navigate', { url: 'https://example.test/1' });
    await executor.hostCommand(id, grant, 'new_tab', { url: 'https://example.test/2' });
    expect(env.views.length).toBe(2);

    executor.setLayout({
      conversation_id: 'conv-ui',
      visible: true,
      obscured: false,
      x: 720,
      y: 120,
      width: 500,
      height: 680,
      fit_viewport: true,
    });

    expect(env.views[0].visible).toBe(false);
    expect(env.views[1].visible).toBe(true);
    expect(env.views[1].bounds).toEqual({ x: 720, y: 120, width: 500, height: 680 });
    expect(env.parentWindow.contentView.children).toContain(env.views[1]);

    // Opening modal hides active view immediately
    executor.setLayout({
      conversation_id: 'conv-ui',
      visible: true,
      obscured: true,
      x: 720,
      y: 120,
      width: 500,
      height: 680,
      fit_viewport: true,
    });
    expect(env.views[1].visible).toBe(false);

    // Switching conversation hides conv-ui view
    executor.setLayout({
      conversation_id: 'other-conv',
      visible: true,
      obscured: false,
      x: 720,
      y: 120,
      width: 500,
      height: 680,
      fit_viewport: true,
    });
    expect(env.views[1].visible).toBe(false);

    // Teardown detaches views, clears listeners & session data, and destroys webContents
    await executor.closeOwner(id);
    expect(env.parentWindow.contentView.children.length).toBe(0);
    expect(env.views[0].webContents.isDestroyed()).toBe(true);
    expect(env.views[1].webContents.isDestroyed()).toBe(true);
    const ses = [...env.partitions.values()][0];
    expect(ses.beforeRequest).toBeNull();
    expect(ses.clearedCount).toBeGreaterThanOrEqual(1);
  });

  it('enforces password handoff, manual_control pause/resume, and close_all cleanup', async () => {
    const env = createFakeElectronEnv();
    const executor = new ElectronBrowserExecutor({
      config: { enabled: true },
      policy: testPolicy(),
      WebContentsView: env.WebContentsView,
      fromPartition: env.fromPartition,
      getParentWindow: () => env.parentWindow,
    });
    const id = makeId('conv-gov');
    const grant = makeGrant();

    await executor.hostCommand(id, grant, 'start', {});
    await executor.hostCommand(id, grant, 'navigate', { url: 'https://example.test/login' });
    const view = env.views[0];
    view.webContents.cdp.passwordTarget = true;

    const snap = await executor.dispatch(id, grant, 'snapshot', {});
    const targetRef = /textbox "Password" \[ref=(e\d+)\]/.exec(String(snap.content))?.[1];
    expect(targetRef).toBeTruthy();

    const status = await executor.dispatch(id, grant, 'status', {});
    const rev = Number(status.revision);

    await expect(
      executor.prepare(id, grant, 'fill', {
        target: { ref: targetRef },
        value: 'hunter2',
        revision: rev,
      }),
    ).rejects.toMatchObject({ code: 'authentication_handoff_required' });

    await executor.hostCommand(id, grant, 'request_handoff', {});
    await expect(
      executor.dispatch(id, grant, 'snapshot', {}),
    ).rejects.toMatchObject({ code: 'manual_control' });

    const resumed = await executor.resume(id, grant);
    expect(resumed.url).toBe('https://example.test/login');
    const afterResume = await executor.dispatch(id, grant, 'status', {});
    expect(afterResume.state).toBe('ready');

    await executor.closeAll();
    expect(env.views[0].webContents.isDestroyed()).toBe(true);
  });

  it('supports explicit BrowserHost engine switching with zero silent fallback', async () => {
    // 1. Embedded engine without embeddedExecutor fails explicitly (never falls back to Playwright)
    const missingEmbeddedHost = new BrowserHost({
      engine: 'embedded',
      inProcess: true,
    });
    await expect(
      missingEmbeddedHost.handleExecute({
        execution_id: 'exec-1',
        identity: makeId('conv'),
        grant: makeGrant(),
        operation: 'status',
        arguments: {},
      }),
    ).rejects.toMatchObject({ code: 'browser_worker_unavailable' });

    // 2. Embedded host must not spawn utilityProcess unless explicitly switched to playwright
    let forkCount = 0;
    const fakeUtility = {
      fork() {
        forkCount += 1;
        return new EventEmitter();
      },
    };
    const env = createFakeElectronEnv();
    const executor = new ElectronBrowserExecutor({
      config: { enabled: true },
      policy: testPolicy(),
      WebContentsView: env.WebContentsView,
      fromPartition: env.fromPartition,
      getParentWindow: () => env.parentWindow,
    });
    const inProcDispatcher = new WorkerRequestDispatcher();
    const host = new BrowserHost({
      engine: 'embedded',
      embeddedExecutor: executor,
      dispatcher: inProcDispatcher,
      utilityProcess: fakeUtility,
    });

    expect(host.getEngine()).toBe('embedded');
    await host.handleExecute({
      execution_id: 'exec-1b',
      identity: makeId('conv-switch'),
      grant: makeGrant(),
      operation: 'start',
      arguments: {},
      host_control: true,
    });
    await host.handleExecute({
      execution_id: 'exec-2',
      identity: makeId('conv-switch'),
      grant: makeGrant(),
      operation: 'navigate',
      arguments: { url: 'https://example.test/page' },
      host_control: true,
    });
    expect(env.views.length).toBe(1);
    expect(forkCount).toBe(0);

    // Switching explicitly to playwright tears down embedded sessions first
    await host.setEngine('playwright');
    expect(host.getEngine()).toBe('playwright');
    expect(env.views[0].webContents.isDestroyed()).toBe(true);

    // Switching explicitly back to embedded works
    await host.setEngine('embedded');
    expect(host.getEngine()).toBe('embedded');
    await host.shutdown();
  });

  it('shows and auto-hides the ImageView cursor overlay across clicks, obscuring, handoff, and teardown', async () => {
    const env = createFakeElectronEnv();
    const createdBitmaps: Array<{ width: number; height: number; scaleFactor: number }> = [];
    const createdOverlays: Array<{
      visible: boolean;
      bounds: { x: number; y: number; width: number; height: number };
      bgColor: string | null;
      imageCount: number;
    }> = [];

    class FakeImageView {
      visible = false;
      bounds = { x: 0, y: 0, width: 0, height: 0 };
      bgColor: string | null = null;
      imageCount = 0;
      constructor() {
        createdOverlays.push(this);
      }
      setImage(_img: unknown) {
        this.imageCount += 1;
      }
      setBackgroundColor(color: string) {
        this.bgColor = color;
      }
      setBounds(bounds: { x: number; y: number; width: number; height: number }) {
        this.bounds = bounds;
      }
      setVisible(visible: boolean) {
        this.visible = visible;
      }
    }

    const pointerEvents: Array<{ visible: boolean; action: string; pressing: boolean; x: number; y: number }> = [];
    const executor = new ElectronBrowserExecutor({
      config: { enabled: true },
      policy: testPolicy(),
      WebContentsView: env.WebContentsView,
      ImageView: FakeImageView,
      createImageFromBitmap: (_buf, opts) => {
        createdBitmaps.push(opts);
        return { kind: 'cursor-sprite', ...opts };
      },
      fromPartition: env.fromPartition,
      getParentWindow: () => env.parentWindow,
      pointerStepDelayMs: 0,
      cursorHideMs: 40,
      onPointer: ev => pointerEvents.push(ev),
    });

    const id = makeId('conv-cursor');
    const grant = makeGrant();
    await executor.hostCommand(id, grant, 'start', {});
    await executor.hostCommand(id, grant, 'navigate', { url: 'https://example.test/start' });
    executor.setLayout({
      conversation_id: 'conv-cursor',
      visible: true,
      obscured: false,
      x: 500,
      y: 100,
      width: 640,
      height: 480,
      fit_viewport: true,
    });

    const runApproved = async (operation: 'click' | 'hover') => {
      const snap = await executor.dispatch(id, grant, 'snapshot', {});
      const status = await executor.dispatch(id, grant, 'status', {});
      const ref = /button "Continue" \[ref=(e\d+)\]/.exec(String(snap.content))![1];
      const args = { target: { ref }, revision: Number(status.revision) };
      const prep = await executor.prepare(id, grant, operation, args);
      return executor.dispatch(id, grant, operation, args, {
        approvedFingerprint: String(prep.fingerprint),
      });
    };

    await runApproved('click');

    expect(createdBitmaps.length).toBeGreaterThanOrEqual(3);
    expect(createdBitmaps[0]).toEqual({ width: 72, height: 72, scaleFactor: 2 });
    expect(createdOverlays.length).toBe(1);
    const overlay = createdOverlays[0];
    expect(overlay.bgColor).toBe('#00000000');
    expect(overlay.visible).toBe(true);
    expect(env.parentWindow.contentView.children).toContain(overlay);
    expect(overlay.bounds.x).toBeGreaterThanOrEqual(500 - 12);
    expect(overlay.bounds.y).toBeGreaterThanOrEqual(100 - 12);
    expect(pointerEvents.some(e => e.visible && e.action === 'click' && e.pressing)).toBe(true);

    // Obscuring panel hides and detaches the cursor overlay cleanly
    executor.setLayout({
      conversation_id: 'conv-cursor',
      visible: true,
      obscured: true,
      x: 500,
      y: 100,
      width: 640,
      height: 480,
      fit_viewport: true,
    });
    expect(overlay.visible).toBe(false);
    expect(env.parentWindow.contentView.children).not.toContain(overlay);

    // Restore layout, run hover, then verify manual handoff hides cursor immediately
    executor.setLayout({
      conversation_id: 'conv-cursor',
      visible: true,
      obscured: false,
      x: 500,
      y: 100,
      width: 640,
      height: 480,
      fit_viewport: true,
    });
    await runApproved('hover');
    expect(overlay.visible).toBe(true);

    pointerEvents.length = 0;
    await executor.hostCommand(id, grant, 'request_handoff', {});
    expect(overlay.visible).toBe(false);
    expect(pointerEvents.some(e => !e.visible)).toBe(true);

    // Resume and verify idle auto-hide clears the overlay view
    await executor.resume(id, grant);
    await runApproved('hover');
    expect(overlay.visible).toBe(true);
    await new Promise(resolve => setTimeout(resolve, 160));
    expect(overlay.visible).toBe(false);

    await executor.closeOwner(id);
    expect(env.parentWindow.contentView.children.length).toBe(0);
    await executor.closeAll();
  });
});
