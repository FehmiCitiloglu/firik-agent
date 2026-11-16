// Real Electron UI, private stdio and a local MCP child; isolated data, no live API.
import { _electron as electron } from 'playwright';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { projectRoot, pythonCommand } from './hybrid_paths.mjs';

const directory = await mkdtemp(join(tmpdir(), 'firik-mcp-smoke-'));
const output = join(projectRoot, 'test-results/hybrid-mcp');
await mkdir(output, { recursive: true });
const config = join(directory, 'config.yaml');
await writeFile(config, JSON.stringify({ providers: { firik: { base_url: 'http://127.0.0.1:1/v1',
  default_model: 'fixture-model', api_key_env: 'FIRIK_DESKTOP_TEST_KEY' } },
  disabled_default_mcps: ['web', 'computer-use'], mcp_servers: {}, browser: { enabled: false } }));
const env = { ...process.env, FIRIK_DESKTOP_PYTHON: pythonCommand(), FIRIK_DESKTOP_CONFIG: config,
  FIRIK_DESKTOP_TEST_KEY: 'firik_llm_local_desktop_fixture' };
delete env.ELECTRON_RUN_AS_NODE;
delete env.FIRIK_DESKTOP_DEV_URL;
let app;
let page;
const errors = [];
const checks = [];
async function launch() {
  app = await electron.launch({ args: [join(projectRoot, 'apps/desktop'), `--user-data-dir=${join(directory, 'profile')}`],
    cwd: projectRoot, env, timeout: 30000 });
  page = await app.firstWindow();
  page.setDefaultTimeout(10000);
  page.on('pageerror', error => errors.push(error.message));
  await page.getByText('Python çekirdeği bağlı', { exact: true }).waitFor({ timeout: 20000 });
}

try {
  await launch();
  await page.getByRole('button', { name: 'MCP sunucuları', exact: true }).click();
  let dialog = page.getByRole('dialog', { name: 'MCP sunucuları', exact: true });
  await dialog.getByText('İlk MCP sunucunuzu ekleyin', { exact: true }).waitFor();
  await dialog.getByRole('button', { name: 'MCP ekle', exact: true }).click();
  await dialog.getByLabel('Sunucu adı', { exact: true }).fill('yerel-test');
  await dialog.getByLabel('Komut', { exact: true }).fill(pythonCommand());
  await dialog.getByLabel('Argümanlar (JSON)', { exact: true }).fill('invalid');
  await dialog.getByRole('button', { name: 'Sunucuyu kaydet', exact: true }).click();
  await dialog.getByRole('alert').getByText(/Argümanlar:/).waitFor();
  const mock = join(projectRoot, 'examples/mock_mcp_server.py');
  await dialog.getByLabel('Argümanlar (JSON)', { exact: true }).fill(JSON.stringify([mock]));
  await dialog.getByRole('button', { name: 'Sunucuyu kaydet', exact: true }).click();
  let card = dialog.getByRole('article', { name: 'yerel-test sunucusu', exact: true });
  await card.waitFor();
  checks.push('visible sidebar entry, empty state, local MCP creation and inline JSON validation');
  await card.getByRole('button', { name: 'Test et', exact: true }).click();
  await dialog.getByRole('status').filter({ hasText: 'Bağlantı başarılı · 3 araç bulundu.' }).waitFor({ timeout: 30000 });
  await card.getByRole('button', { name: 'Bağlan', exact: true }).click();
  await card.getByText('Bağlı', { exact: true }).waitFor();
  await card.getByText('STDIO · 3 araç', { exact: true }).waitFor();
  await page.screenshot({ path: join(output, '01-mcp-connected.png') });
  await card.getByRole('button', { name: 'yerel-test düzenle', exact: true }).click();
  await dialog.getByLabel('Yeni sohbetlerde varsayılan olarak seç', { exact: true }).check();
  await dialog.getByRole('button', { name: 'Sunucuyu kaydet', exact: true }).click();
  await card.getByText('Bağlı', { exact: true }).waitFor();
  await dialog.getByRole('button', { name: 'MCP ekle', exact: true }).click();
  await dialog.getByLabel('Sunucu adı', { exact: true }).fill('yerel-test');
  await dialog.getByLabel('Komut', { exact: true }).fill(pythonCommand());
  await dialog.getByRole('button', { name: 'Sunucuyu kaydet', exact: true }).click();
  await dialog.getByRole('alert').getByText(/Bu adla bir MCP sunucusu zaten var/).waitFor();
  await dialog.getByRole('button', { name: 'Vazgeç', exact: true }).click();
  checks.push('real MCP connection test, tool discovery, connection, edit with reconnect and duplicate-name rejection');
  await dialog.getByRole('button', { name: 'MCP yönetimini kapat', exact: true }).click();
  await page.getByRole('button', { name: /Yeni sohbet/ }).click();
  await page.getByRole('button', { name: /Araçlar/ }).click();
  const selected = page.locator('.tools-popover').getByLabel(/yerel-test/);
  assert.equal(await selected.isChecked(), true);
  await page.getByRole('button', { name: 'MCP sunucularını yönet', exact: true }).click();
  await dialog.getByRole('button', { name: 'MCP ekle', exact: true }).click();
  await dialog.getByLabel('Sunucu adı', { exact: true }).fill('uzak-test');
  await dialog.getByLabel('Bağlantı türü', { exact: true }).selectOption('http');
  await dialog.getByLabel('Sunucu URL’si', { exact: true }).fill('http://127.0.0.1:9876/mcp');
  await dialog.getByLabel('HTTP başlıkları (JSON)', { exact: true }).fill(JSON.stringify({ Authorization: 'Bearer smoke-fixture-secret' }));
  await dialog.getByRole('button', { name: 'Sunucuyu kaydet', exact: true }).click();
  await dialog.getByRole('button', { name: 'uzak-test düzenle', exact: true }).click();
  assert.equal(await dialog.getByLabel('HTTP başlıkları (JSON)', { exact: true }).inputValue(), '{\n  "Authorization": ""\n}');
  await dialog.getByRole('button', { name: 'Sunucuyu kaydet', exact: true }).click();
  assert.equal(await page.evaluate(async () => JSON.stringify(await window.firik.mcp.list({}))).then(text => text.includes('smoke-fixture-secret')), false);
  checks.push('chat default selection and management shortcut, remote HTTP setup, hidden header values and preserving saved credentials on edit');
  await app.close(); app = null;
  await launch();
  await page.getByRole('button', { name: 'Ayarlar', exact: true }).click();
  await page.getByRole('button', { name: 'MCP sunucularını yönet', exact: true }).click();
  dialog = page.getByRole('dialog', { name: 'MCP sunucuları', exact: true });
  await dialog.getByRole('article', { name: 'uzak-test sunucusu', exact: true }).waitFor();
  card = dialog.getByRole('article', { name: 'yerel-test sunucusu', exact: true });
  await card.getByText(/Yeni sohbetlerde seçili/).waitFor();
  await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].setSize(900, 620));
  assert(await dialog.evaluate(element => element.scrollWidth <= element.clientWidth));
  await page.screenshot({ path: join(output, '02-mcp-compact.png') });
  await card.getByRole('button', { name: 'yerel-test sil', exact: true }).click();
  assert((await page.evaluate(() => window.firik.mcp.list({}))).servers.some(row => row.config.name === 'yerel-test'));
  await card.getByRole('button', { name: 'Sunucuyu sil', exact: true }).click();
  await card.waitFor({ state: 'detached' });
  const remote = dialog.getByRole('article', { name: 'uzak-test sunucusu', exact: true });
  await remote.getByRole('button', { name: 'uzak-test sil', exact: true }).click();
  await remote.getByRole('button', { name: 'Sunucuyu sil', exact: true }).click();
  await dialog.getByText('İlk MCP sunucunuzu ekleyin', { exact: true }).waitFor();
  checks.push('persistence after application restart, settings shortcut, 900×620 layout and confirmed deletion');
  assert.deepEqual(errors, []);
  await rm(join(output, 'failure.json'), { force: true });
  await rm(join(output, 'failure.png'), { force: true });
  await writeFile(join(output, 'verification.json'), JSON.stringify({ checks, errors, scope: 'Real Electron, isolated config, local Python MCP process; no live API' }, null, 2));
  process.stdout.write(JSON.stringify({ checks, output }, null, 2) + '\n');
} catch (error) {
  await writeFile(join(output, 'failure.json'), JSON.stringify({ error: String(error), checks, errors }, null, 2));
  if (page) await page.screenshot({ path: join(output, 'failure.png') }).catch(() => {});
  throw error;
} finally {
  if (app) await app.close();
}
