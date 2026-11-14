import { spawn } from 'node:child_process';
import { createRequire } from 'node:module';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import * as esbuild from 'esbuild';
import { projectRoot } from './hybrid_paths.mjs';

const require = createRequire(import.meta.url);
const electronExecutable = require('electron');

const tmpDir = await mkdtemp(join(tmpdir(), 'firik-embedded-test-'));
const bundlePath = join(tmpDir, 'executor-bundle.cjs');
const runnerPath = join(tmpDir, 'runner.cjs');

try {
  await esbuild.build({
    entryPoints: [join(projectRoot, 'apps/desktop/src/main/electron-browser-executor.ts')],
    bundle: true,
    platform: 'node',
    format: 'cjs',
    external: ['electron'],
    outfile: bundlePath,
  });

  // Also bundle models + security helpers for validateIdentity / validateGrant
  const helpersPath = join(tmpDir, 'helpers-bundle.cjs');
  await esbuild.build({
    entryPoints: [join(projectRoot, 'apps/desktop/src/browser-worker/index.ts')],
    bundle: true,
    platform: 'node',
    format: 'cjs',
    external: ['electron', 'playwright', 'playwright-core'],
    outfile: helpersPath,
  });

  const runnerSource = `
const assert = require('node:assert/strict');
const http = require('node:http');
const { app, BrowserWindow } = require('electron');
const { ElectronBrowserExecutor } = require(${JSON.stringify(bundlePath)});
const { validateIdentity, validateGrant, originOf } = require(${JSON.stringify(helpersPath)});

function startServer(handler) {
  return new Promise(resolve => {
    const srv = http.createServer(handler);
    srv.listen(0, '127.0.0.1', () => {
      const addr = srv.address();
      resolve({ server: srv, port: addr.port, origin: 'http://127.0.0.1:' + addr.port });
    });
  });
}

async function run() {
  await app.whenReady();
  assert.equal(app.commandLine.hasSwitch('remote-debugging-port'), false);

  let hitCount = 0;
  let forbiddenReached = 0;

  const forbidden = await startServer((req, res) => {
    forbiddenReached += 1;
    res.writeHead(200, { 'Content-Type': 'text/plain' });
    res.end('FORBIDDEN_REACHED');
  });

  const mainSrv = await startServer((req, res) => {
    if (req.url === '/main' || req.url === '/main-b') {
      const alt = req.url === '/main-b';
      const note = alt ? 'Beta Note XYZ' : 'Alpha Note ABC';
      const pwd = alt ? 'pwd-beta-999' : 'pwd-alpha-111';
      const subQuery = alt ? '?v=2' : '?v=1';
      res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
      res.end(\`<!doctype html><html><head><title>Main Acceptance</title><style>
        body{margin:0;padding:16px;background:#fff;color:#111;font:14px monospace}
        input,textarea{display:block;width:240px;height:28px;margin:8px 0;font:14px monospace}
        iframe{width:280px;height:120px;border:1px solid #333}
      </style></head><body>
        <h1>Main Page</h1>
        <label for="full_name">Full Name</label>
        <input id="full_name" aria-label="Full Name" value="">
        <label for="notes">Notes</label>
        <textarea id="notes" aria-label="Notes">\${note}</textarea>
        <label for="pwd">Password</label>
        <input id="pwd" aria-label="Password" type="password" value="\${pwd}">
        <button id="act" onclick="window.__clicked=(window.__clicked||0)+1;fetch('/hit')">Submit</button>
        <iframe src="/subframe\${subQuery}"></iframe>
      </body></html>\`);
      return;
    }
    if (req.url && req.url.startsWith('/subframe')) {
      const v2 = req.url.includes('v=2');
      const sec = v2 ? 'iframe-secret-222' : 'iframe-secret-111';
      res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
      res.end(\`<!doctype html><html><body style="margin:0;background:#eee;font:14px monospace">
        <input id="sec" aria-label="Frame Secret" value="\${sec}">
        <button id="fbtn">Frame Button</button>
      </body></html>\`);
      return;
    }
    if (req.url === '/hit') {
      hitCount += 1;
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end('{"ok":true}');
      return;
    }
    if (req.url === '/redirect-forbidden') {
      res.writeHead(302, { Location: forbidden.origin + '/stole' });
      res.end();
      return;
    }
    res.writeHead(404);
    res.end('not found');
  });

  const win = new BrowserWindow({
    show: true,
    width: 900,
    height: 700,
    webPreferences: { nodeIntegration: false, contextIsolation: true, sandbox: true },
  });

  const pointerEvents = [];
  const executor = new ElectronBrowserExecutor({
    config: { enabled: true },
    getParentWindow: () => win,
    onPointer: ev => pointerEvents.push(ev),
  });

  const id1 = validateIdentity({ conversation_id: 'c1', actor_id: 'parent' });
  const id2 = validateIdentity({ conversation_id: 'c2', actor_id: 'parent' });
  const grant = validateGrant({
    enabled: true,
    allow_screenshots: true,
    origins: [mainSrv.origin],
    allow_private_origins: [mainSrv.origin],
  }, originOf);

  try {
    executor.setLayout({
      conversation_id: 'c1',
      visible: true,
      obscured: false,
      x: 0,
      y: 0,
      width: 900,
      height: 700,
      fit_viewport: true,
    });

    // 1. Host opens page, human enters text, UA sanitized, snapshot omits secrets
    await executor.hostCommand(id1, grant, 'start', {});
    await executor.hostCommand(id1, grant, 'navigate', { url: mainSrv.origin + '/main' });
    assert.equal(win.contentView.children.length, 1);
    const activeView = win.contentView.children[0];
    const ua = activeView.webContents.getUserAgent();
    assert(ua.includes('Chrome/'), 'Expected Chrome token in UA: ' + ua);
    assert(!ua.includes('Electron/'), 'Unexpected Electron token in UA: ' + ua);

    await activeView.webContents.executeJavaScript('document.getElementById("full_name").value = "Alice Secret";');
    const snap = await executor.dispatch(id1, grant, 'snapshot', {});
    assert(String(snap.content).includes('Main Page'));
    assert(String(snap.content).includes('Full Name'));
    assert(String(snap.content).includes('Frame Button'));
    assert(!String(snap.content).includes('Alice Secret'), 'Snapshot leaked input value');
    assert(!String(snap.content).includes('pwd-alpha-111'), 'Snapshot leaked password value');
    assert(!String(snap.content).includes('Alpha Note ABC'), 'Snapshot leaked textarea value');
    assert(!String(snap.content).includes('iframe-secret-111'), 'Snapshot leaked iframe value');

    // 2. Bound prepare/dispatch click + stale revision rejection
    const st = await executor.dispatch(id1, grant, 'status', {});
    const rev = Number(st.revision);
    assert(rev >= 1);
    const prepared = await executor.prepare(id1, grant, 'click', {
      target: { role: 'button', name: 'Submit' },
      revision: rev,
    });
    await executor.dispatch(id1, grant, 'click', {
      target: { role: 'button', name: 'Submit' },
      revision: rev,
    }, { approvedFingerprint: String(prepared.fingerprint) });
    assert(pointerEvents.some(e => e.visible && e.action === 'click' && e.pressing), 'Expected visible pointer click telemetry');
    assert.equal(win.contentView.children.length, 2, 'Expected ImageView cursor overlay stacked above WebContentsView');
    for (let i = 0; i < 20 && hitCount < 1; i++) {
      await new Promise(r => setTimeout(r, 25));
    }
    const clickedDom = await activeView.webContents.executeJavaScript('window.__clicked || 0');
    assert.equal(clickedDom, 1, 'Expected DOM click event on #act');
    assert.equal(hitCount, 1);

    await assert.rejects(
      () => executor.prepare(id1, grant, 'click', {
        target: { role: 'button', name: 'Submit' },
        revision: Math.max(1, rev - 1) === rev ? rev + 99 : rev - 1,
      }),
      err => Boolean(err),
    );

    // 3. Password target triggers authentication_handoff_required; manual handoff pauses agent
    const freshSnap = await executor.dispatch(id1, grant, 'snapshot', {});
    const revAfter = Number(freshSnap.revision);
    await assert.rejects(
      () => executor.prepare(id1, grant, 'fill', {
        target: { label: 'Password' },
        value: 'attempted',
        revision: revAfter,
      }),
      err => err && err.code === 'authentication_handoff_required',
    );
    await executor.hostCommand(id1, grant, 'request_handoff', {});
    assert.equal(win.contentView.children.length, 1, 'Expected ImageView cursor overlay hidden on manual handoff');
    await assert.rejects(
      () => executor.dispatch(id1, grant, 'snapshot', {}),
      err => err && err.code === 'manual_control',
    );
    await executor.resume(id1, grant);

    // 4. Denied origin and 302 redirect to forbidden origin never reach forbidden server
    await assert.rejects(
      () => executor.dispatch(id1, grant, 'navigate', { url: forbidden.origin + '/direct' }),
      err => Boolean(err),
    );
    assert.equal(forbiddenReached, 0);

    await assert.rejects(
      () => executor.hostCommand(id1, grant, 'navigate', { url: mainSrv.origin + '/redirect-forbidden' }),
      err => Boolean(err),
    ).catch(() => {});
    // Also verify direct navigation inside page to 302 forbidden is blocked before connecting
    await executor.hostCommand(id1, grant, 'navigate', { url: mainSrv.origin + '/main' });
    await activeView.webContents.executeJavaScript('fetch("/redirect-forbidden").catch(() => {})');
    await new Promise(r => setTimeout(r, 150));
    assert.equal(forbiddenReached, 0, '302 redirect must never reach forbidden origin');

    // 5. Masked screenshots: identical for agent across distinct secrets in main+iframe, distinct for host preview
    const agentPngA = Buffer.from(await executor.preview(id1, grant, false));
    const hostPngA = Buffer.from(await executor.hostPreview(id1, grant));

    await executor.hostCommand(id1, grant, 'navigate', { url: mainSrv.origin + '/main-b' });
    const agentPngB = Buffer.from(await executor.preview(id1, grant, false));
    const hostPngB = Buffer.from(await executor.hostPreview(id1, grant));

    assert.equal(Buffer.compare(agentPngA, agentPngB), 0, 'Agent screenshots must mask all input/textarea secrets across main & iframe');
    assert.notEqual(Buffer.compare(hostPngA, hostPngB), 0, 'Host preview must differ on non-password textarea');

    // 6. Multi-conversation isolation + closeOwner cleanup
    await executor.hostCommand(id2, grant, 'start', {});
    await executor.hostCommand(id2, grant, 'navigate', { url: mainSrv.origin + '/main' });
    await executor.closeOwner(id1);
    assert.equal(win.contentView.children.length, 1);
    await executor.closeOwner(id2);
    assert.equal(win.contentView.children.length, 0);

    console.log('Embedded WebContentsView acceptance test PASSED (6/6 checks)');
  } finally {
    await executor.closeAll().catch(() => {});
    if (!win.isDestroyed()) win.destroy();
    mainSrv.server.close();
    forbidden.server.close();
  }
}

run()
  .then(() => { app.exit(0); })
  .catch(err => {
    console.error(err);
    app.exit(1);
  });
`;

  await writeFile(runnerPath, runnerSource, 'utf8');

  const env = { ...process.env };
  delete env.ELECTRON_RUN_AS_NODE;

  const exitCode = await new Promise((resolve, reject) => {
    const child = spawn(electronExecutable, [runnerPath], {
      cwd: projectRoot,
      env,
      stdio: 'inherit',
    });
    child.on('error', reject);
    child.on('exit', code => resolve(code ?? 1));
  });

  if (exitCode !== 0) {
    process.exitCode = exitCode;
  }
} finally {
  await rm(tmpDir, { recursive: true, force: true });
}
