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
    FIRIK_DESKTOP_CONFIG: join(directory, 'config.yaml'), PLAYWRIGHT_BROWSERS_PATH: join(directory, 'unavailable-global-browser-cache'), FIRIK_DESKTOP_TEST_KEY: 'firik_llm_local_desktop_fixture' };
  delete env.ELECTRON_RUN_AS_NODE;
  delete env.FIRIK_DESKTOP_DEV_URL;
  const profile = `--user-data-dir=${join(directory, 'electron-profile')}`;
  const app = await electron.launch({ ...(executablePath ? { executablePath, args: [profile] } : { args: [join(projectRoot, 'apps/desktop'), profile] }), cwd: projectRoot, env, timeout: 30000 });
  const page = await app.firstWindow();
  await page.getByText('Python çekirdeği bağlı', { exact: true }).waitFor({ timeout: 20000 });
  return { app, page };
}

try {
  const fixtureInfo = await waitForFile(join(directory, 'ready.json'));
  const browserOrigin = fixtureInfo.browser_origin;
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

  // Exercise the product controls and the real utilityProcess worker, using
  // a private-origin grant to this one loopback fixture only.
  await page.getByRole('button', { name: /Tarayıcı/ }).click();
  const panel = page.getByRole('complementary', { name: 'Tarayıcı paneli' });
  const preview = panel.locator('.browser-preview-image');
  await panel.getByText('Tarayıcı bu sohbet için kapalı').waitFor();
  await panel.getByRole('button', { name: 'Tarayıcıyı ve canlı görünümü etkinleştir' }).click();
  await panel.getByText('Tarayıcı hazır').waitFor({ timeout: 10000 });
  await panel.getByRole('button', { name: 'Tarayıcı izinleri' }).click();
  await panel.getByLabel('İzin verilen kökenler (her satıra bir tam http/https kökeni)').fill(browserOrigin);
  await panel.getByLabel('Yerel / özel ağ kökenleri (yukarıdaki listede de yer almalıdır)').fill(browserOrigin);
  await panel.getByRole('button', { name: 'İzinleri kaydet' }).click();
  await panel.getByRole('textbox', { name: 'Adres veya arama' }).fill(`${browserOrigin}/browser-fixture`);
  await panel.getByRole('button', { name: 'Git', exact: true }).click();
  await panel.getByRole('tab', { name: 'Firik yerel sayfa' }).waitFor({ timeout: 20000 });
  await preview.waitFor();
  await page.waitForFunction(() => document.querySelector('.browser-preview-image')?.naturalWidth === 1280);
  const processes = await app.evaluate(({ app }) => app.getAppMetrics().map(process => process.name));
  assert(processes.includes('Firik Browser Worker'));
  checks.push('real TypeScript utilityProcess Chromium navigation and binary preview with explicit loopback origin');
  await page.screenshot({ path: join(output, '04-browser-panel.png') });

  await panel.getByRole('button', { name: 'Kontrolü al', exact: true }).click();
  await panel.getByRole('button', { name: 'Ajana geri ver', exact: true }).waitFor();
  await preview.waitFor();
  const bounds = await preview.boundingBox();
  assert(bounds);
  await preview.click({ position: { x: bounds.width * 150 / 1280, y: bounds.height * 170 / 800 } });
  await preview.waitFor();
  await panel.getByRole('textbox', { name: 'Elle kontrol metin girişi' }).fill('Elle yazıldı');
  await panel.getByRole('button', { name: 'Yaz', exact: true }).click();
  await preview.waitFor();
  await panel.getByRole('button', { name: 'Ajana geri ver', exact: true }).click();
  await panel.getByRole('button', { name: 'Kontrolü al', exact: true }).waitFor();
  await page.getByRole('textbox', { name: 'Mesajınız' }).fill('Tarayıcıdaki sayfayı oku');
  await page.getByRole('button', { name: 'Mesajı gönder', exact: true }).click();
  await page.getByText('Ajan aynı sayfayı okudu: Elle yazıldı.', { exact: true }).waitFor({ timeout: 20000 });
  await page.getByRole('button', { name: 'Yanıtı durdur', exact: true }).waitFor({ state: 'hidden' });
  checks.push('human input and Python native Agent browser_snapshot observe the same TypeScript-owned page');

  await page.getByRole('textbox', { name: 'Mesajınız' }).fill('Tarayıcıda Gönder düğmesine bas');
  await page.getByRole('button', { name: 'Mesajı gönder', exact: true }).click();
  const approval = page.getByRole('region', { name: 'İşlem onayı' });
  await approval.locator('strong').filter({ hasText: 'browser_click' }).waitFor({ timeout: 20000 });
  const beforeApproval = await (await fetch(`${browserOrigin}/fixture-state`)).json();
  assert.equal(beforeApproval.browser_actions, 0);
  await page.screenshot({ path: join(output, '05-browser-approval.png') });
  await approval.getByRole('button', { name: 'Onayla', exact: true }).click();
  await page.getByText('TypeScript tıklaması tamamlandı.', { exact: true }).waitFor({ timeout: 20000 });
  await page.getByRole('button', { name: 'Yanıtı durdur', exact: true }).waitFor({ state: 'hidden' });
  const afterApproval = await (await fetch(`${browserOrigin}/fixture-state`)).json();
  assert.equal(afterApproval.browser_actions, 1);
  assert(afterApproval.tools.every(tool => !tool.error && !tool.private_value_leaked));
  checks.push('Python native browser_click waits for exact UI approval and executes once in the TypeScript worker');

  await panel.getByRole('button', { name: 'Yeni sekme aç' }).click();
  await page.waitForFunction(() => document.querySelectorAll('.browser-tab').length === 2);
  await page.waitForFunction(() => document.querySelector('input[aria-label="Adres veya arama"]')?.value === '');
  await panel.getByRole('tab').first().click();
  await page.waitForFunction(url => document.querySelector('input[aria-label="Adres veya arama"]')?.value === url, `${browserOrigin}/browser-fixture`);
  await preview.waitFor();
  checks.push('tab switching restores the correct active address and page');

  await panel.getByRole('button', { name: 'Tarayıcı izinleri' }).click();
  await panel.getByRole('checkbox', { name: 'Canlı sayfa görünümü ve ekran görüntüsüne izin ver' }).uncheck();
  await panel.getByRole('button', { name: 'İzinleri kaydet' }).click();
  await panel.getByText('Canlı önizleme kapalı').waitFor();
  assert.equal(await preview.count(), 0);
  await page.getByRole('button', { name: 'Uzun yanıtı durdur', exact: true }).click();
  await panel.getByText('Tarayıcı bu sohbet için kapalı').waitFor();
  assert.equal(await preview.count(), 0);
  await page.getByRole('button', { name: 'Merhaba hibrit', exact: true }).click();
  await panel.getByText('Canlı önizleme kapalı').waitFor();
  await panel.getByRole('button', { name: 'Canlı görünümü aç', exact: true }).click();
  await preview.waitFor();
  checks.push('preview revocation clears frames and conversation switching does not share browser grants or images');

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
  await page.getByRole('button', { name: /Tarayıcı/ }).click();
  await page.getByText('Tarayıcı bu sohbet için kapalı').waitFor();
  assert.equal(await page.locator('.browser-preview-image').count(), 0);
  checks.push('app restart closes browser resources and does not persist browser authority');
  if (process.env.FIRIK_DESKTOP_PACKAGED_EXECUTABLE) checks.push('bundled Chromium launches with an intentionally unavailable global browser cache');
  await page.setViewportSize({ width: 960, height: 700 });
  await page.screenshot({ path: join(output, '03-compact.png') });
  await app.close();
  application = undefined;
  await writeFile(join(output, 'results.json'), JSON.stringify({ passed: true, checks, liveInference: false, platform: process.platform, architecture: process.arch, globalBrowserCacheDisabled: true }, null, 2) + '\n');
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
