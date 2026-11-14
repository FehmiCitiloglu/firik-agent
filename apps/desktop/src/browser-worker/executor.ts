import { existsSync } from 'node:fs';
import type { BrowserPointerAction, BrowserPointerState } from '@firik/contracts/desktop-api';
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
} from './models.js';
import { BrowserSecurityPolicy, originOf, redact, sanitizedUrl } from './security.js';
import {
  ALLOWED_HOST_OPS,
  BrowserArtifacts,
  BrowserGrantGovernance,
  FORM_OBSERVATION_SCRIPT,
  INTERACTIONS,
  NAVIGATIONS,
  POINTER_ACTION_LABELS,
  READS,
  TARGET_STATE_SCRIPT,
  assertBrowserEnabled,
  assertValidHostInputShape,
  boundedDiagnosticItems,
  computePointerTrajectory,
  hashValue,
  normalizeBrowserArguments,
  pageFunction,
  validateHostInputArgs,
  validateTargetState,
  type BrowserArtifact,
  type PreparedAction,
} from './shared.js';

export {
  BrowserArtifacts,
  FORM_OBSERVATION_SCRIPT,
  INTERACTIONS,
  NAVIGATIONS,
  READS,
  TARGET_STATE_SCRIPT,
  hashValue,
  pageFunction,
  type BrowserArtifact,
};

export interface BrowserTabState {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  page: any;
  tab_id: string;
  title: string;
  revision: number;
  observed_digest: string;
  lock: AsyncMutex;
  console: DiagnosticBuffer;
  page_errors: DiagnosticBuffer;
  network_errors: DiagnosticBuffer;
  pointer?: { x: number; y: number };
}

export interface BrowserSessionState {
  identity: BrowserIdentity;
  identity_key: string;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  context: any;
  grant: BrowserGrant;
  session_id: string;
  context_id: string;
  state: string;
  tabs: Map<string, BrowserTabState>;
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

export interface BrowserExecutor {
  readonly config: BrowserConfig;
  readonly policy: BrowserSecurityPolicy;
  readonly artifacts: BrowserArtifacts;
  bindGrant(identity: BrowserIdentity, grant: BrowserGrant): void;
  bindGrantProvider(identity: BrowserIdentity, provider: () => BrowserGrant): void;
  prepare(
    identity: BrowserIdentity,
    grant: BrowserGrant,
    operation: string,
    args?: Record<string, unknown>,
  ): Promise<Record<string, unknown>>;
  dispatch(
    identity: BrowserIdentity,
    grant: BrowserGrant,
    operation: string,
    args?: Record<string, unknown> | null,
    options?: { approvedFingerprint?: string | null; hostControl?: boolean; signal?: AbortSignal },
  ): Promise<Record<string, unknown>>;
  hostCommand(
    identity: BrowserIdentity,
    grant: BrowserGrant,
    operation: string,
    args?: Record<string, unknown> | null,
  ): Promise<Record<string, unknown>>;
  hostInput(
    identity: BrowserIdentity,
    grant: BrowserGrant,
    kind: string,
    args?: Record<string, unknown> | null,
  ): Promise<Record<string, unknown>>;
  resume(identity: BrowserIdentity, grant: BrowserGrant): Promise<Record<string, unknown>>;
  preview(identity: BrowserIdentity, grant: BrowserGrant, fullPage?: boolean): Promise<Uint8Array>;
  hostPreview(identity: BrowserIdentity, grant: BrowserGrant): Promise<Uint8Array>;
  sessions(identity: BrowserIdentity): Record<string, unknown>[];
  artifactPath(identity: BrowserIdentity, artifactId: string): string;
  closeOwner(identity: BrowserIdentity): Promise<void>;
  closeAll(): Promise<void>;
  setPointerSink?(sink: ((pointer: BrowserPointerState) => void) | undefined): void;
}

// The driver chooses a revision from its own browsers.json. Never substitute a
// browser cached by another Playwright/Python installation.
export function resolveChromiumExecutable(defaultExecutable: string): string | undefined {
  return defaultExecutable && existsSync(defaultExecutable) ? defaultExecutable : undefined;
}

// Helpers to bridge camelCase Playwright JS and snake_case test doubles seamlessly
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function pageUrl(page: any): string {
  return typeof page.url === 'function' ? page.url() : String(page.url ?? 'about:blank');
}
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function pageIsClosed(page: any): boolean {
  if (typeof page.isClosed === 'function') return Boolean(page.isClosed());
  if (typeof page.is_closed === 'function') return Boolean(page.is_closed());
  return false;
}
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function pageMainFrame(page: any): any {
  return typeof page.mainFrame === 'function' ? page.mainFrame() : page.main_frame ?? page;
}
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function pageFrames(page: any): any[] {
  const frames = typeof page.frames === 'function' ? page.frames() : page.frames;
  return Array.isArray(frames) ? frames : [pageMainFrame(page)];
}
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function frameUrl(frame: any): string {
  return typeof frame?.url === 'function' ? frame.url() : String(frame?.url ?? 'about:blank');
}

export class PlaywrightBrowserExecutor implements BrowserExecutor {
  readonly config: BrowserConfig;
  readonly policy: BrowserSecurityPolicy;
  readonly artifacts: BrowserArtifacts;
  readonly _sessions = new Map<string, BrowserSessionState>();
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  readonly _browsers = new Map<boolean, any>();
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  _playwright: any = null;
  private readonly _startupLock = new AsyncMutex();
  private readonly _governance = new BrowserGrantGovernance();
  readonly _prepared: Map<string, PreparedAction> = this._governance.prepared;
  readonly _grantProviders: Map<string, () => BrowserGrant> = this._governance.grantProviders;
  private readonly _redirectTasks = new Map<string, Promise<unknown>>();
  private readonly _redirectCounts = new Map<string, number>();
  private readonly _navigationErrors = new Map<string, BrowserError>();
  private _pointerSink?: (pointer: BrowserPointerState) => void;
  readonly metrics = { calls: 0, startup_seconds: 0, action_seconds: 0, crashes: 0 };

  constructor(config?: Partial<BrowserConfig>, policy?: BrowserSecurityPolicy) {
    this.config = defaultBrowserConfig(config);
    this.policy = policy ?? new BrowserSecurityPolicy();
    this.artifacts = new BrowserArtifacts();
  }

  setPointerSink(sink: ((pointer: BrowserPointerState) => void) | undefined): void {
    this._pointerSink = sink;
  }

  private _emitPointer(
    session: BrowserSessionState,
    x: number,
    y: number,
    action: BrowserPointerAction,
    pressing: boolean,
    visible = true,
  ): void {
    this._pointerSink?.({
      conversation_id: session.identity.conversation_id,
      visible,
      x: Math.round(x),
      y: Math.round(y),
      viewport_width: Math.max(1, session.viewport.width),
      viewport_height: Math.max(1, session.viewport.height),
      action,
      pressing,
      label: visible ? POINTER_ACTION_LABELS[action] : undefined,
    });
  }

  private async _movePointerSmoothly(
    session: BrowserSessionState,
    tab: BrowserTabState,
    targetX: number,
    targetY: number,
    action: BrowserPointerAction,
  ): Promise<void> {
    const start = tab.pointer ?? {
      x: Math.max(24, Math.min(240, Math.round(targetX * 0.35))),
      y: Math.max(24, Math.min(180, Math.round(targetY * 0.35))),
    };
    const points = computePointerTrajectory(start, { x: targetX, y: targetY });
    for (let i = 0; i < points.length; i++) {
      if (session.cancelled || pageIsClosed(tab.page)) break;
      const pt = points[i];
      if (typeof tab.page?.mouse?.move === 'function') {
        await tab.page.mouse.move(pt.x, pt.y);
      }
      tab.pointer = pt;
      this._emitPointer(session, pt.x, pt.y, action, false, true);
      if (i < points.length - 1) {
        await new Promise(resolve => setTimeout(resolve, 12));
      }
    }
  }

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  private async _locatePointerTarget(
    session: BrowserSessionState,
    tab: BrowserTabState,
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    locator: any,
    action: BrowserPointerAction,
  ): Promise<{ x: number; y: number } | null> {
    if (typeof locator?.boundingBox !== 'function') return null;
    const box = await locator.boundingBox().catch(() => null);
    if (!box || box.width <= 0 || box.height <= 0) return null;
    const x = Math.round(box.x + box.width / 2);
    const y = Math.round(box.y + box.height / 2);
    await this._movePointerSmoothly(session, tab, x, y, action);
    return { x, y };
  }

  bindGrant(identity: BrowserIdentity, grant: BrowserGrant): void {
    this._governance.bindGrant(identity, grant);
  }

  bindGrantProvider(identity: BrowserIdentity, provider: () => BrowserGrant): void {
    this._governance.bindGrantProvider(identity, provider);
  }

  _liveGrant(identity: BrowserIdentity, fallback: BrowserGrant): BrowserGrant {
    return this._governance.liveGrant(identity, fallback);
  }

  private _enabled(grant: BrowserGrant): void {
    assertBrowserEnabled(this.config, grant);
  }

  _arguments(operation: string, rawArgs?: Record<string, unknown> | null): [string, Record<string, unknown>] {
    return normalizeBrowserArguments(operation, rawArgs, this.config.action_timeout_ms);
  }

  _session(identity: BrowserIdentity, grant: BrowserGrant): BrowserSessionState {
    const checkedIdentity = validateIdentity(identity);
    const key = identityKey(checkedIdentity);
    const liveGrant = this._liveGrant(checkedIdentity, grant);
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

  _tab(session: BrowserSessionState, args: Record<string, unknown>): BrowserTabState {
    const tabId = (args.tab_id as string | undefined) || session.active_tab_id;
    const tab = session.tabs.get(tabId);
    if (!tab || pageIsClosed(tab.page)) {
      throw new BrowserError(
        'ownership_denied',
        'The tab is closed or does not belong to this execution identity.',
      );
    }
    return tab;
  }

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  async _browser(headless: boolean, signal?: AbortSignal): Promise<any> {
    const existing = this._browsers.get(headless);
    if (existing) {
      const connected =
        typeof existing.isConnected === 'function'
          ? existing.isConnected()
          : typeof existing.is_connected === 'function'
            ? existing.is_connected()
            : true;
      if (!connected) {
        this._browsers.delete(headless);
      } else {
        return existing;
      }
    }
    const started = performance.now();
    try {
      if (!this._playwright) {
        const pw = await import('playwright');
        this._playwright = pw;
      }
      const chromium = this._playwright.chromium;
      const defaultExec = typeof chromium.executablePath === 'function' ? chromium.executablePath() : '';
      const executablePath = resolveChromiumExecutable(defaultExec);
      if (typeof chromium.executablePath === 'function' && !executablePath) {
        throw new BrowserError('browser_binary_missing', 'Matching Chromium is missing. Run npm run desktop:browser:install and rebuild the desktop package.');
      }
      const launchPromise = chromium.launch({
        headless,
        chromiumSandbox: true,
        timeout: this.config.launch_timeout_ms,
        ...(executablePath ? { executablePath } : {}),
      });
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      let browser: any;
      if (signal) {
        if (signal.aborted) {
          const err = new Error('Cancelled');
          err.name = 'AbortError';
          throw err;
        }
        browser = await Promise.race([
          launchPromise,
          new Promise<never>((_, reject) => {
            signal.addEventListener(
              'abort',
              () => {
                const err = new Error('Cancelled');
                err.name = 'AbortError';
                reject(err);
              },
              { once: true },
            );
          }),
        ]);
      } else {
        browser = await launchPromise;
      }
      this._browsers.set(headless, browser);
      if (typeof browser.on === 'function') {
        browser.on('disconnected', () => this._disconnected(headless));
      }
      this.metrics.startup_seconds += (performance.now() - started) / 1000;
      return browser;
    } catch (exc: unknown) {
      if (this._playwright) {
        try {
          if (typeof this._playwright.stop === 'function') {
            await this._playwright.stop();
          }
        } finally {
          this._playwright = null;
          this._browsers.clear();
          for (const active of this._sessions.values()) {
            active.state = 'failed';
            active.cancelled = true;
          }
        }
      }
      if (exc instanceof Error && exc.name === 'AbortError') {
        throw exc;
      }
      if (exc instanceof BrowserError) throw exc;
      throw new BrowserError('browser_launch_failed', 'Chromium could not start. Check the matching hybrid desktop browser installation.');
    }
  }

  _disconnected(headless: boolean): void {
    let affected = false;
    for (const session of this._sessions.values()) {
      if (session.headless === headless && !['closing', 'closed'].includes(session.state)) {
        affected = true;
        session.state = 'failed';
        session.cancelled = true;
      }
    }
    if (affected) {
      this.metrics.crashes += 1;
    }
  }

  async _start(
    identity: BrowserIdentity,
    grant: BrowserGrant,
    args: Record<string, unknown>,
  ): Promise<Record<string, unknown>> {
    const checkedIdentity = validateIdentity(identity);
    const key = identityKey(checkedIdentity);
    const liveGrant = this._liveGrant(checkedIdentity, grant);
    this._enabled(liveGrant);
    return this._startupLock.run(async () => {
      if (this._sessions.has(key)) {
        const previous = this._sessions.get(key)!;
        if (previous.cancelled || ['closed', 'failed'].includes(previous.state)) {
          const provider = this._grantProviders.get(key);
          await this.closeOwner(checkedIdentity);
          if (provider) {
            this._grantProviders.set(key, provider);
          }
        } else {
          const session = this._session(checkedIdentity, liveGrant);
          return this._status(session);
        }
      }
      if (this._sessions.size >= this.config.max_sessions) {
        throw new BrowserError('browser_limit', 'Maximum number of concurrent browser sessions reached.');
      }
      const headless = typeof args.headless === 'boolean' ? args.headless : this.config.headless;
      const browser = await this._browser(headless);
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      let context: any = null;
      try {
        const newContextFn = browser.newContext ?? browser.new_context;
        const viewport = {
          width: typeof args.width === 'number' ? args.width : this.config.viewport_width,
          height: typeof args.height === 'number' ? args.height : this.config.viewport_height,
        };
        context = await newContextFn.call(browser, {
          viewport,
          locale: resolveBrowserLocale(this.config.locale),
          timezoneId: resolveBrowserTimezone(this.config.timezone_id),
          acceptDownloads: false,
          serviceWorkers: 'block',
          bypassCSP: false,
        });
        const setTimeoutFn = context.setDefaultTimeout ?? context.set_default_timeout;
        if (typeof setTimeoutFn === 'function') {
          setTimeoutFn.call(context, this.config.action_timeout_ms);
        }
        const setNavTimeoutFn = context.setDefaultNavigationTimeout ?? context.set_default_navigation_timeout;
        if (typeof setNavTimeoutFn === 'function') {
          setNavTimeoutFn.call(context, this.config.navigation_timeout_ms);
        }
        const session: BrowserSessionState = {
          identity: checkedIdentity,
          identity_key: key,
          context,
          grant: liveGrant,
          session_id: randomId('browser'),
          context_id: randomId('context'),
          state: 'starting',
          tabs: new Map(),
          active_tab_id: '',
          created_at: Date.now() / 1000,
          last_activity: Date.now() / 1000,
          actions: 0,
          cancelled: false,
          headless,
          viewport,
          pending: new Set(),
          control_lock: new AsyncMutex(),
        };
        await context.route('**/*', (route: unknown) => this._route(session, route));
        const routeWs = context.routeWebSocket ?? context.route_web_socket;
        if (typeof routeWs !== 'function') {
          throw new BrowserError(
            'unsupported_capability',
            'Installed Playwright cannot enforce WebSocket restrictions.',
          );
        }
        await routeWs.call(context, '**/*', (ws: { close: (opts?: unknown) => void }) => {
          try {
            ws.close({ code: 1008, reason: 'Browser WebSockets disabled by host policy' });
          } catch {
            ws.close();
          }
        });
        if (typeof context.on === 'function') {
          context.on('page', (page: unknown) => this._trackPage(session, page));
        }
        const newPageFn = context.newPage ?? context.new_page;
        const page = await newPageFn.call(context);
        if (session.tabs.size === 0) {
          this._trackPage(session, page);
        }
        session.state = 'ready';
        this._sessions.set(key, session);
        return this._status(session);
      } catch (err) {
        if (context && typeof context.close === 'function') {
          try {
            await context.close();
          } catch {
            // Ignore cleanup failure on partial start
          }
        }
        throw err;
      }
    });
  }

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  async _route(session: BrowserSessionState, route: any): Promise<void> {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    let response: any = null;
    const req = typeof route.request === 'function' ? route.request() : route.request;
    const reqUrl = (): string => (typeof req.url === 'function' ? req.url() : String(req.url));
    const reqMethod = (): string => (typeof req.method === 'function' ? req.method() : String(req.method ?? 'GET'));
    const reqResourceType = (): string =>
      typeof req.resourceType === 'function' ? req.resourceType() : String(req.resource_type ?? 'document');
    const reqFrame = () => (typeof req.frame === 'function' ? req.frame() : req.frame);
    const reqIsNav = (): boolean =>
      typeof req.isNavigationRequest === 'function'
        ? Boolean(req.isNavigationRequest())
        : typeof req.is_navigation_request === 'function'
          ? Boolean(req.is_navigation_request())
          : true;

    try {
      if (session.cancelled || ['closing', 'closed', 'failed'].includes(session.state)) {
        throw new BrowserError('cancelled', 'Browser no longer active.');
      }
      session.grant = this._liveGrant(session.identity, session.grant);
      this._enabled(session.grant);
      const checkedGrant = session.grant;
      await this.policy.validate(reqUrl(), checkedGrant);
      let currentGrant = this._liveGrant(session.identity, session.grant);
      this._enabled(currentGrant);
      if (!grantsEqual(currentGrant, checkedGrant)) {
        throw new BrowserError(
          'permission_changed',
          'Browser authority changed during destination validation.',
        );
      }
      session.grant = currentGrant;

      response = await route.fetch({ maxRedirects: 0, timeout: this.config.navigation_timeout_ms });
      currentGrant = this._liveGrant(session.identity, session.grant);
      this._enabled(currentGrant);
      if (!grantsEqual(currentGrant, checkedGrant)) {
        throw new BrowserError(
          'permission_changed',
          'Browser authority changed during the request; its response was discarded.',
        );
      }

      let redirects = 0;
      let destination = reqUrl();
      const respStatus = () => (typeof response.status === 'function' ? response.status() : response.status);
      const respHeaders = (): Record<string, string> =>
        typeof response.headers === 'function' ? response.headers() : (response.headers ?? {});

      while ([301, 302, 303, 307, 308].includes(respStatus()) && respHeaders().location) {
        destination = new URL(respHeaders().location, destination).toString();
        await this.policy.validate(destination, checkedGrant);
        currentGrant = this._liveGrant(session.identity, session.grant);
        this._enabled(currentGrant);
        if (!grantsEqual(currentGrant, checkedGrant)) {
          throw new BrowserError(
            'permission_changed',
            'Browser authority changed during redirect validation.',
          );
        }
        const method = reqMethod();
        const status = respStatus();
        const safeGetTransition = method === 'POST' && (status === 302 || status === 303);
        if (method !== 'GET' && method !== 'HEAD' && !safeGetTransition) {
          throw new BrowserError(
            'redirect_blocked',
            'A submitted request is not replayed automatically after a redirect.',
          );
        }
        redirects += 1;
        if (redirects > 10) {
          throw new BrowserError('redirect_limit', 'The website redirected too many times.');
        }
        const frame = reqFrame();
        const page = typeof frame?.page === 'function' ? frame.page() : frame?.page;
        const tab = [...session.tabs.values()].find(item => item.page === page);
        if (reqIsNav() && page && frame === pageMainFrame(page) && tab) {
          const count = (this._redirectCounts.get(tab.tab_id) ?? 0) + 1;
          this._redirectCounts.set(tab.tab_id, count);
          if (count > 10) {
            throw new BrowserError('redirect_limit', 'The website redirected too many times.');
          }
          let releaseReady!: () => void;
          const readyPromise = new Promise<void>(resolve => {
            releaseReady = resolve;
          });
          const task = this._followRedirect(session, tab, destination, readyPromise);
          this._redirectTasks.set(tab.tab_id, task);
          task.catch(() => {});
          try {
            await route.abort('aborted');
          } finally {
            releaseReady();
          }
          return;
        }
        if (safeGetTransition) {
          throw new BrowserError(
            'redirect_blocked',
            'A submitted resource redirect requires native browser origin handling.',
          );
        }
        if (originOf(destination) !== originOf(reqUrl())) {
          throw new BrowserError(
            'cross_origin_redirect_blocked',
            'A redirected resource changed origin and could not be loaded safely.',
          );
        }
        if (typeof response.dispose === 'function') {
          await response.dispose();
        }
        response = await route.fetch({
          url: destination,
          maxRedirects: 0,
          timeout: this.config.navigation_timeout_ms,
        });
      }

      currentGrant = this._liveGrant(session.identity, session.grant);
      this._enabled(currentGrant);
      if (!grantsEqual(currentGrant, checkedGrant)) {
        throw new BrowserError(
          'permission_changed',
          'Browser authority changed while loading the response.',
        );
      }
      await route.fulfill({ response });
    } catch (exc: unknown) {
      if (exc instanceof BrowserError) {
        for (const tab of session.tabs.values()) {
          tab.network_errors.append({
            url: sanitizedUrl(reqUrl()),
            method: reqMethod(),
            resource_type: reqResourceType(),
            failure: exc.code,
          });
        }
        const frame = reqFrame();
        const page = typeof frame?.page === 'function' ? frame.page() : frame?.page;
        const mainNavigation = Boolean(reqIsNav() && page && frame === pageMainFrame(page));
        if (mainNavigation && page) {
          const tab = [...session.tabs.values()].find(item => item.page === page);
          if (tab) {
            const submitted = response !== null && !['GET', 'HEAD'].includes(reqMethod());
            this._navigationErrors.set(
              tab.tab_id,
              new BrowserError(exc.code, exc.message, {
                dispatched: exc.dispatched || submitted,
                requires_reconciliation: exc.requires_reconciliation || submitted,
              }),
            );
          }
        }
        await route.abort(mainNavigation ? 'aborted' : 'blockedbyclient');
      } else {
        try {
          await route.abort('blockedbyclient');
        } catch {
          // Ignore secondary abort errors
        }
      }
    } finally {
      if (response && typeof response.dispose === 'function') {
        try {
          await response.dispose();
        } catch {
          // Ignore dispose errors
        }
      }
    }
  }

  private async _followRedirect(
    session: BrowserSessionState,
    tab: BrowserTabState,
    destination: string,
    ready: Promise<void>,
  ): Promise<unknown> {
    await ready;
    const grant = this._liveGrant(session.identity, session.grant);
    this._enabled(grant);
    await this.policy.validate(destination, grant);
    try {
      return await tab.page.goto(destination, { waitUntil: 'domcontentloaded' });
    } catch (err) {
      const following = this._redirectTasks.get(tab.tab_id);
      if (following) {
        return await following;
      }
      throw err;
    }
  }

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  _trackPage(session: BrowserSessionState, page: any): void {
    if ([...session.tabs.values()].some(t => t.page === page)) {
      return;
    }
    if (session.tabs.size >= this.config.max_tabs_per_session) {
      if (typeof page.close === 'function') {
        void Promise.resolve(page.close()).catch(() => {});
      }
      return;
    }
    const tab: BrowserTabState = {
      page,
      tab_id: randomId('tab'),
      title: '',
      revision: 0,
      observed_digest: '',
      lock: new AsyncMutex(),
      console: new DiagnosticBuffer(this.config.max_diagnostics),
      page_errors: new DiagnosticBuffer(this.config.max_diagnostics),
      network_errors: new DiagnosticBuffer(this.config.max_diagnostics),
    };
    session.tabs.set(tab.tab_id, tab);
    if (!session.active_tab_id) {
      session.active_tab_id = tab.tab_id;
    }
    if (typeof page.on !== 'function') {
      return;
    }
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    page.on('console', (msg: any) => {
      const type = typeof msg.type === 'function' ? msg.type() : msg.type;
      const text = typeof msg.text === 'function' ? msg.text() : msg.text;
      if ((type === 'error' || type === 'warning') && session.state !== 'manual_control') {
        tab.console.append({ type, text: redact(text), untrusted: true });
      }
    });
    page.on('pageerror', (error: unknown) => {
      if (session.state !== 'manual_control') {
        tab.page_errors.append({ text: redact(String(error)), untrusted: true });
      }
    });
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    page.on('requestfailed', (req: any) => {
      if (session.state !== 'manual_control') {
        const method = typeof req.method === 'function' ? req.method() : req.method;
        const url = typeof req.url === 'function' ? req.url() : req.url;
        const resourceType = typeof req.resourceType === 'function' ? req.resourceType() : req.resource_type;
        const failureObj = typeof req.failure === 'function' ? req.failure() : req.failure;
        const failureText =
          typeof failureObj === 'object' && failureObj !== null
            ? (failureObj.errorText ?? 'failed')
            : String(failureObj || 'failed');
        tab.network_errors.append({
          method,
          url: sanitizedUrl(url),
          resource_type: resourceType,
          failure: redact(failureText),
        });
      }
    });
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    page.on('response', (resp: any) => {
      const status = typeof resp.status === 'function' ? resp.status() : resp.status;
      const url = typeof resp.url === 'function' ? resp.url() : resp.url;
      if (status >= 400 && session.state !== 'manual_control') {
        tab.network_errors.append({ url: sanitizedUrl(url), status });
      }
    });
    page.on('framenavigated', () => this._invalidate(tab));
    page.on('crash', () => this._pageCrash(session, tab));
    page.on('close', () => this._pageClosed(session, tab));
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    page.on('dialog', (dialog: any) => {
      if (session.state !== 'manual_control') {
        return dialog.dismiss();
      }
      return null;
    });
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    page.on('download', (download: any) => {
      return download.cancel();
    });
  }

  private _pageCrash(session: BrowserSessionState, tab: BrowserTabState): void {
    this._invalidate(tab);
    session.state = 'failed';
    this.metrics.crashes += 1;
  }

  private _pageClosed(session: BrowserSessionState, tab: BrowserTabState): void {
    this._redirectTasks.delete(tab.tab_id);
    this._redirectCounts.delete(tab.tab_id);
    this._navigationErrors.delete(tab.tab_id);
    session.tabs.delete(tab.tab_id);
    if (session.active_tab_id === tab.tab_id) {
      const firstKey = session.tabs.keys().next().value;
      session.active_tab_id = firstKey ?? '';
    }
    for (const [token, prep] of [...this._prepared.entries()]) {
      if (prep.tab_id === tab.tab_id && prep.identity_key === session.identity_key) {
        this._prepared.delete(token);
      }
    }
  }

  _invalidate(tab: BrowserTabState): void {
    tab.revision += 1;
    tab.observed_digest = '';
  }

  _status(session: BrowserSessionState, options?: { host?: boolean }): Record<string, unknown> {
    const browserObj = this._browsers.get(session.headless);
    const browserVersion = browserObj
      ? typeof browserObj.version === 'function'
        ? browserObj.version()
        : String(browserObj.version ?? '')
      : '';
    const activeTab = session.tabs.get(session.active_tab_id);
    const result: Record<string, unknown> = {
      session_id: session.session_id,
      context_id: session.context_id,
      state: session.state,
      conversation_id: session.identity.conversation_id,
      project_id: session.identity.project_id,
      run_id: session.identity.run_id,
      actor_id: session.identity.actor_id,
      active_tab_id: session.active_tab_id,
      tab_count: session.tabs.size,
      revision: activeTab?.revision ?? 0,
      headless: session.headless,
      created_at: session.created_at,
      last_activity: session.last_activity,
      browser_version: browserVersion,
      actions: session.actions,
      max_actions: this.config.max_actions_per_task,
      network_boundary: 'host_request_policy; not OS isolation',
      websockets: 'blocked',
      service_workers: 'blocked',
    };
    if (options?.host || session.state !== 'manual_control') {
      result.tabs = [...session.tabs.values()]
        .filter(tab => !pageIsClosed(tab.page))
        .map(tab => ({
          tab_id: tab.tab_id,
          url: sanitizedUrl(pageUrl(tab.page)),
          title: tab.title || '',
          active: tab.tab_id === session.active_tab_id,
        }));
    }
    return result;
  }

  _boundedItems(items: unknown[], key: string): Record<string, unknown> {
    return boundedDiagnosticItems(items, key, this.config.max_snapshot_chars);
  }

  sessions(identity: BrowserIdentity): Record<string, unknown>[] {
    const key = identityKey(validateIdentity(identity));
    const session = this._sessions.get(key);
    return session ? [this._status(session)] : [];
  }

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  async _digest(tab: BrowserTabState, frame?: any): Promise<string> {
    const frames = frame ? [frame] : pageFrames(tab.page);
    if (frames.length > 32) {
      throw new BrowserError('frame_limit', 'The document exceeds the supported observation frame limit.');
    }
    const contents: { url: string; content: string }[] = [];
    for (const current of frames) {
      const url = frameUrl(current);
      const content = typeof current.content === 'function' ? await current.content() : '';
      contents.push({ url, content });
    }
    return hashValue(contents);
  }

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  async _locator(session: BrowserSessionState, tab: BrowserTabState, target: Record<string, string>, requireReady = true): Promise<any> {
    let frame = pageMainFrame(tab.page);
    if (target.frame) {
      const candidates = pageFrames(tab.page).filter(f => frameUrl(f) === target.frame);
      if (candidates.length !== 1) {
        throw new BrowserError('ambiguous_target', 'Frame URL must uniquely identify an owned frame.');
      }
      frame = candidates[0];
    }
    const currentFrameUrl = frameUrl(frame);
    if (currentFrameUrl !== 'about:blank') {
      await this.policy.validate(currentFrameUrl, session.grant);
    }
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    let locator: any;
    if ('ref' in target) {
      const getByRef = tab.page.getByRef ?? tab.page.get_by_ref;
      if (frame !== pageMainFrame(tab.page) || typeof getByRef !== 'function') {
        throw new BrowserError(
          'unsupported_capability',
          'Native snapshot references require Playwright 1.64 or newer.',
        );
      }
      locator = getByRef.call(tab.page, target.ref);
    } else if ('role' in target) {
      const options = 'name' in target ? { name: target.name, exact: true } : {};
      const getByRole = frame.getByRole ?? frame.get_by_role;
      locator = getByRole.call(frame, target.role, options);
    } else if ('label' in target) {
      const getByLabel = frame.getByLabel ?? frame.get_by_label;
      locator = getByLabel.call(frame, target.label, { exact: true });
    } else if ('test_id' in target) {
      const getByTestId = frame.getByTestId ?? frame.get_by_test_id;
      locator = getByTestId.call(frame, target.test_id);
    } else {
      const getByText = frame.getByText ?? frame.get_by_text;
      locator = getByText.call(frame, target.text, { exact: true });
    }
    if (!requireReady) {
      return locator;
    }
    const count = await locator.count();
    if (count !== 1) {
      throw new BrowserError(
        count > 0 ? 'ambiguous_target' : 'target_missing',
        `Semantic target matched ${count} elements; refresh the snapshot.`,
      );
    }
    const isVisible = await (locator.isVisible ?? locator.is_visible).call(locator);
    const isEnabled = await (locator.isEnabled ?? locator.is_enabled).call(locator);
    if (!isVisible || !isEnabled) {
      throw new BrowserError('target_unavailable', 'The target must be visible and enabled.');
    }
    return locator;
  }

  private async _validateRevision(tab: BrowserTabState, revision: number): Promise<void> {
    if (revision !== tab.revision || !tab.observed_digest || (await this._digest(tab)) !== tab.observed_digest) {
      this._invalidate(tab);
      throw new BrowserError(
        'stale_observation',
        'The page changed. Capture a fresh snapshot before interacting.',
      );
    }
  }

  private async _actionState(
    session: BrowserSessionState,
    tab: BrowserTabState,
    operation: string,
    args: Record<string, unknown>,
  ): Promise<[string, Record<string, unknown>]> {
    await this._validateRevision(tab, args.revision as number);
    if (operation === 'scroll') {
      const state = { url: pageUrl(tab.page), revision: tab.revision, dom: await this._digest(tab) };
      const preview = { target: 'page viewport', form: [] };
      return [hashValue(state), preview];
    }
    const locator = await this._locator(session, tab, args.target as Record<string, string>);
    const state = (await locator.evaluate(pageFunction(TARGET_STATE_SCRIPT))) as Record<string, unknown>;
    const preview = await validateTargetState(
      state,
      args.target,
      this.policy,
      session.grant,
      this.config.max_snapshot_chars,
    );
    state.dom = await this._digest(tab);
    return [hashValue(state), preview];
  }

  async prepare(
    identity: BrowserIdentity,
    grant: BrowserGrant,
    operation: string,
    rawArgs?: Record<string, unknown>,
  ): Promise<Record<string, unknown>> {
    const [op, args] = this._arguments(operation, rawArgs);
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
          origin: originOf(pageUrl(tab.page)),
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
    const [op, args] = this._arguments(operation, rawArgs);
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
      return { state: 'uninitialized', tab_count: 0 };
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
      this._emitPointer(session, 0, 0, 'idle', false, false);
      for (const [token, prep] of [...this._prepared.entries()]) {
        if (prep.identity_key === key) {
          this._prepared.delete(token);
        }
      }
      for (const tab of session.tabs.values()) {
        await tab.lock.run(async () => {
          this._invalidate(tab);
          tab.console.clear();
          tab.page_errors.clear();
          tab.network_errors.clear();
        });
      }
      if (!session.headless) {
        const activeTab = this._tab(session, {});
        const bringToFront = activeTab.page.bringToFront ?? activeTab.page.bring_to_front;
        if (typeof bringToFront === 'function') {
          await bringToFront.call(activeTab.page);
        }
      }
      return this._status(session, { host: hostControl });
    }
    if (op === 'resize') {
      if (!hostControl) {
        throw new BrowserError('host_action_required', 'Only host browser controls may resize the viewport.');
      }
      const viewport = {
        width: args.width as number,
        height: args.height as number,
      };
      session.viewport = viewport;
      for (const openTab of session.tabs.values()) {
        if (pageIsClosed(openTab.page)) continue;
        await openTab.lock.run(async () => {
          const setVp = openTab.page.setViewportSize ?? openTab.page.set_viewport_size;
          if (typeof setVp === 'function') {
            await setVp.call(openTab.page, viewport);
          }
          this._invalidate(openTab);
        });
      }
      return this._status(session, { host: hostControl });
    }
    if (op === 'list_tabs') {
      const tabs = [];
      for (const tab of session.tabs.values()) {
        if (!pageIsClosed(tab.page)) {
          const title = typeof tab.page.title === 'function' ? redact(await tab.page.title()) : tab.title;
          tabs.push({
            tab_id: tab.tab_id,
            url: sanitizedUrl(pageUrl(tab.page)),
            title,
            active: tab.tab_id === session.active_tab_id,
          });
        }
      }
      return { tabs };
    }
    if (op === 'new_tab') {
      if (session.tabs.size >= this.config.max_tabs_per_session) {
        throw new BrowserError('browser_limit', 'Maximum tabs per session reached.');
      }
      if (typeof args.url === 'string' && args.url) {
        await this.policy.validate(args.url, liveGrant);
      }
      const newPageFn = session.context.newPage ?? session.context.new_page;
      const page = await newPageFn.call(session.context);
      if (
        session.viewport.width !== this.config.viewport_width ||
        session.viewport.height !== this.config.viewport_height
      ) {
        const setVp = page.setViewportSize ?? page.set_viewport_size;
        if (typeof setVp === 'function') {
          await setVp.call(page, session.viewport);
        }
      }
      let tab = [...session.tabs.values()].find(t => t.page === page);
      if (!tab) {
        this._trackPage(session, page);
        tab = [...session.tabs.values()].find(t => t.page === page)!;
      }
      session.active_tab_id = tab.tab_id;
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
      this._emitPointer(session, 0, 0, 'idle', false, false);
      return { tab_id: tab.tab_id, url: sanitizedUrl(pageUrl(tab.page)) };
    }
    if (op === 'close_tab') {
      return tab.lock.run(async () => {
        await tab.page.close();
        session.tabs.delete(tab.tab_id);
        if (session.active_tab_id === tab.tab_id) {
          session.active_tab_id = session.tabs.keys().next().value ?? '';
        }
        this._emitPointer(session, 0, 0, 'idle', false, false);
        return { closed_tab_id: tab.tab_id };
      });
    }

    let dispatched = false;
    const started = performance.now();
    this.metrics.calls += 1;
    const previousState = session.state;
    let abortReject: (() => void) | null = null;
    const pendingHandle = {
      cancel: () => {
        if (abortReject) abortReject();
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
        const currentTabUrl = pageUrl(tab.page);
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
    } catch (err: unknown) {
      if (err instanceof BrowserError) {
        throw err;
      }
      if (err instanceof Error && err.name === 'AbortError') {
        session.cancelled = true;
        session.state = 'closing';
        await this._closeContext(session);
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
      this.metrics.action_seconds += (performance.now() - started) / 1000;
    }
  }

  private async _execute(
    session: BrowserSessionState,
    tab: BrowserTabState,
    operation: string,
    args: Record<string, unknown>,
  ): Promise<Record<string, unknown>> {
    const page = tab.page;
    if (NAVIGATIONS.has(operation)) {
      this._emitPointer(session, 0, 0, 'idle', false, false);
      const requested = (args.url as string | undefined) ?? pageUrl(page);
      this._redirectCounts.set(tab.tab_id, 0);
      this._navigationErrors.delete(tab.tab_id);
      const previousRedirect = this._redirectTasks.get(tab.tab_id);
      this._redirectTasks.delete(tab.tab_id);
      if (previousRedirect) {
        try {
          await previousRedirect;
        } catch {
          // Ignore prior redirect completion error
        }
      }
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      let response: any = null;
      try {
        try {
          if (operation === 'navigate') {
            await this.policy.validate(requested, session.grant);
            response = await page.goto(requested, { waitUntil: 'domcontentloaded' });
          } else if (operation === 'back') {
            const goBack = page.goBack ?? page.go_back;
            response = await goBack.call(page, { waitUntil: 'domcontentloaded' });
          } else if (operation === 'forward') {
            const goForward = page.goForward ?? page.go_forward;
            response = await goForward.call(page, { waitUntil: 'domcontentloaded' });
          } else {
            response = await page.reload({ waitUntil: 'domcontentloaded' });
          }
        } catch (navErr) {
          const following = this._redirectTasks.get(tab.tab_id);
          if (!following) {
            throw navErr;
          }
          response = await following;
        }
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
      const finalUrl = pageUrl(page);
      if (finalUrl !== 'about:blank') {
        await this.policy.validate(finalUrl, session.grant);
      }
      tab.title = typeof page.title === 'function' ? redact(await page.title()) : '';
      const httpStatus = response
        ? typeof response.status === 'function'
          ? response.status()
          : (response.status ?? null)
        : null;
      const respReq = response
        ? typeof response.request === 'function'
          ? response.request()
          : response.request
        : null;
      const redirectedFrom = respReq
        ? typeof respReq.redirectedFrom === 'function'
          ? respReq.redirectedFrom()
          : respReq.redirected_from
        : null;
      return {
        requested_url: sanitizedUrl(requested),
        url: sanitizedUrl(finalUrl),
        title: tab.title,
        http_status: httpStatus,
        redirected: Boolean((this._redirectCounts.get(tab.tab_id) ?? 0) > 0 || redirectedFrom),
        outcome: 'navigation_observed',
        task_completion_verified: false,
      };
    }

    if (operation === 'snapshot') {
      const before = await this._digest(tab);
      const ariaSnapshotFn = page.ariaSnapshot ?? page.aria_snapshot;
      let text: string;
      if (typeof ariaSnapshotFn === 'function') {
        text = await ariaSnapshotFn.call(page, { mode: 'ai' });
      } else {
        const bodyLoc = page.locator('body');
        const bodyAria = bodyLoc.ariaSnapshot ?? bodyLoc.aria_snapshot;
        text = await bodyAria.call(bodyLoc);
      }
      text = text.replace(
        /(^\s*- (?:textbox|searchbox|combobox)[^\n]*?)(?:: .*)$/gm,
        '$1: [value omitted]',
      );
      text = redact(text, this.config.max_snapshot_chars + 1);
      const after = await this._digest(tab);
      if (before !== after) {
        this._invalidate(tab);
        throw new BrowserError(
          'stale_observation',
          'The page changed while its snapshot was captured; take a fresh observation.',
        );
      }
      tab.revision += 1;
      tab.observed_digest = after;
      const mainFrame = pageMainFrame(page);
      return {
        url: sanitizedUrl(pageUrl(page)),
        type: 'aria',
        captured_at: Date.now() / 1000,
        content: text.slice(0, this.config.max_snapshot_chars),
        truncated: text.length > this.config.max_snapshot_chars,
        untrusted: true,
        frames: pageFrames(page)
          .filter(f => f !== mainFrame)
          .map(f => ({ url: sanitizedUrl(frameUrl(f)) })),
      };
    }

    if (operation === 'extract_text') {
      const bodyLoc = page.locator('body');
      const innerTextFn = bodyLoc.innerText ?? bodyLoc.inner_text;
      const rawText = await innerTextFn.call(bodyLoc);
      const text = redact(rawText, this.config.max_snapshot_chars + 1);
      return {
        content: text.slice(0, this.config.max_snapshot_chars),
        truncated: text.length > this.config.max_snapshot_chars,
        untrusted: true,
      };
    }

    if (operation === 'get_links') {
      const getByRole = page.getByRole ?? page.get_by_role;
      const linksLocator = getByRole.call(page, 'link');
      const total = await linksLocator.count();
      const evalAll = linksLocator.evaluateAll ?? linksLocator.evaluate_all;
      const links = (await evalAll.call(
        linksLocator,
        "els => els.slice(0,200).map(e=>({text:(e.innerText||e.getAttribute('aria-label')||'').slice(0,300),url:(e.href||'').slice(0,8192)}))",
      )) as { text: string; url: string }[];
      const result = this._boundedItems(
        links.map(link => ({ text: redact(link.text), url: sanitizedUrl(link.url) })),
        'links',
      );
      result.truncated = Boolean(result.truncated) || total > links.length;
      return result;
    }

    if (operation === 'get_forms') {
      const formsLocator = page.locator('form');
      const total = await formsLocator.count();
      const evalAll = formsLocator.evaluateAll ?? formsLocator.evaluate_all;
      const forms = (await evalAll.call(formsLocator, pageFunction(FORM_OBSERVATION_SCRIPT))) as {
        method: string;
        action: string;
        fieldCount: number;
        fields: { name: string; label: string; tag: string; type: string; required: boolean; disabled: boolean }[];
      }[];
      for (const form of forms) {
        form.action = sanitizedUrl(form.action);
        for (const item of form.fields) {
          item.name = redact(item.name);
          item.label = redact(item.label);
        }
      }
      const result = this._boundedItems(forms, 'forms');
      result.truncated =
        Boolean(result.truncated) ||
        total > forms.length ||
        forms.some(f => f.fieldCount > f.fields.length);
      return result;
    }

    if (['get_console', 'get_page_errors', 'get_network_errors'].includes(operation)) {
      const bufferKey = operation.replace(/^get_/, '') as 'console' | 'page_errors' | 'network_errors';
      const buffer = tab[bufferKey];
      const result = this._boundedItems(buffer.toArray(), 'entries');
      result.truncated = buffer.dropped > 0 || Boolean(result.truncated);
      result.dropped_entries = buffer.dropped;
      return result;
    }

    if (operation === 'screenshot') {
      const data = await this._capture(session, tab, Boolean(args.full_page));
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
        const locator = await this._locator(session, tab, args.target as Record<string, string>, false);
        const waitForFn = locator.waitFor ?? locator.wait_for;
        await waitForFn.call(locator, {
          state: (args.state as string | undefined) ?? 'visible',
          timeout,
        });
      } else {
        const waitForLoad = page.waitForLoadState ?? page.wait_for_load_state;
        await waitForLoad.call(page, 'domcontentloaded', { timeout });
      }
      return { outcome: 'condition_observed' };
    }

    if (INTERACTIONS.has(operation)) {
      const locator =
        operation !== 'scroll'
          ? await this._locator(session, tab, args.target as Record<string, string>)
          : null;
      if (operation === 'click') {
        const pt = await this._locatePointerTarget(session, tab, locator, 'click');
        if (pt) this._emitPointer(session, pt.x, pt.y, 'click', true, true);
        await locator.click();
        if (pt) this._emitPointer(session, pt.x, pt.y, 'click', false, true);
      } else if (operation === 'fill') {
        const pt = await this._locatePointerTarget(session, tab, locator, 'fill');
        if (pt) this._emitPointer(session, pt.x, pt.y, 'fill', true, true);
        await locator.fill(args.value as string);
        if (pt) this._emitPointer(session, pt.x, pt.y, 'fill', false, true);
      } else if (operation === 'type') {
        const pt = await this._locatePointerTarget(session, tab, locator, 'type');
        if (pt) this._emitPointer(session, pt.x, pt.y, 'type', true, true);
        const pressSeq = locator.pressSequentially ?? locator.press_sequentially;
        const val = args.value as string;
        await pressSeq.call(locator, val, ...(val.length <= 200 ? [{ delay: 8 }] : []));
        if (pt) this._emitPointer(session, pt.x, pt.y, 'type', false, true);
      } else if (operation === 'press') {
        await locator.press(args.key as string);
      } else if (operation === 'select') {
        const pt = await this._locatePointerTarget(session, tab, locator, 'select');
        const selectOpt = locator.selectOption ?? locator.select_option;
        await selectOpt.call(locator, args.values as string[]);
        if (pt) this._emitPointer(session, pt.x, pt.y, 'select', false, true);
      } else if (operation === 'check') {
        const pt = await this._locatePointerTarget(session, tab, locator, 'check');
        if (pt) this._emitPointer(session, pt.x, pt.y, 'check', true, true);
        const setChecked = locator.setChecked ?? locator.set_checked;
        await setChecked.call(locator, typeof args.checked === 'boolean' ? args.checked : true);
        if (pt) this._emitPointer(session, pt.x, pt.y, 'check', false, true);
      } else if (operation === 'hover') {
        await this._locatePointerTarget(session, tab, locator, 'hover');
        await locator.hover();
      } else {
        const sx = tab.pointer?.x ?? Math.max(1, Math.floor(session.viewport.width / 2));
        const sy = tab.pointer?.y ?? Math.max(1, Math.floor(session.viewport.height / 2));
        await this._movePointerSmoothly(session, tab, sx, sy, 'scroll');
        await page.mouse.wheel(
          typeof args.x === 'number' ? args.x : 0,
          typeof args.y === 'number' ? args.y : 600,
        );
      }
      this._invalidate(tab);
      return {
        outcome: 'action_dispatched',
        action: operation,
        url: sanitizedUrl(pageUrl(page)),
        task_completion_verified: false,
        requires_fresh_snapshot: true,
      };
    }

    throw new BrowserError('unsupported_capability', 'Browser operation is unavailable.');
  }

  private _capturePermission(session: BrowserSessionState): void {
    session.grant = this._liveGrant(session.identity, session.grant);
    this._enabled(session.grant);
    if (!session.grant.allow_screenshots) {
      throw new BrowserError('permission_denied', 'Screenshot capture requires a separate host privacy grant.');
    }
    if (session.state === 'manual_control') {
      throw new BrowserError('manual_control', 'Preview is paused during manual authentication.');
    }
  }

  async _capture(session: BrowserSessionState, tab: BrowserTabState, fullPage = false): Promise<Uint8Array> {
    this._capturePermission(session);
    if (fullPage) {
      const dimensions = (await tab.page.evaluate(pageFunction(
        `() => ({width: Math.max(innerWidth, document.documentElement.scrollWidth, document.body?.scrollWidth || 0),
          height: Math.max(innerHeight, document.documentElement.scrollHeight, document.body?.scrollHeight || 0)})`,
      ))) as { width: number; height: number };
      if (dimensions.height > 4096 || dimensions.width > 2560) {
        throw new BrowserError(
          'artifact_limit',
          'Full-page screenshot exceeds the supported 2560 by 4096 CSS pixel bounds.',
        );
      }
    }
    const masks = pageFrames(tab.page).map(frame =>
      frame.locator('input, textarea, [contenteditable]'),
    );
    this._capturePermission(session);
    const raw = await tab.page.screenshot({
      type: 'png',
      fullPage,
      mask: masks,
      animations: 'disabled',
      timeout: this.config.action_timeout_ms,
    });
    const data = raw instanceof Uint8Array ? raw : new Uint8Array(raw);
    this._capturePermission(session);
    if (data.byteLength > this.config.max_screenshot_bytes) {
      throw new BrowserError('artifact_limit', 'Screenshot exceeds the configured artifact size limit.');
    }
    return data;
  }

  async preview(identity: BrowserIdentity, grant: BrowserGrant, fullPage = false): Promise<Uint8Array> {
    const session = this._session(identity, grant);
    const tab = this._tab(session, {});
    return tab.lock.run(() => this._capture(session, tab, fullPage));
  }

  async hostPreview(identity: BrowserIdentity, grant: BrowserGrant): Promise<Uint8Array> {
    const session = this._session(identity, grant);
    const tab = this._tab(session, {});
    return tab.lock.run(async () => {
      const currentSession = this._session(identity, grant);
      if (!currentSession.grant.allow_screenshots) {
        throw new BrowserError('permission_denied', 'Browser view requires the host preview permission.');
      }
      const checkedGrant = currentSession.grant;
      const currentUrl = pageUrl(tab.page);
      if (currentUrl !== 'about:blank') {
        await this.policy.validate(currentUrl, checkedGrant);
      }
      const afterValidate = this._session(identity, grant);
      if (afterValidate !== currentSession || !grantsEqual(afterValidate.grant, checkedGrant)) {
        throw new BrowserError('permission_changed', 'Browser preview authority changed during validation.');
      }
      const masks = pageFrames(tab.page).map(frame =>
        frame.locator(
          'input[type="password"], input[autocomplete="one-time-code"], input[autocomplete^="cc-"], input[name*="otp" i], input[name*="token" i]',
        ),
      );
      const raw = await tab.page.screenshot({
        type: 'png',
        mask: masks,
        animations: 'disabled',
        timeout: this.config.action_timeout_ms,
      });
      const data = raw instanceof Uint8Array ? raw : new Uint8Array(raw);
      const afterShot = this._session(identity, grant);
      if (!afterShot.grant.allow_screenshots) {
        throw new BrowserError('permission_denied', 'Browser preview permission was revoked during capture.');
      }
      if (data.byteLength > this.config.max_screenshot_bytes) {
        throw new BrowserError('artifact_limit', 'Browser view exceeds the configured image limit.');
      }
      return data;
    });
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
        const url = pageUrl(tab.page);
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
        this._redirectCounts.set(tab.tab_id, 0);
        const vp =
          (typeof tab.page.viewportSize === 'function'
            ? tab.page.viewportSize()
            : tab.page.viewport_size) ||
          session.viewport || {
            width: this.config.viewport_width,
            height: this.config.viewport_height,
          };
        const parsedInput = validateHostInputArgs(kind, args, vp);
        if (parsedInput.kind === 'click') {
          await tab.page.mouse.click(parsedInput.x, parsedInput.y);
        } else if (parsedInput.kind === 'scroll') {
          await tab.page.mouse.wheel(parsedInput.deltaX, parsedInput.deltaY);
        } else if (parsedInput.kind === 'key') {
          await tab.page.keyboard.press(parsedInput.key);
        } else if (parsedInput.kind === 'text') {
          const insertText = tab.page.keyboard.insertText ?? tab.page.keyboard.insert_text;
          await insertText.call(tab.page.keyboard, parsedInput.text);
        }
        this._invalidate(tab);
        session.last_activity = Date.now() / 1000;
        return this._status(session, { host: true });
      });
    });
  }

  artifactPath(identity: BrowserIdentity, artifactId: string): string {
    return this.artifacts.path(identity, artifactId);
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
      const url = pageUrl(tab.page);
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

  private async _closeContext(session: BrowserSessionState): Promise<void> {
    let timer: NodeJS.Timeout | undefined;
    try {
      await Promise.race([
        session.context.close(),
        new Promise<never>((_, reject) => {
          timer = setTimeout(() => reject(new Error('Context close timeout')), 10000);
        }),
      ]);
    } catch {
      session.state = 'failed';
      throw new BrowserError(
        'cleanup_unresolved',
        'Chromium context did not confirm closure; cleanup ownership has been retained.',
        { requires_reconciliation: true },
      );
    } finally {
      if (timer) clearTimeout(timer);
    }
    session.state = 'closed';
    for (const tabId of session.tabs.keys()) {
      this._navigationErrors.delete(tabId);
      this._redirectCounts.delete(tabId);
      this._redirectTasks.delete(tabId);
    }
    session.tabs.clear();
    this._sessions.delete(session.identity_key);
    this.artifacts.removeOwner(session.identity);
  }

  async closeOwner(identity: BrowserIdentity): Promise<void> {
    const checked = validateIdentity(identity);
    const key = identityKey(checked);
    const session = this._sessions.get(key);
    if (!session) {
      this._grantProviders.delete(key);
      return;
    }
    session.cancelled = true;
    session.state = 'closing';
    this._emitPointer(session, 0, 0, 'idle', false, false);
    for (const [token, prep] of [...this._prepared.entries()]) {
      if (prep.identity_key === key) {
        this._prepared.delete(token);
      }
    }
    for (const pending of [...session.pending]) {
      pending.cancel();
    }
    if (this._sessions.has(key)) {
      await this._closeContext(session);
    }
    this._grantProviders.delete(key);
  }

  async closeAll(): Promise<void> {
    const cleanupFailed: BrowserIdentity[] = [];
    for (const session of [...this._sessions.values()]) {
      try {
        await this.closeOwner(session.identity);
      } catch (err) {
        if (err instanceof BrowserError) {
          cleanupFailed.push(session.identity);
        }
      }
    }
    let browserClosedCount = 0;
    const browserTotal = this._browsers.size;
    for (const browser of [...this._browsers.values()]) {
      try {
        await browser.close();
        browserClosedCount += 1;
      } catch {
        // Ignore individual browser close error
      }
    }
    this._browsers.clear();
    let processShutdownVerified = browserTotal > 0 && browserClosedCount === browserTotal;
    if (this._playwright) {
      if (typeof this._playwright.stop === 'function') {
        await this._playwright.stop();
        processShutdownVerified = true;
      }
      this._playwright = null;
    }
    if (cleanupFailed.length > 0 && !processShutdownVerified) {
      throw new BrowserError(
        'cleanup_unresolved',
        'Browser closure was not confirmed; failed session ownership remains available for cleanup retry.',
        { requires_reconciliation: true },
      );
    }
    for (const identity of cleanupFailed) {
      const key = identityKey(identity);
      const session = this._sessions.get(key);
      this._sessions.delete(key);
      if (session) {
        session.state = 'closed';
        session.tabs.clear();
      }
    }
    this._prepared.clear();
    this._grantProviders.clear();
    this._navigationErrors.clear();
    this.artifacts.close();
  }
}
