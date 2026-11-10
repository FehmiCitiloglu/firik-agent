import { afterEach, expect, it } from 'vitest';
import { mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { BrowserHost } from '../src/main/browser-host.js';
import { resolveChromiumExecutable, WorkerRequestDispatcher, validateIdentity, validateGrant, originOf,
  type WorkerEnvelope, type WorkerResponseEnvelope } from '../src/browser-worker/index.js';

const hosts: BrowserHost[] = [];
const directories: string[] = [];
afterEach(async () => {
  await Promise.all(hosts.splice(0).map(host => host.shutdown()));
  await Promise.all(directories.splice(0).map(path => rm(path, { recursive: true, force: true })));
});
const identity = validateIdentity({ conversation_id: 'owner', actor_id: 'parent' });
const grant = validateGrant({ enabled: true, allow_public_https: true, allow_screenshots: true }, originOf);

it('never substitutes a browser revision left by another Playwright installation', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'firik-browser-revision-'));
  directories.push(directory);
  const binary = join(directory, 'chromium-999/chrome-linux64/chrome');
  await mkdir(join(directory, 'chromium-999/chrome-linux64'), { recursive: true });
  await writeFile(binary, 'other-revision');
  await writeFile(join(directory, 'chromium-999/INSTALLATION_COMPLETE'), '');
  const previous = process.env.PLAYWRIGHT_BROWSERS_PATH;
  process.env.PLAYWRIGHT_BROWSERS_PATH = directory;
  try {
    expect(resolveChromiumExecutable(join(directory, 'chromium-1254/chrome-linux64/chrome'))).toBeUndefined();
    expect(resolveChromiumExecutable(binary)).toBe(binary);
  } finally {
    if (previous === undefined) delete process.env.PLAYWRIGHT_BROWSERS_PATH;
    else process.env.PLAYWRIGHT_BROWSERS_PATH = previous;
  }
});

it('drops a previously captured frame when preview permission changes before delivery', async () => {
  let release!: () => void;
  let entered!: () => void;
  const capturing = new Promise<void>(resolve => { entered = resolve; });
  const blocked = new Promise<void>(resolve => { release = resolve; });
  class Dispatcher extends WorkerRequestDispatcher {
    override async handle(message: WorkerEnvelope): Promise<WorkerResponseEnvelope> {
      if (message.payload.type === 'host_preview') {
        entered(); await blocked;
        return { id: message.id, ok: true, binary: new Uint8Array([1, 2, 3]) };
      }
      return { id: message.id, ok: true, result: { state: 'ready' } };
    }
  }
  const host = new BrowserHost({ dispatcher: new Dispatcher() });
  hosts.push(host);
  await host.handleExecute({ execution_id: 'initial', identity, grant, operation: 'start', arguments: {}, host_control: true });
  const preview = host.captureUserPreview('owner');
  await capturing;
  await host.handleRevoke({ identity, grant: { ...grant, allow_screenshots: false } });
  release();
  expect(await preview).toBeNull();
});

it('retains definite policy rejection without labelling it as an unknown external effect', async () => {
  class Denied extends WorkerRequestDispatcher {
    override async handle(message: WorkerEnvelope): Promise<WorkerResponseEnvelope> {
      return { id: message.id, ok: false, error: { code: 'private_network_denied', message: 'Denied',
        dispatched: false, requires_reconciliation: false } };
    }
  }
  const host = new BrowserHost({ dispatcher: new Denied() });
  hosts.push(host);
  await expect(host.handleExecute({ execution_id: 'denied-navigation', identity, grant,
    operation: 'navigate', arguments: { url: 'https://127.0.0.1/' }, host_control: true }))
    .rejects.toMatchObject({ code: 'private_network_denied', dispatched: false, requires_reconciliation: false });
});

it('fails closed after worker exit and does not recreate a context or repeat an action', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'firik-browser-exit-'));
  directories.push(directory);
  const worker = join(directory, 'worker.cjs');
  const marker = join(directory, 'calls.txt');
  await writeFile(worker, `const {parentPort}=require('node:worker_threads');
const {appendFileSync}=require('node:fs');
parentPort.on('message',message=>{if(message.port)message.port.on('message',()=>{
appendFileSync(${JSON.stringify(marker)},'called\\n');process.exit(23);});});`);
  const host = new BrowserHost({ workerPath: worker });
  hosts.push(host);
  await expect(host.handleExecute({ execution_id: 'uncertain', identity, grant, operation: 'navigate',
    arguments: { url: 'https://example.com/' }, host_control: true }))
    .rejects.toMatchObject({ code: 'tool_outcome_unknown', requires_reconciliation: true });
  expect(await host.captureUserPreview('owner')).toBeNull();
  await expect(host.handleExecute({ execution_id: 'second', identity, grant, operation: 'navigate',
    arguments: { url: 'https://example.com/' }, host_control: true }))
    .rejects.toMatchObject({ code: 'browser_worker_unavailable' });
  expect((await readFile(marker, 'utf8')).trim()).toBe('called');
});


it('blocks subsequent calls and shuts down the worker after a lost action acknowledgement', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'firik-browser-timeout-'));
  directories.push(directory);
  const worker = join(directory, 'worker.cjs');
  await writeFile(worker, `const {parentPort}=require('node:worker_threads');
parentPort.on('message', message=>{if(message.port)message.port.on('message', envelope=>{
if(envelope.payload.type==='close_all')message.port.postMessage({id:envelope.id,ok:true,result:{}});
});});`);
  const host = new BrowserHost({ workerPath: worker });
  hosts.push(host);
  await expect(host.sendToWorker({ type: 'host_command', identity, grant,
    operation: 'navigate', arguments: { url: 'https://example.com' } }, { dispatched: true, timeoutMs: 100 }))
    .rejects.toMatchObject({ code: 'tool_outcome_unknown', requires_reconciliation: true });
  await expect(host.handleExecute({ execution_id: 'after-timeout', identity, grant,
    operation: 'navigate', arguments: { url: 'https://example.com' }, host_control: true }))
    .rejects.toMatchObject({ code: 'cancelled' });
  expect(await host.captureUserPreview(identity.conversation_id)).toBeNull();
});
