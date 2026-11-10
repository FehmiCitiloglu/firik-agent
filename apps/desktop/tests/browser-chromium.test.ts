import { createServer, type RequestListener, type Server } from 'node:http';
import type { AddressInfo } from 'node:net';
import { resolve } from 'node:path';
import { readFile } from 'node:fs/promises';
import { afterEach, describe, expect, it } from 'vitest';
import { originOf, validateGrant, validateIdentity } from '../src/browser-worker/index.js';
import { BrowserHost } from '../src/main/browser-host.js';

const workerPath = resolve(import.meta.dirname, '../out/browser-worker/index.cjs');
const servers: Server[] = [];
const hosts: BrowserHost[] = [];

afterEach(async () => {
  await Promise.all(hosts.splice(0).map(host => host.shutdown()));
  await Promise.all(
    servers.splice(0).map(
      srv =>
        new Promise<void>(resolveClose => {
          srv.close(() => resolveClose());
        }),
    ),
  );
});

describe.skipIf(process.env.FIRIK_HYBRID_BROWSER_INTEGRATION !== '1')('Real headless Chromium integration via BrowserHost worker', () => {
  it('launches worker over MessagePort, navigates local fixture, snapshots ARIA refs, executes bound click, and captures binary preview', async () => {
    const server = createServer((req, res) => {
      if (req.url === '/denied') {
        res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
        res.end('<h1>Should not be reached without grant</h1>');
        return;
      }
      res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
      res.end(`<!doctype html>
<html>
  <head><title>Firik Chromium Test</title></head>
  <body>
    <main>
      <h1>Merhaba Firik</h1>
      <input id="note" aria-label="Not alanı" value="initial-secret" />
      <input id="pwd" type="password" aria-label="Parola" value="topsecret" />
      <button id="act" onclick="document.querySelector('h1').textContent = 'Tıklandı: ' + document.getElementById('note').value">Gönder</button>
    </main>
  </body>
</html>`);
    });
    servers.push(server);
    await new Promise<void>((resolveListen, reject) => {
      server.once('error', reject);
      server.listen(0, '127.0.0.1', () => resolveListen());
    });
    const port = (server.address() as AddressInfo).port;
    const baseOrigin = `http://127.0.0.1:${port}`;

    const host = new BrowserHost({ workerPath });
    hosts.push(host);

    const identity = validateIdentity({ conversation_id: 'chromium-conv-1', actor_id: 'parent' });
    const grant = validateGrant(
      {
        enabled: true,
        allow_public_https: false,
        allow_screenshots: true,
        origins: [baseOrigin],
        allow_private_origins: [baseOrigin],
      },
      originOf,
    );

    const started = await host.handleExecute({
      execution_id: 'chr-start-1',
      identity,
      grant,
      operation: 'start',
      arguments: { headless: true },
      host_control: true,
    });
    expect(started.state).toBe('ready');

    const nav = await host.handleExecute({
      execution_id: 'chr-nav-1',
      identity,
      grant,
      operation: 'navigate',
      arguments: { url: `${baseOrigin}/index.html` },
      host_control: true,
    });
    expect(nav.title).toBe('Firik Chromium Test');

    const snap = await host.handleExecute({
      execution_id: 'chr-snap-1',
      identity,
      grant,
      operation: 'snapshot',
      arguments: {},
    });
    const snapshotText = String(snap.content);
    expect(snapshotText).toContain('Merhaba Firik');
    expect(snapshotText).toContain('[value omitted]');
    expect(snapshotText).not.toContain('initial-secret');
    expect(snapshotText).not.toContain('topsecret');

    const revision = snap.revision as number;
    const originalButton = /button "Gönder"[^\n]*\[ref=([^\]]+)\]/.exec(snapshotText)?.[1];
    expect(originalButton).toBeTruthy();
    const staleArgs = { target: { ref: originalButton! }, revision };
    const earlierClick = await host.handlePrepare({ identity, grant, operation: 'click', arguments: staleArgs });
    const prepFill = await host.handlePrepare({
      identity,
      grant,
      operation: 'fill',
      arguments: {
        target: { label: 'Not alanı' },
        value: 'Hibrit Test',
        revision,
      },
    });
    expect(typeof prepFill.fingerprint).toBe('string');

    await host.handleExecute({
      execution_id: 'chr-fill-1',
      identity,
      grant,
      operation: 'fill',
      arguments: {
        target: { label: 'Not alanı' },
        value: 'Hibrit Test',
        revision,
      },
      approved_fingerprint: String(prepFill.fingerprint),
    });

    await expect(host.handleExecute({ execution_id: 'chr-stale-click', identity, grant, operation: 'click',
      arguments: staleArgs, approved_fingerprint: String(earlierClick.fingerprint) }))
      .rejects.toMatchObject({ code: 'stale_observation' });

    const snapAfterFill = await host.handleExecute({
      execution_id: 'chr-snap-2',
      identity,
      grant,
      operation: 'snapshot',
      arguments: {},
    });
    const buttonRef = /button "Gönder"[^\n]*\[ref=([^\]]+)\]/.exec(String(snapAfterFill.content))?.[1];
    expect(buttonRef).toBeTruthy();

    const prepClick = await host.handlePrepare({
      identity,
      grant,
      operation: 'click',
      arguments: {
        target: { ref: buttonRef! },
        revision: snapAfterFill.revision as number,
      },
    });

    await host.handleExecute({
      execution_id: 'chr-click-1',
      identity,
      grant,
      operation: 'click',
      arguments: {
        target: { ref: buttonRef! },
        revision: snapAfterFill.revision as number,
      },
      approved_fingerprint: String(prepClick.fingerprint),
    });
    await expect(host.handleExecute({ execution_id: 'chr-token-replay', identity, grant, operation: 'click',
      arguments: { target: { ref: buttonRef! }, revision: snapAfterFill.revision },
      approved_fingerprint: String(prepClick.fingerprint) })).rejects.toMatchObject({ code: 'approval_required' });

    const snapAfterClick = await host.handleExecute({
      execution_id: 'chr-snap-3',
      identity,
      grant,
      operation: 'snapshot',
      arguments: {},
    });
    expect(String(snapAfterClick.content)).toContain('Tıklandı: Hibrit Test');

    // Password field must trigger authentication_handoff_required
    await expect(
      host.handlePrepare({
        identity,
        grant,
        operation: 'fill',
        arguments: {
          target: { label: 'Parola' },
          value: 'newpass',
          revision: snapAfterClick.revision as number,
        },
      }),
    ).rejects.toMatchObject({ code: 'authentication_handoff_required' });

    // Binary user preview returns valid PNG bytes
    const pngBytes = await host.captureUserPreview('chromium-conv-1');
    expect(pngBytes).not.toBeNull();
    expect(pngBytes!.slice(0, 4)).toEqual(new Uint8Array([0x89, 0x50, 0x4e, 0x47]));
  }, 30000);

  async function serve(handler: RequestListener): Promise<string> {
    const server = createServer(handler);
    servers.push(server);
    await new Promise<void>((resolveListen, reject) => {
      server.once('error', reject);
      server.listen(0, '127.0.0.1', resolveListen);
    });
    return `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  }

  function owner(origin: string, conversation = 'real-owner') {
    const host = new BrowserHost({ workerPath });
    hosts.push(host);
    const identity = validateIdentity({ conversation_id: conversation });
    const grant = validateGrant({ enabled: true, origins: [origin], allow_private_origins: [origin],
      allow_screenshots: true, allow_public_https: false }, originOf);
    let nextId = 0;
    const execute = (operation: string, args: Record<string, unknown> = {}, hostControl = true,
      fingerprint?: string, otherIdentity = identity, otherGrant = grant) => host.handleExecute({
      execution_id: `op-${++nextId}`, identity: otherIdentity, grant: otherGrant, operation, arguments: args,
      host_control: hostControl, approved_fingerprint: fingerprint,
    });
    return { host, identity, grant, execute };
  }

  it('shares a page with manual control, isolates conversations, and revokes one owner without affecting another', async () => {
    const origin = await serve((_req, res) => {
      res.setHeader('Content-Type', 'text/html');
      res.end(`<html><title>Manual fixture</title><body style="margin:0">
      <h1 id="result">Empty</h1><form onsubmit="event.preventDefault();localStorage.setItem('note',note.value);result.textContent=note.value">
      <input id="note" aria-label="Note" style="position:absolute;left:20px;top:100px;width:300px;height:40px" />
      <button>Save</button></form><script>result.textContent=localStorage.getItem('note')||'Empty'</script></body></html>`);
    });
    const { host, identity, grant, execute } = owner(origin);
    await execute('start');
    await execute('navigate', { url: origin });
    const first = await execute('status');
    await execute('request_handoff');
    await expect(execute('snapshot', {}, false)).rejects.toMatchObject({ code: 'manual_control' });
    const input = (kind: string, args: Record<string, unknown>) => execute('host_input', { kind, arguments: args });
    await input('click', { x_ratio: 100 / 1280, y_ratio: 120 / 800 });
    await input('text', { text: 'Human same-page evidence' });
    await input('key', { key: 'Enter' });
    expect(await host.captureUserPreview(identity.conversation_id)).not.toBeNull();
    await execute('resume');
    const observed = await execute('snapshot', {}, false);
    expect(observed.content).toContain('Human same-page evidence');

    const second = validateIdentity({ conversation_id: 'another-owner' });
    await execute('start', {}, true, undefined, second);
    await execute('navigate', { url: origin }, true, undefined, second);
    const isolated = await execute('snapshot', {}, false, undefined, second);
    expect(isolated.content).toContain('Empty');
    expect(isolated.content).not.toContain('Human same-page evidence');
    await expect(execute('select_tab', { tab_id: first.active_tab_id }, true, undefined, second))
      .rejects.toMatchObject({ code: 'ownership_denied' });
    await host.handleRevoke({ identity, grant: { ...grant, enabled: false }, close_owner: true });
    expect(await host.captureUserPreview(identity.conversation_id)).toBeNull();
    expect((await execute('status', {}, true, undefined, second)).state).toBe('ready');
  }, 30000);

  it('checks private origins before dispatch and checks a redirect destination before requesting it', async () => {
    let reached = 0;
    const forbidden = await serve((_req, res) => { reached++; res.end('forbidden'); });
    const origin = await serve((req, res) => {
      if (req.url === '/redirect') { res.writeHead(302, { Location: forbidden }); res.end(); }
      else res.end('<title>Allowed</title><h1>Allowed</h1>');
    });
    const { execute, grant } = owner(origin);
    await execute('start');
    await expect(execute('navigate', { url: forbidden }, true, undefined, undefined,
      { ...grant, origins: [origin, forbidden], allow_private_origins: [] }))
      .rejects.toMatchObject({ code: 'private_network_denied', dispatched: false });
    await expect(execute('navigate', { url: `${origin}/redirect` })).rejects.toMatchObject({ code: 'origin_denied' });
    expect(reached).toBe(0);
    expect((await execute('navigate', { url: origin })).title).toBe('Allowed');
  }, 30000);

  it('masks private inputs in main and child frames in model artifacts and the user preview', async () => {
    const origin = await serve((req, res) => {
      const version = req.url?.includes('two') ? 'much-longer-second-secret' : 'first-secret';
      res.setHeader('Content-Type', 'text/html');
      if (req.url?.startsWith('/child')) {
        res.end(`<input type="password" aria-label="Child password" value="${version}">
          <textarea aria-label="Private child note">${version}</textarea>`);
      } else {
        res.end(`<title>Privacy fixture</title><h1>Privacy fixture</h1>
          <input type="password" aria-label="Password" value="${version}">
          <iframe title="Private frame" src="/child-${req.url?.includes('two') ? 'two' : 'one'}"></iframe>`);
      }
    });
    const { host, identity, execute } = owner(origin);
    await execute('start');
    const modelImages: Buffer[] = [];
    const userImages: Uint8Array[] = [];
    for (const page of ['one', 'two']) {
      await execute('navigate', { url: `${origin}/${page}` });
      const snap = await execute('snapshot', {}, false);
      expect(String(snap.content)).not.toContain('first-secret');
      expect(String(snap.content)).not.toContain('much-longer-second-secret');
      const image = await execute('screenshot', {}, false);
      modelImages.push(await readFile(String(image.artifact_path)));
      userImages.push((await host.captureUserPreview(identity.conversation_id))!);
    }
    expect(modelImages[0]).toEqual(modelImages[1]);
    // User preview masks passwords in every frame. Regular human text remains
    // visible there, so the child textarea changes the human preview only.
    expect(userImages[0]).not.toEqual(userImages[1]);
  }, 30000);


  it('cancels an in-flight navigation on revocation without retrying its request', async () => {
    let entered!: () => void;
    let release!: () => void;
    let count = 0;
    const waiting = new Promise<void>(resolve => { entered = resolve; });
    const origin = await serve((_req, res) => {
      count++;
      release = () => res.end('<h1>Late response</h1>');
      entered();
    });
    const { host, identity, grant, execute } = owner(origin);
    await execute('start');
    const result = execute('navigate', { url: origin }).then(
      () => ({ code: 'unexpected_success', requires_reconciliation: false }),
      error => error,
    );
    await waiting;
    try {
      await host.handleRevoke({ identity, grant: { ...grant, enabled: false }, close_owner: true });
    } finally {
      release();
    }
    expect(await result).toMatchObject({ code: 'tool_outcome_unknown', requires_reconciliation: true });
    expect(count).toBe(1);
    expect(await host.captureUserPreview(identity.conversation_id)).toBeNull();
    await expect(execute('snapshot', {}, false, undefined, identity, { ...grant, enabled: false }))
      .rejects.toMatchObject({ code: 'permission_denied' });
  }, 30000);

});
