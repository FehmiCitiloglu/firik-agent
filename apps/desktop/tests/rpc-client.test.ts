import { afterEach, expect, it } from 'vitest';
import { mkdtemp, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { BrowserHost } from '../src/main/browser-host.js';
import { BackendClient } from '../src/main/rpc-client.js';

const root = resolve(import.meta.dirname, '../../..');
const python = process.env.FIRIK_DESKTOP_PYTHON ?? join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const clients: BackendClient[] = [];
const hosts: BrowserHost[] = [];
const directories: string[] = [];
afterEach(async () => {
  await Promise.all(clients.splice(0).map(client => client.shutdown()));
  await Promise.all(hosts.splice(0).map(host => host.shutdown()));
  await Promise.all(directories.splice(0).map(path => rm(path, { recursive: true, force: true })));
});

it('communicates with the real headless Python sidecar and closes it', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'firik-rpc-client-'));
  directories.push(directory);
  const config = join(directory, 'config.yaml');
  await writeFile(config, 'disabled_default_mcps: [web, computer-use]\nproviders:\n  firik:\n    base_url: https://fixture.invalid/v1\n    api_key_env: FIRIK_DESKTOP_TEST_KEY\n');
  const host = new BrowserHost({ inProcess: true });
  hosts.push(host);
  const client = new BackendClient(python, ['-m', 'firik_agent.desktop_rpc', '--config', config], root,
    { ...process.env, FIRIK_DESKTOP_TEST_KEY: 'firik_llm_local_desktop_fixture' });
  clients.push(client);
  client.setHostHandler(async (method, params) => {
    if (method === 'browser.configureExecutor') {
      return host.configure((params as { config: Parameters<BrowserHost['configure']>[0] }).config);
    }
    if (method === 'browser.prepare') {
      return { ok: true, payload: await host.handlePrepare(params as Parameters<BrowserHost['handlePrepare']>[0]) };
    }
    if (method === 'browser.execute') {
      return { ok: true, payload: await host.handleExecute(params as Parameters<BrowserHost['handleExecute']>[0]) };
    }
    if (method === 'browser.revoke') {
      return { ok: true, payload: await host.handleRevoke(params as Parameters<BrowserHost['handleRevoke']>[0]) };
    }
    throw new Error('Unsupported host method');
  });
  const hello = await client.request('system.hello', { protocol_version: 1 });
  expect(hello.capabilities).toContain('chat');
  expect(hello.capabilities).toContain('browser-typescript');
  const created = await client.request('chat.create', { model: 'fixture-model' });
  const loaded = await client.request('chat.get', { conversation_id: created.conversation_id });
  expect(loaded.model).toBe('fixture-model');
  expect(loaded.messages).toEqual([]);
  expect(loaded.browser?.enabled).toBe(false);

  const configured = await client.request('browser.configure', {
    conversation_id: created.conversation_id,
    enabled: true,
    screenshots: true,
    origins: ['https://example.com'],
    private_origins: [],
  });
  expect(configured.grant?.enabled).toBe(true);
  expect(configured.grant?.screenshots).toBe(true);
  expect(configured.state).toBe('uninitialized');

  const status = await client.request('browser.userCommand', {
    conversation_id: created.conversation_id,
    command: 'status',
  });
  expect(status.state).toBe('uninitialized');

  const snapshot = await client.request('state.snapshot', {});
  expect(snapshot.backend_instance_id).toBe(hello.backend_instance_id);
  await client.shutdown();
  await expect(client.request('state.snapshot', {})).rejects.toThrow(/kapalı/);
});

it('rejects malformed stdout and does not replay a request after backend exit', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'firik-rpc-malformed-'));
  directories.push(directory);
  const script = join(directory, 'broken.py');
  await writeFile(script, "import sys\nsys.stdin.readline()\nprint('not-json', flush=True)\n");
  const client = new BackendClient(python, [script], root);
  clients.push(client);
  await expect(client.request('system.hello', { protocol_version: 1 })).rejects.toThrow(/protokol/);
  await expect(client.request('system.hello', { protocol_version: 1 })).rejects.toThrow(/kapalı/);
});


it('rejects an unknown reverse-RPC method through the protocol failure path', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'firik-rpc-host-frame-'));
  directories.push(directory);
  const script = join(directory, 'broken.py');
  await writeFile(script, `import json, sys
sys.stdin.readline()
print(json.dumps({"jsonrpc":"2.0", "id":"host_bad", "method":"unknown.host", "params":{}}), flush=True)
sys.stdin.read()
`);
  const client = new BackendClient(python, [script], root);
  clients.push(client);
  await expect(client.request('system.hello', { protocol_version: 1 })).rejects.toThrow(/protokol/);
  await expect(client.request('state.snapshot', {})).rejects.toThrow(/kapalı/);
});
