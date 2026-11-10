import { describe, expect, it } from 'vitest';
import {
  BrowserSecurityPolicy,
  PlaywrightBrowserExecutor,
  WorkerRequestDispatcher,
  defaultBrowserConfig,
  originOf,
  validateGrant,
  validateIdentity,
} from '../src/browser-worker/index.js';
import { BrowserHost } from '../src/main/browser-host.js';

function createFakePlaywright() {
  const createPage = () => {
    let currentUrl = 'about:blank';
    let currentTitle = 'Fixture Page';
    let currentHtml = '<main><input aria-label="Search" /><button>Submit</button></main>';
    let closed = false;

    const makeLocator = (desc: string) => ({
      first() {
        return this;
      },
      count: async () => 1,
      isVisible: async () => true,
      isEnabled: async () => true,
      async waitFor() {},
      async click() {
        currentHtml = `<main data-clicked="${desc}">${currentHtml}</main>`;
      },
      async fill(text: string) {
        currentHtml = `<main data-filled="${text}">${currentHtml}</main>`;
      },
      async press() {},
      async selectOption(value: string) {
        return [value];
      },
      async setChecked() {},
      async evaluate() {
        if (desc.includes('Password')) {
          return {
            tag: 'input',
            type: 'password',
            name: 'password',
            autocomplete: 'current-password',
            label: 'Password',
            href: '',
            formAction: 'https://example.com/login',
            sensitive: true,
            valueLength: 0,
            selectedCount: 0,
            formFieldCount: 1,
            form: [
              {
                name: 'password',
                type: 'password',
                value: '',
                valueLength: 0,
                checked: false,
                selected: [],
                selectedCount: 0,
                sensitive: true,
              },
            ],
          };
        }
        return {
          tag: 'button',
          type: 'button',
          name: 'submit',
          autocomplete: '',
          label: 'Submit',
          href: '',
          formAction: 'https://example.com/submit',
          sensitive: false,
          valueLength: 0,
          selectedCount: 0,
          formFieldCount: 1,
          form: [
            {
              name: 'q',
              type: 'text',
              value: 'query',
              valueLength: 5,
              checked: false,
              selected: [],
              selectedCount: 0,
              sensitive: false,
            },
          ],
        };
      },
    });

    const mainFrame = {
      url: () => currentUrl,
      content: async () => currentHtml,
      getByRef: (ref: string) => makeLocator(`ref:${ref}`),
      getByRole: (role: string) => makeLocator(`role:${role}`),
      getByLabel: (label: string) => makeLocator(`label:${label}`),
      getByText: (text: string) => makeLocator(`text:${text}`),
      getByTestId: (id: string) => makeLocator(`testid:${id}`),
      locator: (sel: string) => makeLocator(`css:${sel}`),
    };

    return {
      url: () => currentUrl,
      getByRef: (ref: string) => makeLocator(`ref:${ref}`),
      title: async () => currentTitle,
      isClosed: () => closed,
      mainFrame: () => mainFrame,
      frames: () => [mainFrame],
      on() {},
      async goto(url: string) {
        currentUrl = url;
        currentTitle = `Loaded ${url}`;
        return {
          status: () => 200,
          request: () => ({ redirectedFrom: () => null }),
        };
      },
      async reload() {
        return { status: () => 200, request: () => ({ redirectedFrom: () => null }) };
      },
      async goBack() {
        return { status: () => 200, request: () => ({ redirectedFrom: () => null }) };
      },
      async goForward() {
        return { status: () => 200, request: () => ({ redirectedFrom: () => null }) };
      },
      async ariaSnapshot() {
        return '- main:\n  - textbox "Search" [ref=e1]: secret user text\n  - button "Submit" [ref=e2]';
      },
      locator(sel: string) {
        if (sel.includes('password')) {
          return {
            count: async () => 1,
            nth: () => ({ isVisible: async () => true }),
          };
        }
        return makeLocator(sel);
      },
      async screenshot() {
        return Buffer.from('\x89PNG\r\n\x1a\nfake_png_bytes', 'binary');
      },
      mouse: {
        click: async () => {},
        wheel: async () => {},
      },
      keyboard: {
        insertText: async () => {},
        press: async () => {},
      },
      viewportSize: () => ({ width: 1280, height: 800 }),
      async close() {
        closed = true;
      },
    };
  };

  return async () => ({
    chromium: {
      launch: async () => ({
        version: () => '156.0.fake',
        on() {},
        close: async () => {},
        newContext: async () => ({
          setDefaultTimeout() {},
          setDefaultNavigationTimeout() {},
          route: async () => {},
          routeWebSocket: async () => {},
          on() {},
          newPage: async () => createPage(),
          close: async () => {},
        }),
      }),
    },
    stop: async () => {},
  });
}

async function makeExecutor(policy: BrowserSecurityPolicy): Promise<PlaywrightBrowserExecutor> {
  const executor = new PlaywrightBrowserExecutor(defaultBrowserConfig({ enabled: true }), policy);
  // Inject a deterministic Playwright double; production code imports 'playwright'.
  executor._playwright = await createFakePlaywright()();
  return executor;
}

describe('Browser worker runtime and BrowserHost governance', () => {
  const policy = new BrowserSecurityPolicy(async () => ['93.184.216.34']);

  const grant = validateGrant(
    {
      enabled: true,
      allow_public_https: true,
      allow_screenshots: true,
      origins: ['https://example.com'],
      allow_private_origins: [],
    },
    originOf,
  );

  it('enforces snapshot revision, bound one-time approvals, and redacts input values', async () => {
    const executor = await makeExecutor(policy);
    const identity = validateIdentity({ conversation_id: 'c1', actor_id: 'parent' });

    await executor.dispatch(identity, grant, 'start');
    await executor.dispatch(identity, grant, 'navigate', { url: 'https://example.com/form' });
    const snap = await executor.dispatch(identity, grant, 'snapshot');
    const revision = snap.revision as number;
    expect(revision).toBeGreaterThan(0);
    expect(String(snap.content)).toContain('[value omitted]');
    expect(String(snap.content)).not.toContain('secret user text');

    // Unapproved interaction must be rejected
    await expect(
      executor.dispatch(identity, grant, 'click', {
        target: { ref: 'e2' },
        revision,
      }),
    ).rejects.toMatchObject({ code: 'approval_required' });

    // Prepare approval
    const prepared = await executor.prepare(identity, grant, 'click', {
      target: { ref: 'e2' },
      revision,
    });
    expect(typeof prepared.fingerprint).toBe('string');

    // Mismatched arguments must reject and consume the token
    await expect(
      executor.dispatch(
        identity,
        grant,
        'click',
        { target: { ref: 'e1' }, revision },
        { approvedFingerprint: String(prepared.fingerprint) },
      ),
    ).rejects.toMatchObject({ code: 'approval_required' });

    // An intervening permission change invalidates a pending approval even
    // when the original grant is restored before the action is dispatched.
    executor.bindGrant(identity, grant);
    const beforeGrantChange = await executor.prepare(identity, grant, 'click', { target: { ref: 'e2' }, revision });
    executor.bindGrant(identity, { ...grant, allow_screenshots: false });
    executor.bindGrant(identity, grant);
    await expect(executor.dispatch(identity, grant, 'click', { target: { ref: 'e2' }, revision },
      { approvedFingerprint: String(beforeGrantChange.fingerprint) })).rejects.toMatchObject({ code: 'approval_required' });

    // Prepare fresh token and execute once
    const prepared2 = await executor.prepare(identity, grant, 'click', {
      target: { ref: 'e2' },
      revision,
    });
    const clicked = await executor.dispatch(
      identity,
      grant,
      'click',
      { target: { ref: 'e2' }, revision },
      { approvedFingerprint: String(prepared2.fingerprint) },
    );
    expect(clicked.outcome).toBe('action_dispatched');

    // Replay with the same token must fail
    const snap2 = await executor.dispatch(identity, grant, 'snapshot');
    await expect(
      executor.dispatch(
        identity,
        grant,
        'click',
        { target: { ref: 'e2' }, revision: snap2.revision as number },
        { approvedFingerprint: String(prepared2.fingerprint) },
      ),
    ).rejects.toMatchObject({ code: 'approval_required' });

    await executor.closeAll();
  });

  it('requires manual handoff on password/sensitive fields and pauses agent during manual control', async () => {
    const executor = await makeExecutor(policy);
    const identity = validateIdentity({ conversation_id: 'c2', actor_id: 'parent' });

    await executor.dispatch(identity, grant, 'start');
    await executor.dispatch(identity, grant, 'navigate', { url: 'https://example.com/login' });
    const snap = await executor.dispatch(identity, grant, 'snapshot');

    await expect(
      executor.prepare(identity, grant, 'fill', {
        target: { label: 'Password' },
        value: 'hunter2',
        revision: snap.revision as number,
      }),
    ).rejects.toMatchObject({ code: 'authentication_handoff_required' });

    const handoff = await executor.dispatch(identity, grant, 'request_handoff');
    expect(handoff.state).toBe('manual_control');

    // Agent calls are blocked during manual control
    await expect(executor.dispatch(identity, grant, 'snapshot')).rejects.toMatchObject({
      code: 'manual_control',
    });

    // Host input is allowed during manual control
    const inputRes = await executor.hostInput(identity, grant, 'click', { x_ratio: 0.5, y_ratio: 0.5 });
    expect(inputRes.state).toBe('manual_control');

    // Host resume returns control to agent and takes a fresh snapshot
    const resumed = await executor.resume(identity, grant);
    expect(resumed.resumed_by_host).toBe(true);

    await executor.closeAll();
  });

  it('deduplicates execution_id and keeps binary preview bytes out of JSON results in BrowserHost', async () => {
    const executor = await makeExecutor(policy);
    const dispatcher = new WorkerRequestDispatcher(executor);
    const host = new BrowserHost({ inProcess: true, dispatcher });
    const identity = validateIdentity({ conversation_id: 'c3', actor_id: 'parent' });

    await host.handleExecute({
      execution_id: 'exec-start',
      identity,
      grant,
      operation: 'start',
      arguments: {},
      host_control: true,
    });

    const nav = await host.handleExecute({
      execution_id: 'exec-1',
      identity,
      grant,
      operation: 'navigate',
      arguments: { url: 'https://example.com/' },
      host_control: true,
    });
    expect(nav.url).toBe('https://example.com/');

    // Duplicate execution_id must fail with tool_outcome_unknown and requires_reconciliation
    await expect(
      host.handleExecute({
        execution_id: 'exec-1',
        identity,
        grant,
        operation: 'navigate',
        arguments: { url: 'https://example.com/' },
        host_control: true,
      }),
    ).rejects.toMatchObject({
      code: 'tool_outcome_unknown',
      dispatched: true,
      requires_reconciliation: true,
    });

    // Binary preview returns Uint8Array directly via captureUserPreview
    const binary = await host.captureUserPreview('c3');
    expect(binary).toBeInstanceOf(Uint8Array);
    expect(binary!.byteLength).toBeGreaterThan(8);

    await host.shutdown();
  });
});
