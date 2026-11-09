import { _electron as electron } from 'playwright';
import { spawn } from 'node:child_process';
import { mkdtemp, readFile, mkdir, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import assert from 'node:assert/strict';
import { pythonCommand, projectRoot } from './hybrid_paths.mjs';

const directory = await mkdtemp(join(tmpdir(), 'firik-hybrid-smoke-'));
const output = resolve(process.env.FIRIK_DESKTOP_EVIDENCE ?? join(projectRoot, 'test-results/hybrid-desktop'));
await mkdir(output, { recursive: true });
const fixture = spawn(pythonCommand(), ['tests/fixtures/desktop_provider.py', '--directory', directory], {
  cwd: projectRoot, stdio: ['ignore', 'ignore', 'pipe'],
});
fixture.stderr.on('data', () => {});
const checks = [];
let application;
async function waitForFile(path) {
  const deadline = Date.now() + 10000;
  while (Date.now() < deadline) {
    try { return JSON.parse(await readFile(path, 'utf8')); } catch { await new Promise(resolve => setTimeout(resolve, 50)); }
  }
  throw new Error('Yerel sağlayıcı başlatılamadı.');
}
async function launch() {
  const executablePath = process.env.FIRIK_DESKTOP_PACKAGED_EXECUTABLE;
  const env = { ...process.env, FIRIK_DESKTOP_PYTHON: executablePath ? join(directory, 'python-not-installed') : pythonCommand(),
    FIRIK_DESKTOP_CONFIG: join(directory, 'config.yaml'), FIRIK_DESKTOP_TEST_KEY: 'firik_llm_local_desktop_fixture' };
  delete env.ELECTRON_RUN_AS_NODE;
  delete env.FIRIK_DESKTOP_DEV_URL;
  const profile = `--user-data-dir=${join(directory, 'electron-profile')}`;
  const app = await electron.launch({ ...(executablePath ? { executablePath, args: [profile] } : { args: [join(projectRoot, 'apps/desktop'), profile] }), cwd: projectRoot, env, timeout: 30000 });
  const page = await app.firstWindow();
  await page.getByText('Python çekirdeği bağlı', { exact: true }).waitFor({ timeout: 20000 });
  return { app, page };
}

try {
  await waitForFile(join(directory, 'ready.json'));
  let { app, page } = await launch();
  application = app;
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const preferences = await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].webContents.getLastWebPreferences());
  assert.equal(preferences.sandbox, true);
  assert.equal(preferences.contextIsolation, true);
  assert.equal(preferences.nodeIntegration, false);
  assert.equal(await page.evaluate(() => typeof window.require), 'undefined');
  checks.push('sandbox/context isolation; no renderer Node access');
  await page.screenshot({ path: join(output, '01-welcome.png') });

  await page.getByRole('button', { name: /Yeni sohbet/ }).click();
  await page.getByRole('textbox', { name: 'Mesajınız' }).fill('Merhaba hibrit');
  await page.getByRole('button', { name: 'Mesajı gönder', exact: true }).click();
  await page.getByText('Yerel doğrulama yanıtı: Merhaba hibrit!', { exact: true }).waitFor({ timeout: 20000 });
  await page.getByRole('button', { name: 'Yanıtı durdur', exact: true }).waitFor({ state: 'hidden', timeout: 10000 });
  checks.push('real Python Agent streaming through private stdio and Electron IPC');
  await page.screenshot({ path: join(output, '02-chat.png') });

  await page.getByRole('button', { name: 'Ayarlar', exact: true }).click();
  await page.getByRole('dialog').waitFor();
  assert.equal(await page.locator('input[type=password]').inputValue(), '');
  await page.getByLabel('Varsayılan model', { exact: true }).fill('fixture-alternate');
  await page.getByRole('button', { name: 'Kaydet', exact: true }).click();
  await page.getByRole('dialog').waitFor({ state: 'hidden' });
  checks.push('settings persist and saved key is never returned to UI');

  await page.keyboard.press(process.platform === 'darwin' ? 'Meta+n' : 'Control+n');
  await page.getByRole('textbox', { name: 'Mesajınız' }).waitFor();
  await page.waitForFunction(() => document.querySelector('select[aria-label="Sohbet modeli"]')?.value === 'fixture-alternate');
  assert.equal(await page.getByLabel('Sohbet modeli').inputValue(), 'fixture-alternate');
  await page.getByRole('textbox', { name: 'Mesajınız' }).fill('Uzun yanıtı durdur');
  await page.getByRole('button', { name: 'Mesajı gönder', exact: true }).click();
  await page.getByRole('button', { name: 'Yanıtı durdur', exact: true }).waitFor();
  await page.getByText(/Uzun yanıt adım/).waitFor();
  await page.getByRole('button', { name: 'Yanıtı durdur', exact: true }).click();
  await page.getByRole('button', { name: 'Yanıtı durdur', exact: true }).waitFor({ state: 'hidden', timeout: 10000 });
  checks.push('per-run cancellation and new-chat default model');
  await page.getByRole('textbox', { name: 'Mesajınız' }).fill('İptalden sonra devam');
  await page.getByRole('button', { name: 'Mesajı gönder', exact: true }).click();
  await page.getByText('Yerel doğrulama yanıtı: İptalden sonra devam!', { exact: true }).waitFor({ timeout: 20000 });
  await page.getByRole('button', { name: 'Yanıtı durdur', exact: true }).waitFor({ state: 'hidden', timeout: 10000 });
  checks.push('same conversation continues successfully after cancellation');
  await page.getByRole('button', { name: 'Merhaba hibrit', exact: true }).click();
  await page.getByText('Yerel doğrulama yanıtı: Merhaba hibrit!', { exact: true }).waitFor();
  checks.push('conversation switching keeps independent histories');
  assert.deepEqual(errors, []);
  await app.close();
  application = undefined;

  ({ app, page } = await launch());
  application = app;
  await page.getByRole('button', { name: 'Merhaba hibrit', exact: true }).click();
  await page.getByText('Yerel doğrulama yanıtı: Merhaba hibrit!', { exact: true }).waitFor();
  await page.getByRole('button', { name: 'Ayarlar', exact: true }).click();
  assert.equal(await page.getByLabel('Varsayılan model', { exact: true }).inputValue(), 'fixture-alternate');
  checks.push('app restart restores SQLite conversation and config settings');
  if (process.env.FIRIK_DESKTOP_PACKAGED_EXECUTABLE) checks.push('bundled backend runs with an intentionally missing system Python path');
  await page.getByRole('button', { name: 'Ayarları kapat', exact: true }).click();
  await page.setViewportSize({ width: 960, height: 700 });
  await page.screenshot({ path: join(output, '03-compact.png') });
  await app.close();
  application = undefined;
  await writeFile(join(output, 'results.json'), JSON.stringify({ passed: true, checks, liveInference: false, platform: process.platform }, null, 2) + '\n');
  console.log(`Hybrid desktop smoke passed: ${checks.length} checks. Evidence: ${output}`);
} catch (error) {
  if (application) {
    try { const page = await application.firstWindow(); await page.screenshot({ path: join(output, 'failure.png') });
      console.error((await page.locator('body').innerText()).slice(0, 5000)); } catch { /* Startup may have failed. */ }
  }
  throw error;
} finally {
  await application?.close().catch(() => {});
  fixture.kill('SIGTERM');
}
