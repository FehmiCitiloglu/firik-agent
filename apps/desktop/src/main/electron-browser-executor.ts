import { createHash } from 'node:crypto';
import type { BrowserPointerState } from '@firik/contracts/desktop-api';
import {
  AsyncMutex,
  BrowserError,
  DiagnosticBuffer,
  defaultBrowserConfig,
  grantsEqual,
  identityKey,
  randomId,
  resolveBrowserLocale,
  resolveBrowserTimezone,
  validateIdentity,
  type BrowserConfig,
  type BrowserGrant,
  type BrowserIdentity,
} from '../browser-worker/models.js';
import type { BrowserExecutor } from '../browser-worker/executor.js';
import { BrowserSecurityPolicy, originOf, redact, sanitizedUrl } from '../browser-worker/security.js';
import {
  ALLOWED_HOST_OPS,
  BrowserArtifacts,
  BrowserGrantGovernance,
  INTERACTIONS,
  NAVIGATIONS,
  READS,
  assertBrowserEnabled,
  assertValidHostInputShape,
  boundedDiagnosticItems,
  hashValue,
  normalizeBrowserArguments,
  validateHostInputArgs,
  validateTargetState,
} from '../browser-worker/shared.js';
import { AgentCursorOverlay, type ElectronImageViewLike } from './agent-cursor-overlay.js';
import { CdpPageDriver, type CdpPointerEvent, type CdpTransport } from './cdp-driver.js';

export interface EmbeddedViewportLayout {
  conversation_id: string | null;
  visible: boolean;
  obscured: boolean;
  x: number;
  y: number;
  width: number;
  height: number;
  fit_viewport: boolean;
}

export interface ElectronWebContentsLike {
  readonly id: number;
  readonly debugger: CdpTransport;
  readonly navigationHistory: {
    canGoBack(): boolean;
    canGoForward(): boolean;
    goBack(): void;
    goForward(): void;
  };
  getURL(): string;
  getTitle(): string;
  isDestroyed(): boolean;
  loadURL(url: string): Promise<void>;
  reload(): void;
  stop(): void;
  focus(): void;
  setZoomFactor(factor: number): void;
  getUserAgent(): string;
  setUserAgent(userAgent: string): void;
  sendInputEvent(inputEvent: Record<string, unknown>): void;
  setWindowOpenHandler(handler: (details: { url: string }) => { action: 'deny' | 'allow' }): void;
  close(opts?: { waitForBeforeUnload?: boolean }): void;
  destroy?(): void;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  on(event: string, listener: (...args: any[]) => void): void;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  once?(event: string, listener: (...args: any[]) => void): void;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  removeListener?(event: string, listener: (...args: any[]) => void): void;
}

export interface ElectronWebContentsViewLike {
  readonly webContents: ElectronWebContentsLike;
  setBounds(bounds: { x: number; y: number; width: number; height: number }): void;
  setVisible(visible: boolean): void;
  setBackgroundColor?(color: string): void;
}

export interface ElectronSessionLike {
  getUserAgent?(): string;
  setUserAgent?(userAgent: string, acceptLanguages?: string): void;
  clearStorageData(options?: Record<string, unknown>): Promise<void>;
  setPermissionCheckHandler(handler: (() => boolean) | null): void;
  setPermissionRequestHandler(
    handler: ((_wc: unknown, _perm: string, callback: (granted: boolean) => void) => void) | null,
  ): void;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  on(event: string, listener: (...args: any[]) => void): void;
  webRequest: {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    onBeforeRequest(listener: ((details: any, callback: (resp: { cancel?: boolean }) => void) => void) | null): void;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    onBeforeSendHeaders(listener: ((details: any, callback: (resp: { requestHeaders?: Record<string, string> }) => void) => void) | null): void;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    onHeadersReceived(listener: ((details: any, callback: (resp: { cancel?: boolean }) => void) => void) | null): void;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    onCompleted(listener: ((details: any) => void) | null): void;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    onErrorOccurred(listener: ((details: any) => void) | null): void;
  };
}

export interface ElectronParentWindowLike {
  isDestroyed(): boolean;
  readonly contentView: {
    readonly children: unknown[];
    addChildView(view: unknown): void;
    removeChildView(view: unknown): void;
  };
}

export interface ElectronBrowserExecutorOptions {
  config?: Partial<BrowserConfig>;
  policy?: BrowserSecurityPolicy;
  WebContentsView?: new (options?: unknown) => ElectronWebContentsViewLike;
  ImageView?: new () => ElectronImageViewLike;
  createImageFromBitmap?: (
    buffer: Buffer,
    options: { width: number; height: number; scaleFactor: number },
  ) => unknown;
  fromPartition?: (partition: string) => ElectronSessionLike;
  getParentWindow?: () => ElectronParentWindowLike | null;
  partitionPrefix?: string;
  onPointer?: (pointer: BrowserPointerState) => void;
  pointerStepDelayMs?: number;
  cursorHideMs?: number;
}

interface EmbeddedTabState {
  view: ElectronWebContentsViewLike;
  webContents: ElectronWebContentsLike;
  driver: CdpPageDriver;
  tab_id: string;
  title: string;
  revision: number;
  observed_digest: string;
  lock: AsyncMutex;
  console: DiagnosticBuffer;
  page_errors: DiagnosticBuffer;
  network_errors: DiagnosticBuffer;
  viewport: { width: number; height: number };
  lastHttpStatus: number | null;
  lastRedirected: boolean;
  closed: boolean;
}

interface EmbeddedSessionState {
  identity: BrowserIdentity;
  identity_key: string;
  electronSession: ElectronSessionLike;
  partition: string;
  grant: BrowserGrant;
  session_id: string;
  context_id: string;
  state: string;
  tabs: Map<string, EmbeddedTabState>;
  active_tab_id: string;
  created_at: number;
  last_activity: number;
  actions: number;
  cancelled: boolean;
  headless: boolean;
  viewport: { width: number; height: number };
  pending: Set<{ cancel: () => void }>;
  control_lock: AsyncMutex;
}

function buildStandardDesktopUserAgent(rawUa: string): string {
  if (!rawUa) {
    return 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36';
  }
  return rawUa
    .replace(/\sElectron\/\S+/g, '')
    .replace(/\sFirik[^\s]*/gi, '')
    .replace(/HeadlessChrome/g, 'Chrome')
    .replace(/\s{2,}/g, ' ')
    .trim();
}

function buildAcceptLanguage(locale: string): string {
  const base = locale.split('-')[0] || 'en';
  if (base.toLowerCase() === 'en') {
    return `${locale},en;q=0.9`;
  }
  return `${locale},${base};q=0.9,en-US;q=0.8,en;q=0.7`;
}

function loadElectronApis(options: ElectronBrowserExecutorOptions): {
  WebContentsView: new (options?: unknown) => ElectronWebContentsViewLike;
  fromPartition: (partition: string) => ElectronSessionLike;
} {
  if (options.WebContentsView && options.fromPartition) {
    return { WebContentsView: options.WebContentsView, fromPartition: options.fromPartition };
  }
  // eslint-disable-next-line @typescript-eslint/no-require-imports, @typescript-eslint/no-explicit-any
  const electron = require('electron') as any;
  return {
    WebContentsView: options.WebContentsView ?? electron.WebContentsView,
    fromPartition: options.fromPartition ?? ((p: string) => electron.session.fromPartition(p)),
  };
}

function parseNavigationStatusUrl(tab: EmbeddedTabState): string {
  try {
    return tab.webContents.isDestroyed() ? 'about:blank' : tab.webContents.getURL() || 'about:blank';
  } catch {
    return 'about:blank';
  }
}

/**
 * Embedded Chromium `BrowserExecutor` backed directly by Electron `WebContentsView`
 * and internal CDP (`webContents.debugger`), preserving strict `BrowserSecurityPolicy`,
 * per-conversation isolated sessions, and single-use bound approvals without any
 * open TCP remote-debugging port or silent fallback to the Playwright worker.
 */
export class ElectronBrowserExecutor implements BrowserExecutor {
  readonly config: BrowserConfig;
  readonly policy: BrowserSecurityPolicy;
  readonly artifacts: BrowserArtifacts;
  private readonly _options: ElectronBrowserExecutorOptions;
  private readonly _governance = new BrowserGrantGovernance();
  private readonly _sessions = new Map<string, EmbeddedSessionState>();
  private readonly _tabsByWcId = new Map<number, { session: EmbeddedSessionState; tab: EmbeddedTabState }>();
  private readonly _navigationErrors = new Map<string, BrowserError>();
  private readonly _redirectCounts = new Map<number, number>();
  private readonly _redirectedRequests = new Set<number>();
  private readonly _cursorOverlay: AgentCursorOverlay;
  private _layout: EmbeddedViewportLayout = {
    conversation_id: null,
    visible: false,
    obscured: false,
    x: 0,
    y: 0,
    width: 1280,
    height: 800,
    fit_viewport: true,
  };

  constructor(options: ElectronBrowserExecutorOptions = {}) {
    this._options = options;
    this.config = defaultBrowserConfig(options.config);
    this.policy = options.policy ?? new BrowserSecurityPolicy();
    this.artifacts = new BrowserArtifacts();
    this._cursorOverlay = new AgentCursorOverlay({
      getParentWindow: options.getParentWindow,
      ImageView: options.ImageView,
      createImageFromBitmap: options.createImageFromBitmap,
      idleHideMs: options.cursorHideMs,
      hoverHideMs:
        options.cursorHideMs !== undefined
          ? Math.max(options.cursorHideMs, Math.round(options.cursorHideMs * 1.4))
          : undefined,
    });
  }

  private _emitHiddenPointer(conversationId: string): void {
    this._cursorOverlay.hide();
    this._options.onPointer?.({
      conversation_id: conversationId,
      visible: false,
      x: 0,
      y: 0,
      viewport_width: this.config.viewport_width,
      viewport_height: this.config.viewport_height,
      action: 'idle',
      pressing: false,
    });
  }

  private _handleTabPointer(
    session: EmbeddedSessionState,
    tab: EmbeddedTabState,
    event: CdpPointerEvent,
  ): void {
    if (
      session.cancelled ||
      tab.closed ||
      session.state === 'manual_control' ||
      tab.tab_id !== session.active_tab_id
    ) {
      return;
    }
    const pointerState: BrowserPointerState = {
      conversation_id: session.identity.conversation_id,
      visible: true,
      x: event.x,
      y: event.y,
      viewport_width: Math.max(1, tab.viewport.width),
      viewport_height: Math.max(1, tab.viewport.height),
      action: event.action,
      pressing: event.pressing,
      label: event.label,
    };
    this._options.onPointer?.(pointerState);

    const layout = this._layout;
    if (
      layout.conversation_id === session.identity.conversation_id &&
      layout.visible &&
      !layout.obscured &&
      this.config.enabled
    ) {
      const liveGrant = this._liveGrant(session.identity, session.grant);
      if (liveGrant.enabled && liveGrant.allow_screenshots && layout.width >= 40 && layout.height >= 40) {
        const zoom = layout.fit_viewport
          ? 1
          : Math.max(0.25, Math.min(1, layout.width / this.config.viewport_width));
        this._cursorOverlay.show(pointerState, layout, zoom);
      }
    }
  }

  bindGrant(identity: BrowserIdentity, grant: BrowserGrant): void {
    this._governance.bindGrant(identity, grant);
    this._syncPresentation();
  }

  bindGrantProvider(identity: BrowserIdentity, provider: () => BrowserGrant): void {
    this._governance.bindGrantProvider(identity, provider);
  }

  setLayout(layout: EmbeddedViewportLayout): void {
    this._layout = { ...layout };
    this._syncPresentation();
  }

  hideAllViews(): void {
    this._layout = { ...this._layout, visible: false };
    this._syncPresentation();
  }

  private _liveGrant(identity: BrowserIdentity, fallback: BrowserGrant): BrowserGrant {
    return this._governance.liveGrant(identity, fallback);
  }

  private _enabled(grant: BrowserGrant): void {
    assertBrowserEnabled(this.config, grant);
  }

  private _session(identity: BrowserIdentity, grant: BrowserGrant): EmbeddedSessionState {
    const checked = validateIdentity(identity);
    const key = identityKey(checked);
    const liveGrant = this._liveGrant(checked, grant);
    this._enabled(liveGrant);
    const session = this._sessions.get(key);
    if (!session) {
      throw new BrowserError('session_missing', 'Start an owned browser session first.');
    }
    if (session.identity_key !== key) {
      throw new BrowserError('ownership_denied', 'Browser ownership does not match.');
    }
    session.grant = liveGrant;
    if (session.cancelled || ['closed', 'closing', 'failed'].includes(session.state)) {
      throw new BrowserError(
        'session_unavailable',
        'The browser session is closed, cancelled or failed; state was not recreated.',
      );
    }
    return session;
  }

  private _tab(session: EmbeddedSessionState, args: Record<string, unknown>): EmbeddedTabState {
    const tabId = (args.tab_id as string | undefined) || session.active_tab_id;
    const tab = session.tabs.get(tabId);
    if (!tab || tab.closed || tab.webContents.isDestroyed()) {
      throw new BrowserError(
        'ownership_denied',
        'The tab is closed or does not belong to this execution identity.',
      );
    }
    return tab;
  }

  private _invalidate(tab: EmbeddedTabState): void {
    tab.revision += 1;
    tab.observed_digest = '';
  }

  private _status(session: EmbeddedSessionState, options?: { host?: boolean }): Record<string, unknown> {
    const activeTab = session.tabs.get(session.active_tab_id);
    const chromeMatch = /Chrome\/([\d.]+)/.exec(session.electronSession.getUserAgent?.() ?? '');
    const result: Record<string, unknown> = {
      session_id: session.session_id,
      context_id: session.context_id,
      state: session.state,
      engine: 'embedded',
      conversation_id: session.identity.conversation_id,
      project_id: session.identity.project_id,
      run_id: session.identity.run_id,
      actor_id: session.identity.actor_id,
      active_tab_id: session.active_tab_id,
      tab_count: session.tabs.size,
      revision: activeTab?.revision ?? 0,
      headless: false,
      created_at: session.created_at,
      last_activity: session.last_activity,
      browser_version: chromeMatch?.[1] ?? 'electron-chromium',
      actions: session.actions,
      max_actions: this.config.max_actions_per_task,
      network_boundary: 'host_request_policy; not OS isolation',
      websockets: 'blocked',
      service_workers: 'managed_by_webrequest',
    };
    if (options?.host || session.state !== 'manual_control') {
      result.tabs = [...session.tabs.values()]
        .filter(tab => !tab.closed && !tab.webContents.isDestroyed())
        .map(tab => ({
          tab_id: tab.tab_id,
          url: sanitizedUrl(parseNavigationStatusUrl(tab)),
          title: tab.title || '',
          active: tab.tab_id === session.active_tab_id,
        }));
    }
    return result;
  }

  private _installSessionPolicy(session: EmbeddedSessionState): void {
    const ses = session.electronSession;
    const locale = resolveBrowserLocale(this.config.locale);
    const acceptLang = buildAcceptLanguage(locale);
    const baseUa = buildStandardDesktopUserAgent(ses.getUserAgent?.() ?? '');
    ses.setUserAgent?.(baseUa, acceptLang);
    ses.setPermissionCheckHandler(() => false);
    ses.setPermissionRequestHandler((_wc, _perm, cb) => cb(false));
    ses.on('will-download', (event: { preventDefault?: () => void }, item: { cancel?: () => void }) => {
      event?.preventDefault?.();
      item?.cancel?.();
    });

    const recordError = (wcId: number | undefined, entry: Record<string, unknown>) => {
      if (!wcId) return;
      const hit = this._tabsByWcId.get(wcId);
      if (!hit || hit.session.state === 'manual_control') return;
      hit.tab.network_errors.append({ ...entry, timestamp: Date.now() / 1000 });
    };

    const recordBlockedNavigation = (wcId: number | undefined, resourceType: string, err: BrowserError) => {
      if (!wcId || resourceType !== 'mainFrame') return;
      const hit = this._tabsByWcId.get(wcId);
      if (!hit) return;
      this._navigationErrors.set(hit.tab.tab_id, err);
    };

    ses.webRequest.onBeforeRequest((details, callback) => {
      const liveSession = this._sessions.get(session.identity_key);
      if (
        !liveSession ||
        liveSession !== session ||
        liveSession.cancelled ||
        ['closed', 'closing', 'failed'].includes(liveSession.state)
      ) {
        callback({ cancel: true });
        return;
      }
      const url = String(details.url ?? '');
      const resourceType = String(details.resourceType ?? 'other');
      const liveGrant = this._liveGrant(liveSession.identity, liveSession.grant);
      liveSession.grant = liveGrant;

      if (!this.config.enabled || !liveGrant.enabled) {
        recordBlockedNavigation(
          details.webContentsId,
          resourceType,
          new BrowserError('permission_denied', 'Browser permission revoked.'),
        );
        callback({ cancel: true });
        return;
      }
      if (url === 'about:blank') {
        callback({ cancel: false });
        return;
      }
      if (url.startsWith('ws:') || url.startsWith('wss:') || resourceType === 'webSocket') {
        recordError(details.webContentsId, {
          url: sanitizedUrl(url),
          method: details.method,
          error: 'WebSocket connections are blocked.',
        });
        callback({ cancel: true });
        return;
      }

      this.policy.validate(url, liveGrant).then(
        () => callback({ cancel: false }),
        err => {
          const browserErr =
            err instanceof BrowserError ? err : new BrowserError('origin_denied', 'Request denied by security policy.');
          recordBlockedNavigation(details.webContentsId, resourceType, browserErr);
          recordError(details.webContentsId, {
            url: sanitizedUrl(url),
            method: details.method,
            error: browserErr.message,
          });
          callback({ cancel: true });
        },
      );
    });

    ses.webRequest.onBeforeSendHeaders((details, callback) => {
      const headers = { ...(details.requestHeaders ?? {}) };
      if (!Object.keys(headers).some(k => k.toLowerCase() === 'accept-language')) {
        headers['Accept-Language'] = acceptLang;
      }
      if ('User-Agent' in headers) {
        headers['User-Agent'] = buildStandardDesktopUserAgent(headers['User-Agent']);
      }
      callback({ requestHeaders: headers });
    });

    ses.webRequest.onHeadersReceived((details, callback) => {
      const liveSession = this._sessions.get(session.identity_key);
      if (!liveSession || liveSession !== session || liveSession.cancelled) {
        callback({ cancel: true });
        return;
      }
      const status = Number(details.statusCode ?? 0);
      const resourceType = String(details.resourceType ?? 'other');
      if (resourceType === 'mainFrame' && details.webContentsId) {
        const hit = this._tabsByWcId.get(details.webContentsId);
        if (hit) hit.tab.lastHttpStatus = status || null;
      }
      if (status < 300 || status >= 400) {
        callback({});
        return;
      }

      const headers = (details.responseHeaders ?? {}) as Record<string, string[]>;
      const locationKey = Object.keys(headers).find(k => k.toLowerCase() === 'location');
      const locationRaw = locationKey ? headers[locationKey]?.[0] : undefined;
      if (!locationRaw) {
        callback({});
        return;
      }

      const hops = (this._redirectCounts.get(details.id) ?? 0) + 1;
      this._redirectCounts.set(details.id, hops);
      this._redirectedRequests.add(details.id);
      if (resourceType === 'mainFrame' && details.webContentsId) {
        const hit = this._tabsByWcId.get(details.webContentsId);
        if (hit) hit.tab.lastRedirected = true;
      }
      if (hops > 10) {
        recordBlockedNavigation(
          details.webContentsId,
          resourceType,
          new BrowserError('browser_operation_failed', 'Too many HTTP redirects.'),
        );
        callback({ cancel: true });
        return;
      }

      let target: string;
      try {
        target = new URL(locationRaw, String(details.url)).toString();
      } catch {
        recordBlockedNavigation(
          details.webContentsId,
          resourceType,
          new BrowserError('origin_denied', 'Invalid redirect location.'),
        );
        callback({ cancel: true });
        return;
      }

      const method = String(details.method ?? 'GET').toUpperCase();
      if (!['GET', 'HEAD'].includes(method) && ![302, 303].includes(status)) {
        recordError(details.webContentsId, {
          url: sanitizedUrl(target),
          method,
          error: 'Blocked unsafe non-GET redirect.',
        });
        callback({ cancel: true });
        return;
      }
      if (resourceType !== 'mainFrame') {
        try {
          if (originOf(target) !== originOf(String(details.url))) {
            recordError(details.webContentsId, {
              url: sanitizedUrl(target),
              method,
              error: 'Blocked cross-origin subresource redirect.',
            });
            callback({ cancel: true });
            return;
          }
        } catch {
          callback({ cancel: true });
          return;
        }
      }

      const liveGrant = this._liveGrant(liveSession.identity, liveSession.grant);
      this.policy.validate(target, liveGrant).then(
        () => callback({}),
        err => {
          const browserErr =
            err instanceof BrowserError ? err : new BrowserError('origin_denied', 'Redirect destination denied.');
          recordBlockedNavigation(details.webContentsId, resourceType, browserErr);
          recordError(details.webContentsId, {
            url: sanitizedUrl(target),
            method,
            error: browserErr.message,
          });
          callback({ cancel: true });
        },
      );
    });

    ses.webRequest.onCompleted(details => {
      this._redirectCounts.delete(details.id);
      this._redirectedRequests.delete(details.id);
      if (Number(details.statusCode ?? 0) >= 400) {
        recordError(details.webContentsId, {
          url: sanitizedUrl(String(details.url ?? '')),
          method: details.method,
          status: details.statusCode,
        });
      }
    });

    ses.webRequest.onErrorOccurred(details => {
      this._redirectCounts.delete(details.id);
      this._redirectedRequests.delete(details.id);
      if (String(details.error ?? '') !== 'net::ERR_ABORTED') {
        recordError(details.webContentsId, {
          url: sanitizedUrl(String(details.url ?? '')),
          method: details.method,
          error: redact(String(details.error ?? 'Network request failed'), 500),
        });
      }
    });
  }

  private async _createTab(session: EmbeddedSessionState): Promise<EmbeddedTabState> {
    const { WebContentsView } = loadElectronApis(this._options);
    const view = new WebContentsView({
      webPreferences: {
        session: session.electronSession,
        nodeIntegration: false,
        contextIsolation: true,
        sandbox: true,
        webviewTag: false,
        spellcheck: false,
        backgroundThrottling: false,
      },
    });
    view.setBackgroundColor?.('#ffffff');
    view.setVisible(false);
    view.setBounds({
      x: 0,
      y: 0,
      width: session.viewport.width,
      height: session.viewport.height,
    });

    const wc = view.webContents;
    const baseUa = buildStandardDesktopUserAgent(
      wc.getUserAgent?.() || session.electronSession.getUserAgent?.() || '',
    );
    wc.setUserAgent?.(baseUa);

    const locale = resolveBrowserLocale(this.config.locale);
    const timezoneId = resolveBrowserTimezone(this.config.timezone_id);
    const tabId = randomId('tab');

    const consoleBuf = new DiagnosticBuffer(this.config.max_diagnostics);
    const pageErrorBuf = new DiagnosticBuffer(this.config.max_diagnostics);
    const netErrorBuf = new DiagnosticBuffer(this.config.max_diagnostics);

    const tab: EmbeddedTabState = {
      view,
      webContents: wc,
      driver: null as unknown as CdpPageDriver,
      tab_id: tabId,
      title: '',
      revision: 1,
      observed_digest: '',
      lock: new AsyncMutex(),
      console: consoleBuf,
      page_errors: pageErrorBuf,
      network_errors: netErrorBuf,
      viewport: { ...session.viewport },
      lastHttpStatus: null,
      lastRedirected: false,
      closed: false,
    };

    const driver = new CdpPageDriver(wc.debugger, {
      onConsole: entry => {
        if (session.state !== 'manual_control') consoleBuf.append(entry as unknown as Record<string, unknown>);
      },
      onPageError: entry => {
        if (session.state !== 'manual_control') pageErrorBuf.append(entry as unknown as Record<string, unknown>);
      },
      onPointer: entry => {
        this._handleTabPointer(session, tab, entry);
      },
      pointerStepDelayMs: this._options.pointerStepDelayMs,
    });
    tab.driver = driver;

    this._tabsByWcId.set(wc.id, { session, tab });
    session.tabs.set(tabId, tab);
    session.active_tab_id = tabId;

    wc.setWindowOpenHandler(({ url }) => {
      if (!url || !url.startsWith('http')) {
        return { action: 'deny' };
      }
      // Route `_blank` popups safely into managed tabs rather than unmanaged OS windows.
      if (session.tabs.size < this.config.max_tabs_per_session && session.state !== 'closing' && !session.cancelled) {
        const liveGrant = this._liveGrant(session.identity, session.grant);
        void this.policy.validate(url, liveGrant).then(async () => {
          if (session.tabs.size >= this.config.max_tabs_per_session || session.cancelled) return;
          const popupTab = await this._createTab(session);
          this._syncPresentation();
          await this._navigateTab(session, popupTab, 'navigate', url);
        }).catch(() => {});
      }
      return { action: 'deny' };
    });

    wc.on('page-title-updated', (_ev: unknown, title: string) => {
      tab.title = redact(String(title ?? ''), 500);
    });
    wc.on('did-navigate', (_ev: unknown, url: string) => {
      this._invalidate(tab);
      if (session.state !== 'manual_control' && url && url !== 'about:blank') {
        const liveGrant = this._liveGrant(session.identity, session.grant);
        void this.policy.validate(url, liveGrant).catch(() => {
          if (!wc.isDestroyed()) wc.stop();
        });
      }
    });
    wc.on('before-input-event', (_ev: unknown, input: { type?: string }) => {
      if (session.state !== 'busy' && (input?.type === 'mouseUp' || input?.type === 'keyUp')) {
        this._invalidate(tab);
      }
    });
    wc.on('destroyed', () => {
      tab.closed = true;
      this._tabsByWcId.delete(wc.id);
      session.tabs.delete(tabId);
      if (session.active_tab_id === tabId) {
        session.active_tab_id = session.tabs.keys().next().value ?? '';
      }
      this._syncPresentation();
    });

    const parent = this._options.getParentWindow?.();
    if (parent && !parent.isDestroyed()) {
      parent.contentView.addChildView(view);
    }

    // Electron WebContentsView has no active renderer frame for CDP attach/enable
    // until an initial navigation (even about:blank) commits.
    if (!wc.getURL()) {
      await wc.loadURL('about:blank').catch(() => {});
    }
    await driver
      .setEmulation({
        locale,
        acceptLanguage: buildAcceptLanguage(locale),
        timezoneId,
        userAgent: baseUa,
      })
      .catch(() => {});
    this._syncPresentation();
    return tab;
  }

  private _syncPresentation(): void {
    const parent = this._options.getParentWindow?.();
    const layout = this._layout;

    let visibleOwner: EmbeddedSessionState | undefined;
    if (layout.conversation_id && layout.visible && !layout.obscured && this.config.enabled) {
      visibleOwner = [...this._sessions.values()].find(
        s =>
          s.identity.conversation_id === layout.conversation_id &&
          !s.cancelled &&
          ['ready', 'busy', 'manual_control'].includes(s.state),
      );
    }

    let showActive = false;
    if (visibleOwner) {
      const liveGrant = this._liveGrant(visibleOwner.identity, visibleOwner.grant);
      showActive = Boolean(
        liveGrant.enabled &&
          liveGrant.allow_screenshots &&
          layout.width >= 40 &&
          layout.height >= 40,
      );
    }

    if (!showActive || visibleOwner?.state === 'manual_control') {
      this._cursorOverlay.hide();
    }

    for (const session of this._sessions.values()) {
      for (const tab of session.tabs.values()) {
        if (tab.closed || tab.webContents.isDestroyed()) continue;
        const isTarget =
          showActive &&
          session === visibleOwner &&
          tab.tab_id === session.active_tab_id;
        if (isTarget) {
          if (parent && !parent.isDestroyed() && !parent.contentView.children.includes(tab.view)) {
            parent.contentView.addChildView(tab.view);
          }
          const bounds = {
            x: Math.max(0, Math.round(layout.x)),
            y: Math.max(0, Math.round(layout.y)),
            width: Math.max(1, Math.round(layout.width)),
            height: Math.max(1, Math.round(layout.height)),
          };
          tab.view.setBounds(bounds);
          let nextViewport: { width: number; height: number };
          if (layout.fit_viewport) {
            tab.webContents.setZoomFactor(1);
            nextViewport = { width: bounds.width, height: bounds.height };
            session.viewport = { ...nextViewport };
          } else {
            const zoom = Math.max(0.25, Math.min(1, bounds.width / this.config.viewport_width));
            tab.webContents.setZoomFactor(zoom);
            nextViewport = {
              width: Math.round(bounds.width / zoom),
              height: Math.round(bounds.height / zoom),
            };
          }
          if (tab.viewport.width !== nextViewport.width || tab.viewport.height !== nextViewport.height) {
            tab.viewport = nextViewport;
            if (session.state !== 'busy') this._invalidate(tab);
          }
          tab.view.setVisible(true);
        } else {
          tab.view.setVisible(false);
        }
      }
    }
  }

  private async _start(
    identity: BrowserIdentity,
    grant: BrowserGrant,
    args: Record<string, unknown>,
  ): Promise<Record<string, unknown>> {
    const key = identityKey(identity);
    const existing = this._sessions.get(key);
    if (existing && !existing.cancelled && !['closed', 'closing', 'failed'].includes(existing.state)) {
      return this._status(existing, { host: true });
    }
    if (this._sessions.size >= this.config.max_sessions) {
      throw new BrowserError('browser_limit', 'Maximum concurrent browser sessions reached.');
    }

    const { fromPartition } = loadElectronApis(this._options);
    const prefix = this._options.partitionPrefix ?? 'firik-embedded';
    const hash = createHash('sha256').update(key).digest('hex').slice(0, 24);
    const partition = `${prefix}-${hash}`;
    const electronSession = fromPartition(partition);

    const viewport = {
      width: (args.width as number | undefined) ?? this.config.viewport_width,
      height: (args.height as number | undefined) ?? this.config.viewport_height,
    };

    const session: EmbeddedSessionState = {
      identity,
      identity_key: key,
      electronSession,
      partition,
      grant,
      session_id: randomId('bsess'),
      context_id: randomId('bctx'),
      state: 'ready',
      tabs: new Map(),
      active_tab_id: '',
      created_at: Date.now() / 1000,
      last_activity: Date.now() / 1000,
      actions: 0,
      cancelled: false,
      headless: false,
      viewport,
      pending: new Set(),
      control_lock: new AsyncMutex(),
    };

    this._sessions.set(key, session);
    this._installSessionPolicy(session);
    try {
      await this._createTab(session);
    } catch (err) {
      this._sessions.delete(key);
      throw err;
    }
    return this._status(session, { host: true });
  }

  private async _navigateTab(
    session: EmbeddedSessionState,
    tab: EmbeddedTabState,
    operation: 'navigate' | 'back' | 'forward' | 'reload',
    requestedUrl?: string,
  ): Promise<Record<string, unknown>> {
    const wc = tab.webContents;
    const requested = requestedUrl ?? parseNavigationStatusUrl(tab);
    this._navigationErrors.delete(tab.tab_id);
    tab.lastHttpStatus = null;
    tab.lastRedirected = false;

    if (operation === 'navigate') {
      await this.policy.validate(requested, session.grant);
    }
    this._emitHiddenPointer(session.identity.conversation_id);

    try {
      await new Promise<void>((resolve, reject) => {
        let settled = false;
        const cleanup = () => {
          wc.removeListener?.('did-finish-load', onFinish);
          wc.removeListener?.('did-navigate-in-page', onNavigateInPage);
          wc.removeListener?.('did-fail-load', onFail);
        };
        const finishOk = () => {
          if (settled) return;
          settled = true;
          cleanup();
          resolve();
        };
        const finishErr = (err: Error) => {
          if (settled) return;
          settled = true;
          cleanup();
          reject(err);
        };
        const onFinish = () => {
          finishOk();
        };
        const onNavigateInPage = (_ev: unknown, _url: string, isMainFrame: boolean) => {
          if (isMainFrame && (operation === 'back' || operation === 'forward')) {
            finishOk();
          }
        };
        const onFail = (_ev: unknown, errorCode: number, errorDescription: string, _url: string, isMainFrame: boolean) => {
          if (!isMainFrame) return;
          finishErr(new Error(`Navigation failed (${errorCode}): ${errorDescription}`));
        };

        wc.on('did-finish-load', onFinish);
        wc.on('did-navigate-in-page', onNavigateInPage);
        wc.on('did-fail-load', onFail);

        if (operation === 'navigate') {
          wc.loadURL(requested).then(finishOk, finishErr);
        } else if (operation === 'back') {
          if (!wc.navigationHistory.canGoBack()) {
            finishOk();
            return;
          }
          wc.navigationHistory.goBack();
        } else if (operation === 'forward') {
          if (!wc.navigationHistory.canGoForward()) {
            finishOk();
            return;
          }
          wc.navigationHistory.goForward();
        } else {
          wc.reload();
        }
      });
    } catch (err) {
      this._invalidate(tab);
      const blocked = this._navigationErrors.get(tab.tab_id);
      this._navigationErrors.delete(tab.tab_id);
      if (blocked) {
        throw blocked;
      }
      throw err;
    } finally {
      this._navigationErrors.delete(tab.tab_id);
    }

    this._invalidate(tab);
    const finalUrl = parseNavigationStatusUrl(tab);
    if (finalUrl !== 'about:blank') {
      await this.policy.validate(finalUrl, session.grant);
    }
    tab.title = redact(wc.getTitle?.() ?? '', 500);
    return {
      requested_url: sanitizedUrl(requested),
      url: sanitizedUrl(finalUrl),
      title: tab.title,
      http_status: tab.lastHttpStatus,
      redirected: tab.lastRedirected,
      outcome: 'navigation_observed',
      task_completion_verified: false,
    };
  }

  private async _actionState(
    session: EmbeddedSessionState,
    tab: EmbeddedTabState,
    operation: string,
    args: Record<string, unknown>,
  ): Promise<[string, Record<string, unknown>]> {
    if (args.revision !== tab.revision || !tab.observed_digest) {
      throw new BrowserError('stale_observation', 'Snapshot revision is stale; capture a fresh snapshot.');
    }
    const currentDigest = await tab.driver.digest();
    if (currentDigest !== tab.observed_digest) {
      this._invalidate(tab);
      throw new BrowserError(
        'stale_observation',
        'Page content changed since snapshot; capture a fresh snapshot.',
      );
    }
    if (operation === 'scroll') {
      const state = { url: parseNavigationStatusUrl(tab), revision: tab.revision, dom: currentDigest };
      return [hashValue(state), { target: 'page viewport', form: [] }];
    }
    const resolved = await tab.driver.resolveTarget(args.target as Record<string, string>, true);
    const state = await tab.driver.inspectTargetState(resolved);
    const preview = await validateTargetState(
      state,
      args.target,
      this.policy,
      session.grant,
      this.config.max_snapshot_chars,
    );
    state.dom = currentDigest;
    return [hashValue(state), preview];
  }

  async prepare(
    identity: BrowserIdentity,
    grant: BrowserGrant,
    operation: string,
    rawArgs?: Record<string, unknown>,
  ): Promise<Record<string, unknown>> {
    const [op, args] = normalizeBrowserArguments(operation, rawArgs, this.config.action_timeout_ms);
    if (!INTERACTIONS.has(op)) {
      throw new BrowserError('unsupported_capability', 'Only interactive operations require bound action approval.');
    }
    const session = this._session(identity, grant);
    if (session.state === 'manual_control') {
      throw new BrowserError('manual_control', 'The user currently controls this browser.');
    }
    const tab = this._tab(session, args);
    return session.control_lock.run(() =>
      tab.lock.run(async () => {
        const [digest, preview] = await this._actionState(session, tab, op, args);
        const token = this._governance.issueApproval({
          identityKey: session.identity_key,
          grant: session.grant,
          identity: session.identity,
          operation: op,
          args,
          stateDigest: digest,
          tabId: tab.tab_id,
          ttlSeconds: this.config.approval_ttl_seconds,
        });
        const rawValue = args.value ?? args.key ?? (args.values !== undefined ? String(args.values) : '');
        return {
          fingerprint: token,
          action: op,
          origin: originOf(parseNavigationStatusUrl(tab)),
          session_id: session.session_id,
          tab_id: tab.tab_id,
          revision: tab.revision,
          expires_in_seconds: this.config.approval_ttl_seconds,
          value: redact(rawValue),
          consequence: 'This interaction may change or submit information on the website.',
          ...preview,
        };
      }),
    );
  }

  async dispatch(
    identity: BrowserIdentity,
    grant: BrowserGrant,
    operation: string,
    rawArgs?: Record<string, unknown> | null,
    options?: { approvedFingerprint?: string | null; hostControl?: boolean; signal?: AbortSignal },
  ): Promise<Record<string, unknown>> {
    const checkedIdentity = validateIdentity(identity);
    const key = identityKey(checkedIdentity);
    const session = this._sessions.get(key);
    const cleanOp = operation.replace(/^browser_/, '');
    if (session && cleanOp !== 'close') {
      return session.control_lock.run(() =>
        this._dispatch(checkedIdentity, grant, operation, rawArgs, options),
      );
    }
    return this._dispatch(checkedIdentity, grant, operation, rawArgs, options);
  }

  async hostCommand(
    identity: BrowserIdentity,
    grant: BrowserGrant,
    operation: string,
    rawArgs?: Record<string, unknown> | null,
  ): Promise<Record<string, unknown>> {
    const cleanOp = operation.replace(/^browser_/, '');
    if (!ALLOWED_HOST_OPS.has(cleanOp)) {
      throw new BrowserError('unsupported_capability', 'Unsupported host browser control.');
    }
    return this.dispatch(identity, grant, cleanOp, rawArgs, { hostControl: true });
  }

  private async _dispatch(
    identity: BrowserIdentity,
    grant: BrowserGrant,
    operation: string,
    rawArgs?: Record<string, unknown> | null,
    options?: { approvedFingerprint?: string | null; hostControl?: boolean; signal?: AbortSignal },
  ): Promise<Record<string, unknown>> {
    const hostControl = Boolean(options?.hostControl);
    const approvedFingerprint = options?.approvedFingerprint ?? null;
    const [op, args] = normalizeBrowserArguments(operation, rawArgs, this.config.action_timeout_ms);
    const liveGrant = this._liveGrant(identity, grant);
    this._enabled(liveGrant);

    if (op === 'upload' || op === 'download') {
      throw new BrowserError(
        'unsupported_capability',
        'Browser file transfers are disabled until host file grants and controlled download review are implemented.',
      );
    }
    if (op === 'resume_after_handoff') {
      throw new BrowserError('host_action_required', 'Only the host UI or terminal can resume a manual handoff.');
    }
    if (op === 'start') {
      return this._start(identity, liveGrant, args);
    }
    const key = identityKey(identity);
    if (op === 'status' && !this._sessions.has(key)) {
      return { state: 'uninitialized', engine: 'embedded', tab_count: 0 };
    }
    if (op === 'close') {
      await this.closeOwner(identity);
      return { state: 'closed' };
    }

    let session = this._session(identity, liveGrant);
    if (op === 'status') {
      return this._status(session, { host: hostControl });
    }
    if (session.state === 'manual_control' && !hostControl) {
      throw new BrowserError(
        'manual_control',
        'Agent observation and interaction are paused during manual control.',
      );
    }
    if (op === 'request_handoff') {
      session.state = 'manual_control';
      this._emitHiddenPointer(session.identity.conversation_id);
      this._governance.clearOwnerApprovals(key);
      for (const tab of session.tabs.values()) {
        await tab.lock.run(async () => {
          this._invalidate(tab);
          tab.console.clear();
          tab.page_errors.clear();
          tab.network_errors.clear();
        });
      }
      this._syncPresentation();
      const active = session.tabs.get(session.active_tab_id);
      if (active && !active.webContents.isDestroyed()) {
        active.webContents.focus?.();
      }
      return this._status(session, { host: hostControl });
    }
    if (op === 'resize') {
      if (!hostControl) {
        throw new BrowserError('host_action_required', 'Only host browser controls may resize the viewport.');
      }
      const viewport = { width: args.width as number, height: args.height as number };
      session.viewport = viewport;
      for (const openTab of session.tabs.values()) {
        if (openTab.closed || openTab.webContents.isDestroyed()) continue;
        await openTab.lock.run(async () => {
          openTab.viewport = { ...viewport };
          openTab.view.setBounds({ x: 0, y: 0, width: viewport.width, height: viewport.height });
          this._invalidate(openTab);
        });
      }
      this._syncPresentation();
      return this._status(session, { host: hostControl });
    }
    if (op === 'list_tabs') {
      const tabs = [...session.tabs.values()]
        .filter(t => !t.closed && !t.webContents.isDestroyed())
        .map(t => ({
          tab_id: t.tab_id,
          url: sanitizedUrl(parseNavigationStatusUrl(t)),
          title: redact(t.webContents.getTitle?.() ?? t.title, 500),
          active: t.tab_id === session.active_tab_id,
        }));
      return { tabs };
    }
    if (op === 'new_tab') {
      if (session.tabs.size >= this.config.max_tabs_per_session) {
        throw new BrowserError('browser_limit', 'Maximum tabs per session reached.');
      }
      if (typeof args.url === 'string' && args.url) {
        await this.policy.validate(args.url, liveGrant);
      }
      this._emitHiddenPointer(session.identity.conversation_id);
      const tab = await this._createTab(session);
      if (typeof args.url === 'string' && args.url) {
        return this._dispatch(
          identity,
          liveGrant,
          'navigate',
          { url: args.url, tab_id: tab.tab_id },
          { hostControl },
        );
      }
      return { tab_id: tab.tab_id, url: 'about:blank' };
    }

    const tab = this._tab(session, args);
    if (op === 'select_tab') {
      session.active_tab_id = tab.tab_id;
      this._emitHiddenPointer(session.identity.conversation_id);
      this._syncPresentation();
      return { tab_id: tab.tab_id, url: sanitizedUrl(parseNavigationStatusUrl(tab)) };
    }
    if (op === 'close_tab') {
      return tab.lock.run(async () => {
        await this._destroyTab(session, tab);
        this._emitHiddenPointer(session.identity.conversation_id);
        this._syncPresentation();
        return { closed_tab_id: tab.tab_id };
      });
    }

    let dispatched = false;
    const started = performance.now();
    const previousState = session.state;
    let abortReject: (() => void) | null = null;
    const pendingHandle = {
      cancel: () => {
        abortReject?.();
      },
    };
    session.pending.add(pendingHandle);

    try {
      return await tab.lock.run(async () => {
        session = this._session(identity, liveGrant);
        if (session.state === 'manual_control' && !hostControl) {
          throw new BrowserError('manual_control', 'The user currently controls this browser.');
        }
        if (!hostControl && session.actions >= this.config.max_actions_per_task) {
          throw new BrowserError('browser_limit', 'Browser action budget exhausted.');
        }
        const currentTabUrl = parseNavigationStatusUrl(tab);
        if (op !== 'navigate' && currentTabUrl !== 'about:blank') {
          await this.policy.validate(currentTabUrl, session.grant);
        }
        if (INTERACTIONS.has(op)) {
          const prepared = this._governance.consumeApproval({
            approvedFingerprint,
            identityKey: session.identity_key,
            grant: session.grant,
            operation: op,
            args,
            tabId: tab.tab_id,
          });
          const [digest] = await this._actionState(session, tab, op, args);
          if (digest !== prepared.state_digest) {
            throw new BrowserError(
              'approval_invalidated',
              'The reviewed target or form changed; obtain fresh approval.',
            );
          }
        }
        if (!hostControl) {
          session.actions += 1;
        }
        session.last_activity = Date.now() / 1000;
        session.state = 'busy';
        dispatched = INTERACTIONS.has(op) || NAVIGATIONS.has(op);
        const timeoutMs =
          (NAVIGATIONS.has(op) ? this.config.navigation_timeout_ms : this.config.action_timeout_ms) + 1000;

        let timer: NodeJS.Timeout | undefined;
        try {
          const result = await Promise.race([
            this._execute(session, tab, op, args),
            new Promise<never>((_, reject) => {
              timer = setTimeout(() => reject(new Error('Operation timeout')), timeoutMs);
              abortReject = () => {
                const err = new Error('Cancelled');
                err.name = 'AbortError';
                reject(err);
              };
              if (options?.signal) {
                if (options.signal.aborted) {
                  abortReject();
                } else {
                  options.signal.addEventListener('abort', () => abortReject?.(), { once: true });
                }
              }
            }),
          ]);
          return {
            ...result,
            session_id: session.session_id,
            tab_id: tab.tab_id,
            revision: tab.revision,
            duration_seconds: Number(((performance.now() - started) / 1000).toFixed(4)),
          };
        } finally {
          if (timer) clearTimeout(timer);
        }
      });
    } catch (err) {
      if (err instanceof BrowserError) {
        throw err;
      }
      if (err instanceof Error && err.name === 'AbortError') {
        session.cancelled = true;
        session.state = 'closing';
        await this._closeSession(session);
        throw new BrowserError(
          dispatched ? 'tool_outcome_unknown' : 'cancelled',
          dispatched
            ? 'Browser action cancelled; inspect remote state before repeating.'
            : 'Browser action cancelled.',
          { dispatched, requires_reconciliation: dispatched },
        );
      }
      throw new BrowserError(
        dispatched ? 'tool_outcome_unknown' : 'browser_operation_failed',
        dispatched
          ? 'Browser action outcome is unknown; inspect the page before repeating.'
          : 'Browser observation failed or timed out.',
        { dispatched, requires_reconciliation: dispatched },
      );
    } finally {
      session.pending.delete(pendingHandle);
      if (session.state === 'busy') {
        session.state = previousState === 'manual_control' ? 'manual_control' : 'ready';
      }
    }
  }

  private async _execute(
    session: EmbeddedSessionState,
    tab: EmbeddedTabState,
    operation: string,
    args: Record<string, unknown>,
  ): Promise<Record<string, unknown>> {
    if (NAVIGATIONS.has(operation)) {
      return this._navigateTab(
        session,
        tab,
        operation as 'navigate' | 'back' | 'forward' | 'reload',
        args.url as string | undefined,
      );
    }

    if (operation === 'snapshot') {
      const snap = await tab.driver.ariaSnapshot(this.config.max_snapshot_chars);
      tab.revision += 1;
      tab.observed_digest = snap.digest;
      return {
        url: sanitizedUrl(parseNavigationStatusUrl(tab)),
        type: 'aria',
        captured_at: Date.now() / 1000,
        content: snap.content,
        truncated: snap.truncated,
        untrusted: true,
        frames: snap.frames,
      };
    }

    if (operation === 'extract_text') {
      const extracted = await tab.driver.extractText(this.config.max_snapshot_chars);
      return {
        content: extracted.content,
        truncated: extracted.truncated,
        untrusted: true,
      };
    }

    if (operation === 'get_links') {
      const { total, links } = await tab.driver.getLinks();
      const result = boundedDiagnosticItems(
        links.map(l => ({ text: redact(l.text), url: sanitizedUrl(l.url) })),
        'links',
        this.config.max_snapshot_chars,
      );
      result.truncated = Boolean(result.truncated) || total > links.length;
      return result;
    }

    if (operation === 'get_forms') {
      const { total, forms } = await tab.driver.getForms();
      for (const form of forms) {
        form.action = sanitizedUrl(form.action);
        for (const item of form.fields) {
          item.name = redact(item.name);
          item.label = redact(item.label);
        }
      }
      const result = boundedDiagnosticItems(forms, 'forms', this.config.max_snapshot_chars);
      result.truncated =
        Boolean(result.truncated) ||
        total > forms.length ||
        forms.some(f => f.fieldCount > f.fields.length);
      return result;
    }

    if (['get_console', 'get_page_errors', 'get_network_errors'].includes(operation)) {
      const bufferKey = operation.replace(/^get_/, '') as 'console' | 'page_errors' | 'network_errors';
      const buffer = tab[bufferKey];
      const result = boundedDiagnosticItems(buffer.toArray(), 'entries', this.config.max_snapshot_chars);
      result.truncated = buffer.dropped > 0 || Boolean(result.truncated);
      result.dropped_entries = buffer.dropped;
      return result;
    }

    if (operation === 'screenshot') {
      const data = await this._capture(session, tab, { fullPage: Boolean(args.full_page), host: false });
      const artifact = this.artifacts.save(session.identity, data);
      return {
        artifact_id: artifact.artifact_id,
        artifact_path: artifact.path,
        mime_type: 'image/png',
        size_bytes: artifact.size,
        retention: 'ephemeral_until_session_close',
        model_image: false,
      };
    }

    if (operation === 'wait_for') {
      const timeout = typeof args.timeout_ms === 'number' ? args.timeout_ms : this.config.action_timeout_ms;
      if (args.target) {
        await tab.driver.waitForTarget(
          args.target as Record<string, string>,
          (args.state as string | undefined) ?? 'visible',
          timeout,
        );
      } else {
        await new Promise(resolve => setTimeout(resolve, Math.min(timeout, 250)));
      }
      return { outcome: 'condition_observed' };
    }

    if (INTERACTIONS.has(operation)) {
      if (operation === 'scroll') {
        await tab.driver.scroll(
          typeof args.x === 'number' ? args.x : 0,
          typeof args.y === 'number' ? args.y : 600,
          tab.viewport,
        );
      } else {
        const resolved = await tab.driver.resolveTarget(args.target as Record<string, string>, true);
        if (operation === 'click') {
          await tab.driver.click(resolved);
        } else if (operation === 'fill') {
          await tab.driver.fill(resolved, args.value as string);
        } else if (operation === 'type') {
          await tab.driver.type(resolved, args.value as string);
        } else if (operation === 'press') {
          await tab.driver.press(resolved, args.key as string);
        } else if (operation === 'select') {
          await tab.driver.select(resolved, args.values as string[]);
        } else if (operation === 'check') {
          await tab.driver.check(resolved, typeof args.checked === 'boolean' ? args.checked : true);
        } else if (operation === 'hover') {
          await tab.driver.hover(resolved);
        }
      }
      this._invalidate(tab);
      return {
        outcome: 'action_dispatched',
        action: operation,
        url: sanitizedUrl(parseNavigationStatusUrl(tab)),
        task_completion_verified: false,
        requires_fresh_snapshot: true,
      };
    }

    throw new BrowserError('unsupported_capability', 'Browser operation is unavailable.');
  }

  private async _capture(
    session: EmbeddedSessionState,
    tab: EmbeddedTabState,
    options: { fullPage?: boolean; host?: boolean },
  ): Promise<Uint8Array> {
    if (!session.grant.allow_screenshots) {
      throw new BrowserError('permission_denied', 'Browser screenshot permission is disabled.');
    }
    const url = parseNavigationStatusUrl(tab);
    if (url !== 'about:blank') {
      await this.policy.validate(url, session.grant);
    }
    return tab.driver.captureMaskedScreenshot({
      fullPage: options.fullPage,
      host: options.host,
      maxBytes: this.config.max_screenshot_bytes,
    });
  }

  async preview(identity: BrowserIdentity, grant: BrowserGrant, fullPage = false): Promise<Uint8Array> {
    if (!READS.has('screenshot')) {
      throw new BrowserError('unsupported_capability', 'Screenshots unavailable.');
    }
    const session = this._session(identity, grant);
    if (session.state === 'manual_control') {
      throw new BrowserError('manual_control', 'Agent observation is paused during manual control.');
    }
    const tab = this._tab(session, {});
    return tab.lock.run(() => this._capture(session, tab, { fullPage, host: false }));
  }

  async hostPreview(identity: BrowserIdentity, grant: BrowserGrant): Promise<Uint8Array> {
    const session = this._session(identity, grant);
    const tab = this._tab(session, {});
    return tab.lock.run(() => this._capture(session, tab, { fullPage: false, host: true }));
  }

  async hostInput(
    identity: BrowserIdentity,
    grant: BrowserGrant,
    kind: string,
    rawArgs?: Record<string, unknown> | null,
  ): Promise<Record<string, unknown>> {
    const args = assertValidHostInputShape(kind, rawArgs);
    const initialSession = this._session(identity, grant);
    return initialSession.control_lock.run(async () => {
      const session = this._session(identity, grant);
      if (session.state !== 'manual_control') {
        throw new BrowserError(
          'host_action_required',
          'Take manual control before interacting with the browser view.',
        );
      }
      const tab = this._tab(session, {});
      return tab.lock.run(async () => {
        let current = this._session(identity, grant);
        if (current !== session || current.state !== 'manual_control') {
          throw new BrowserError(
            'host_action_required',
            'Manual browser ownership changed while input was waiting.',
          );
        }
        const checkedGrant = current.grant;
        const url = parseNavigationStatusUrl(tab);
        if (url !== 'about:blank') {
          await this.policy.validate(url, checkedGrant);
        }
        current = this._session(identity, grant);
        if (
          current !== session ||
          current.state !== 'manual_control' ||
          !grantsEqual(current.grant, checkedGrant)
        ) {
          throw new BrowserError('permission_changed', 'Browser input authority changed during validation.');
        }

        const parsedInput = validateHostInputArgs(kind, args, tab.viewport || session.viewport);
        if (parsedInput.kind === 'click') {
          await tab.driver.clickAt(parsedInput.x, parsedInput.y);
        } else if (parsedInput.kind === 'scroll') {
          await tab.driver.scroll(parsedInput.deltaX, parsedInput.deltaY, tab.viewport);
        } else if (parsedInput.kind === 'key') {
          await tab.driver.pressKey(parsedInput.key);
        } else if (parsedInput.kind === 'text') {
          await tab.driver.insertText(parsedInput.text);
        }
        this._invalidate(tab);
        session.last_activity = Date.now() / 1000;
        return this._status(session, { host: true });
      });
    });
  }

  async resume(identity: BrowserIdentity, grant: BrowserGrant): Promise<Record<string, unknown>> {
    const session = this._session(identity, grant);
    if (session.state !== 'manual_control') {
      throw new BrowserError('invalid_state', 'The browser is not waiting for manual control.');
    }
    const tab = this._tab(session, {});
    await tab.lock.run(async () => {
      let current = this._session(identity, grant);
      if (current !== session || current.state !== 'manual_control') {
        throw new BrowserError('invalid_state', 'Manual browser ownership changed while resuming.');
      }
      const checkedGrant = current.grant;
      const url = parseNavigationStatusUrl(tab);
      if (url !== 'about:blank') {
        await this.policy.validate(url, checkedGrant);
      }
      current = this._session(identity, grant);
      if (current !== session || !grantsEqual(current.grant, checkedGrant)) {
        throw new BrowserError('permission_changed', 'Browser authority changed while resuming.');
      }
      for (const item of session.tabs.values()) {
        this._invalidate(item);
        item.console.clear();
        item.page_errors.clear();
        item.network_errors.clear();
      }
      session.state = 'ready';
    });

    let result: Record<string, unknown> = {};
    for (let attempt = 0; attempt < 3; attempt++) {
      try {
        result = await this.dispatch(identity, grant, 'snapshot');
        break;
      } catch (err) {
        if (!(err instanceof BrowserError) || err.code !== 'stale_observation') {
          throw err;
        }
        if (attempt === 2) {
          result = {
            ...this._status(this._session(identity, grant)),
            observation_pending: true,
            requires_fresh_snapshot: true,
          };
        } else {
          await new Promise(resolve => setTimeout(resolve, 50));
        }
      }
    }
    result.resumed_by_host = true;
    return result;
  }

  sessions(identity: BrowserIdentity): Record<string, unknown>[] {
    const key = identityKey(validateIdentity(identity));
    const session = this._sessions.get(key);
    return session ? [this._status(session)] : [];
  }

  artifactPath(identity: BrowserIdentity, artifactId: string): string {
    return this.artifacts.path(identity, artifactId);
  }

  private async _destroyTab(session: EmbeddedSessionState, tab: EmbeddedTabState): Promise<void> {
    tab.closed = true;
    this._tabsByWcId.delete(tab.webContents.id);
    session.tabs.delete(tab.tab_id);
    if (session.active_tab_id === tab.tab_id) {
      session.active_tab_id = session.tabs.keys().next().value ?? '';
    }
    tab.driver.detach();
    const parent = this._options.getParentWindow?.();
    if (parent && !parent.isDestroyed()) {
      try {
        tab.view.setVisible(false);
        parent.contentView.removeChildView(tab.view);
      } catch {
        // Ignore already detached child view
      }
    }
    if (!tab.webContents.isDestroyed()) {
      try {
        tab.webContents.stop();
      } catch {
        // Ignore stop errors during teardown
      }
      try {
        if (typeof tab.webContents.destroy === 'function') {
          tab.webContents.destroy();
        } else {
          tab.webContents.close({ waitForBeforeUnload: false });
        }
      } catch {
        // Ignore already destroyed webContents
      }
    }
  }

  private async _closeSession(session: EmbeddedSessionState): Promise<void> {
    this._emitHiddenPointer(session.identity.conversation_id);
    for (const tab of [...session.tabs.values()]) {
      await this._destroyTab(session, tab);
    }
    try {
      session.electronSession.webRequest.onBeforeRequest(null);
      session.electronSession.webRequest.onBeforeSendHeaders(null);
      session.electronSession.webRequest.onHeadersReceived(null);
      session.electronSession.webRequest.onCompleted(null);
      session.electronSession.webRequest.onErrorOccurred(null);
      session.electronSession.setPermissionCheckHandler(null);
      session.electronSession.setPermissionRequestHandler(null);
    } catch {
      // Ignore handler cleanup issues
    }
    await session.electronSession.clearStorageData().catch(() => {});
    session.state = 'closed';
    this._sessions.delete(session.identity_key);
    this.artifacts.removeOwner(session.identity);
    this._syncPresentation();
  }

  async closeOwner(identity: BrowserIdentity): Promise<void> {
    const checked = validateIdentity(identity);
    const key = identityKey(checked);
    const session = this._sessions.get(key);
    if (!session) {
      this._governance.removeOwner(key);
      return;
    }
    session.cancelled = true;
    session.state = 'closing';
    this._governance.clearOwnerApprovals(key);
    for (const pending of [...session.pending]) {
      pending.cancel();
    }
    if (this._sessions.has(key)) {
      await this._closeSession(session);
    }
    this._governance.removeOwner(key);
  }

  async closeAll(): Promise<void> {
    for (const session of [...this._sessions.values()]) {
      await this.closeOwner(session.identity);
    }
    this._governance.clearAll();
    this._navigationErrors.clear();
    this._redirectCounts.clear();
    this._redirectedRequests.clear();
    this._cursorOverlay.destroy();
    this.artifacts.close();
  }
}
