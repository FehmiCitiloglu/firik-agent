// Real Electron + private stdio + Python services; isolated data, loopback only.
import { _electron as electron } from 'playwright';
import assert from 'node:assert/strict';
import { spawn, execFileSync } from 'node:child_process';
import { mkdtemp, readFile, realpath, mkdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { projectRoot, pythonCommand } from './hybrid_paths.mjs';

const directory = await realpath(await mkdtemp(join(tmpdir(), 'firik-workspaces-smoke-')));
const output = join(projectRoot, 'test-results/hybrid-workspaces');
const root = join(directory, 'project with spaces');
await mkdir(output, { recursive: true });
await mkdir(root);
await mkdir(join(root, 'src'));
await writeFile(join(root, 'main.py'), 'value = 1\n');
await writeFile(join(root, 'src', 'feature.py'), 'def feature():\n    return "context fixture"\n');
await writeFile(join(root, '.gitignore'), '.env\n');
await writeFile(join(root, '.env'), 'FIXTURE_SECRET=not-a-real-secret\n');
const git = (...args) => execFileSync('git', ['-C', root, ...args], { encoding: 'utf8' });
git('init', '-b', 'main');
git('add', 'main.py', '.gitignore', 'src/feature.py');
git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.test', 'commit', '-m', 'Initial');
const fixture = spawn(pythonCommand(), ['tests/fixtures/desktop_provider.py', '--directory', directory], {
  cwd: projectRoot, stdio: 'ignore',
});
let app;
const checks = [];
const errors = [];
try {
  const deadline = Date.now() + 10000;
  while (true) {
    try { await readFile(join(directory, 'ready.json')); break; }
    catch { if (Date.now() > deadline) throw new Error('Yerel sağlayıcı başlatılamadı.'); }
    await new Promise(resolve => setTimeout(resolve, 50));
  }
  const env = { ...process.env, FIRIK_DESKTOP_PYTHON: pythonCommand(), FIRIK_DESKTOP_CONFIG: join(directory, 'config.yaml'),
    FIRIK_DESKTOP_TEST_KEY: 'firik_llm_local_desktop_fixture' };
  delete env.ELECTRON_RUN_AS_NODE;
  delete env.FIRIK_DESKTOP_DEV_URL;
  app = await electron.launch({ args: [join(projectRoot, 'apps/desktop'), `--user-data-dir=${join(directory, 'electron-profile')}`],
    cwd: projectRoot, env, timeout: 30000 });
  const page = await app.firstWindow();
  page.on('pageerror', error => errors.push(error.message));
  await page.getByText('Python çekirdeği bağlı', { exact: true }).waitFor({ timeout: 20000 });
  await page.getByRole('button', { name: 'Kodlama ajanı', exact: true }).click();
  assert.equal(await page.getByRole('navigation', { name: 'Çalışma alanları', exact: true }).getByRole('button', { name: 'GitGod', exact: true }).count(), 0);
  await page.getByRole('button', { name: 'Proje ekle', exact: true }).click();
  const projectsBeforePicker = (await page.evaluate(() => window.firik.projects.list({}))).projects.length;
  const chooseDirectory = async (canceled, filePaths) => {
    await app.evaluate(({ dialog }, result) => {
      // Exercise renderer -> trusted IPC -> main -> native-dialog adapter.
      // Only the OS dialog is replaced; project creation still uses the real backend.
      dialog.showOpenDialog = async (_parent, options) => {
        globalThis.firikDirectoryDialogOptions = options;
        return result;
      };
    }, { canceled, filePaths });
    await page.getByRole('button', { name: 'Klasör seç', exact: true }).click();
    await page.waitForFunction(() => {
      const button = [...document.querySelectorAll('button')].find(row => row.textContent.trim() === 'Klasör seç');
      return button && !button.disabled;
    });
    const options = await app.evaluate(() => globalThis.firikDirectoryDialogOptions);
    assert(options.properties.includes('openDirectory'));
    assert(!options.properties.includes('multiSelections'));
  };
  await chooseDirectory(true, []);
  assert.equal(await page.getByLabel('Proje klasörü', { exact: true }).inputValue(), '');
  assert.equal((await page.evaluate(() => window.firik.projects.list({}))).projects.length, projectsBeforePicker);
  await chooseDirectory(false, [root]);
  assert.equal(await page.getByLabel('Proje klasörü', { exact: true }).inputValue(), root);
  assert.equal(await page.getByLabel('Proje adı', { exact: true }).inputValue(), 'project with spaces');
  await page.getByLabel('Proje adı', { exact: true }).fill('Geçiş doğrulaması');
  await chooseDirectory(true, []);
  assert.equal(await page.getByLabel('Proje klasörü', { exact: true }).inputValue(), root);
  assert.equal(await page.getByLabel('Proje adı', { exact: true }).inputValue(), 'Geçiş doğrulaması');
  await page.getByRole('button', { name: 'Projeyi ekle', exact: true }).click();
  checks.push('native folder picker via trusted IPC, cancellation preserves fields, folder basename suggestion and a project path containing spaces');
  await page.getByLabel('Kodlama çalışma modu').selectOption('ask');
  const chips = page.getByLabel('İsteğe eklenen dosyalar', { exact: true });
  await page.getByRole('tab', { name: 'Bağlam', exact: true }).click();
  const files = page.getByRole('region', { name: 'Proje dosyaları', exact: true });
  await files.getByRole('checkbox', { name: 'main.py bağlama ekle', exact: true }).check();
  assert.equal(await files.getByRole('button', { name: '.env', exact: true }).count(), 0);
  assert.equal(await files.getByRole('button', { name: '.git', exact: true }).count(), 0);
  await files.getByRole('button', { name: 'src', exact: true }).click();
  await files.getByRole('button', { name: 'feature.py', exact: true }).click();
  await page.getByRole('region', { name: 'Dosya önizlemesi', exact: true }).getByText('context fixture', { exact: false }).waitFor();
  await files.getByRole('checkbox', { name: 'src/feature.py bağlama ekle', exact: true }).check();
  assert.equal(await chips.getByRole('button').count(), 2);
  await page.screenshot({ path: join(output, '05-context.png') });
  await files.getByRole('button', { name: 'Üst klasöre git', exact: true }).click();
  await files.getByRole('textbox', { name: 'Proje dosyalarında ara', exact: true }).fill('feature');
  await files.getByRole('button', { name: 'src/feature.py', exact: true }).waitFor();
  await files.getByRole('textbox', { name: 'Proje dosyalarında ara', exact: true }).fill('no-such-fixture-file');
  await files.getByText('Aramayla eşleşen dosya yok.', { exact: true }).waitFor();
  await files.getByRole('textbox', { name: 'Proje dosyalarında ara', exact: true }).fill('');
  await files.getByRole('checkbox', { name: 'main.py bağlama ekle', exact: true }).waitFor();
  await files.getByRole('button', { name: 'main.py', exact: true }).click();
  await page.getByRole('region', { name: 'Dosya önizlemesi', exact: true }).getByText('value = 1', { exact: false }).waitFor();
  checks.push('project file navigation, nested-file preview, recursive search, empty search, sensitive-file filtering and explicit context chips');

  await page.getByRole('tab', { name: 'İzinler', exact: true }).click();
  await page.getByRole('checkbox', { name: /^Dosyaları düzenle/ }).uncheck();
  await page.getByRole('checkbox', { name: /^Dosyaları oku/ }).uncheck();
  await page.getByRole('button', { name: 'İzinleri kaydet', exact: true }).click();
  await page.getByText('Proje izinleri kaydedildi.', { exact: true }).waitFor();
  const projectId = await page.getByLabel('Çalışılacak proje').inputValue();
  assert.equal((await page.evaluate(id => window.firik.coding.preferences({ project_id: id }), projectId)).allow_edits, false);
  await page.getByRole('tab', { name: 'Bağlam', exact: true }).click();
  await page.getByText('Proje dosyalarını görüntülemek için İzinler sekmesinde dosya okuma iznini açın.', { exact: true }).waitFor();
  assert.equal(await page.getByRole('region', { name: 'Dosya önizlemesi', exact: true }).count(), 0);
  assert.equal(await files.count(), 0);
  await page.getByLabel('Kodlama isteği').fill('main.py dosyasını açıkla');
  assert.equal(await page.getByRole('button', { name: 'Gönder', exact: true }).isDisabled(), true);
  await page.getByText('Seçili bağlamı göndermek için dosya okuma iznini açın veya eklenen dosyaları çıkarın.', { exact: true }).waitFor();
  await page.getByLabel('Kodlama isteği').press('Control+Enter');
  assert.equal(await page.locator('.coding-messages .workspace-message').count(), 0);
  await page.getByRole('tab', { name: 'İzinler', exact: true }).click();
  await page.getByRole('checkbox', { name: /^Dosyaları oku/ }).check();
  await page.getByRole('button', { name: 'İzinleri kaydet', exact: true }).click();
  await page.getByText('Proje izinleri kaydedildi.', { exact: true }).waitFor();
  assert.equal((await page.evaluate(id => window.firik.coding.preferences({ project_id: id }), projectId)).allow_reads, true);
  await page.screenshot({ path: join(output, '06-permissions.png') });
  await page.getByRole('tab', { name: 'Bağlam', exact: true }).click();
  await files.getByRole('checkbox', { name: 'main.py bağlama ekle', exact: true }).waitFor();
  await page.getByRole('region', { name: 'Dosya önizlemesi', exact: true }).getByText('value = 1', { exact: false }).waitFor();
  await page.getByRole('button', { name: 'Dosya önizlemesini kapat', exact: true }).click();
  checks.push('revoking file reads hides cached files and previews, blocks selected context submission and recovers after read permission is restored');
  await page.getByRole('tab', { name: 'Plan', exact: true }).click();
  await page.getByLabel('Kodlama isteği').fill('main.py dosyasını açıkla');
  await page.getByLabel('Kodlama isteği').press('Control+Enter');
  await page.locator('.coding-messages').getByText(/Yerel doğrulama yanıtı/).waitFor({ timeout: 25000 });
  await page.getByRole('button', { name: 'Yeni oturum', exact: true }).waitFor({ state: 'visible' });
  await page.waitForFunction(() => ![...document.querySelectorAll('button')].find(button => button.textContent.trim() === 'Yeni oturum')?.disabled);
  const firstSession = await page.locator('.coding-workspace').getAttribute('data-session-id');
  const chatsBefore = (await page.evaluate(() => window.firik.snapshot({}))).conversations.length;
  await page.getByLabel('Kodlama isteği').fill('Korunacak taslak');
  await page.getByLabel('Kodlama isteği').press('Control+n');
  await page.getByText('Projede ne yapalım?', { exact: true }).waitFor();
  assert.notEqual(await page.locator('.coding-workspace').getAttribute('data-session-id'), firstSession);
  assert.equal((await page.evaluate(() => window.firik.snapshot({}))).conversations.length, chatsBefore);
  assert.equal(await chips.getByRole('button').count(), 0);
  const history = page.getByRole('navigation', { name: 'Kodlama oturumları', exact: true });
  await history.locator('button[data-record-id]').first().click();
  await page.locator('.coding-messages').getByText(/Yerel doğrulama yanıtı/).waitFor();
  assert.equal(await page.getByLabel('Kodlama isteği').inputValue(), 'Korunacak taslak');
  assert.equal(await chips.getByRole('button').count(), 2);
  await chips.getByRole('button', { name: 'src/feature.py bağlamdan çıkar', exact: true }).click();
  assert.equal(await chips.getByRole('button').count(), 1);
  await page.getByRole('tab', { name: 'Plan', exact: true }).focus();
  await page.keyboard.press('ArrowRight');
  await page.keyboard.press('ArrowRight');
  assert.equal(await page.getByRole('tab', { name: 'Testler', exact: true }).getAttribute('aria-selected'), 'true');
  await page.getByRole('tabpanel').getByText('Henüz test çalıştırılmadı.', { exact: false }).waitFor();
  checks.push('coding registration, keyboard submission, project-scoped Ctrl+N, durable history, preserved drafts and accessible evidence tabs');
  await page.screenshot({ path: join(output, '01-coding.png') });

  await page.getByLabel('Kodlama modeli').selectOption('fixture-alternate');
  await page.getByLabel('Kodlama isteği').fill('Uzun bir kodlama yanıtı oluştur');
  await page.getByRole('button', { name: 'Gönder', exact: true }).click();
  const stop = page.getByRole('button', { name: 'Durdur', exact: true });
  await stop.waitFor();
  const activeRun = await page.locator('.coding-workspace').getAttribute('data-run-id');
  await page.getByLabel('Kodlama isteği').press('Control+n');
  await page.getByRole('alert').getByText(/Yeni oturum açmadan önce/).waitFor();
  assert.equal(await page.locator('.coding-workspace').getAttribute('data-run-id'), activeRun);
  await page.getByRole('button', { name: 'Bildirimi kapat', exact: true }).click();
  await page.getByLabel('Kodlama isteği').fill('Çalışırken yazılan taslak');
  const activeSession = await page.locator('.coding-workspace').getAttribute('data-session-id');
  await page.getByRole('tab', { name: 'GitGod', exact: true }).click();
  assert.equal(await stop.isVisible(), true);
  assert.equal(await page.locator('.coding-workspace').getAttribute('data-run-id'), activeRun);
  await page.getByRole('tab', { name: 'Ajan', exact: true }).click();
  assert.equal(await page.locator('.coding-workspace').getAttribute('data-session-id'), activeSession);
  assert.equal(await page.getByLabel('Kodlama modeli').inputValue(), 'fixture-alternate');
  assert.equal(await page.getByLabel('Kodlama çalışma modu').inputValue(), 'ask');
  assert.equal(await page.getByLabel('Kodlama isteği').inputValue(), 'Çalışırken yazılan taslak');
  assert.equal(await chips.getByRole('button', { name: 'main.py bağlamdan çıkar', exact: true }).count(), 1);
  await page.getByRole('tab', { name: 'İzinler', exact: true }).click();
  assert.equal(await page.getByRole('checkbox', { name: /^Dosyaları düzenle/ }).isDisabled(), true);
  assert.equal(await page.getByRole('button', { name: 'İzinleri kaydet', exact: true }).isDisabled(), true);
  await page.getByRole('tab', { name: 'Testler', exact: true }).click();
  await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].setSize(940, 620));
  await page.waitForFunction(() => document.querySelector('.coding-inspector')?.hidden === true);
  for (const locator of [stop, page.getByLabel('Kodlama modeli'), page.getByLabel('Kodlama çalışma modu'), page.getByLabel('Kodlama isteği')]) {
    const bounds = await locator.boundingBox();
    assert(bounds && bounds.y >= 0 && bounds.y + bounds.height <= await page.evaluate(() => innerHeight));
  }
  await page.getByRole('button', { name: 'İnceleme', exact: true }).click();
  await page.getByRole('tab', { name: 'Testler', exact: true }).waitFor();
  await page.getByRole('tab', { name: 'Testler', exact: true }).focus();
  await page.keyboard.press('Escape');
  assert.equal(await page.getByRole('button', { name: 'İnceleme', exact: true }).evaluate(button => document.activeElement === button), true);
  await page.waitForFunction(() => {
    const messages = document.querySelector('.coding-messages');
    return messages && messages.textContent.includes('adım 10') && messages.scrollHeight - messages.clientHeight > 100;
  });
  const atBottom = () => page.waitForFunction(() => {
    const messages = document.querySelector('.coding-messages');
    return messages && messages.scrollHeight - messages.scrollTop - messages.clientHeight < 5;
  });
  await atBottom();
  await page.locator('.coding-messages').evaluate(messages => { messages.scrollTop = 0; });
  await page.getByRole('button', { name: 'Yeni çıktıya git', exact: true }).waitFor();
  assert.equal(await page.locator('.coding-messages').evaluate(messages => messages.scrollTop), 0);
  await page.getByRole('button', { name: 'Yeni çıktıya git', exact: true }).click();
  await atBottom();
  await page.screenshot({ path: join(output, '04-coding-compact.png') });
  await stop.click();
  await page.waitForFunction(() => ![...document.querySelectorAll('button')].find(button => button.textContent.trim() === 'Yeni oturum')?.disabled);
  checks.push('busy-session guard, background draft retention, model/mode/stop/composer visible at 940x620 and collapsible evidence drawer');
  checks.push('streaming output follows the end, preserves reading position after scrolling up and resumes via the new-output action');
  await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].setSize(1280, 850));

  const projectSelect = page.getByLabel('Çalışılacak proje');
  const firstProject = await projectSelect.inputValue();
  const secondRoot = join(directory, 'second-project');
  await mkdir(secondRoot);
  await writeFile(join(secondRoot, 'other.py'), 'other = 1\n');
  await page.getByRole('button', { name: 'Proje ekle', exact: true }).click();
  await page.getByLabel('Proje adı', { exact: true }).fill('İkinci proje');
  await page.getByLabel('Proje klasörü', { exact: true }).fill(secondRoot);
  await page.getByRole('button', { name: 'Projeyi ekle', exact: true }).click();
  await page.getByText('Projede ne yapalım?', { exact: true }).waitFor();
  assert.equal(await chips.getByRole('button').count(), 0);
  await history.getByText('Bu projede henüz kodlama çalışması yok.', { exact: false }).waitFor();
  assert.notEqual(await projectSelect.inputValue(), firstProject);
  assert.equal(await history.locator('button[data-record-id]').count(), 0);
  await projectSelect.selectOption(firstProject);
  await page.locator('.coding-messages').getByText(/Yerel doğrulama yanıtı/).waitFor();
  assert.equal(await page.getByLabel('Kodlama isteği').inputValue(), 'Çalışırken yazılan taslak');
  assert.equal(await chips.getByRole('button', { name: 'main.py bağlamdan çıkar', exact: true }).count(), 1);
  const inspectorToggle = page.getByRole('button', { name: 'İnceleme', exact: true });
  if (await inspectorToggle.getAttribute('aria-expanded') !== 'true') await inspectorToggle.click();
  await page.getByRole('tab', { name: 'İzinler', exact: true }).click();
  assert.equal(await page.getByRole('checkbox', { name: /^Dosyaları düzenle/ }).isChecked(), false);
  await page.getByRole('button', { name: 'İnceleme', exact: true }).click();
  checks.push('project-scoped permission persistence, busy-run permission lock and isolated session/project drafts and context selections');

  await writeFile(join(root, 'main.py'), 'value = 2\n');
  await page.getByRole('tab', { name: 'GitGod', exact: true }).click();
  await page.getByRole('checkbox', { name: 'main.py Git için seç', exact: true }).waitFor();
  await page.getByRole('tab', { name: 'Ajan', exact: true }).click();
  await writeFile(join(root, 'main.py'), 'value = 3\n');
  await page.getByRole('tab', { name: 'GitGod', exact: true }).click();
  await page.locator('.git-content').getByText(/\+value = 3/).waitFor({ state: 'attached' });
  await page.getByRole('checkbox', { name: 'main.py Git için seç', exact: true }).check();
  await page.getByRole('button', { name: 'Seçilenleri hazırla', exact: true }).click();
  assert.equal(await page.getByLabel('Dosya yolları (her satırda bir yol)').inputValue(), 'main.py');
  await page.getByRole('tab', { name: 'Ajan', exact: true }).click();
  await page.getByRole('tab', { name: 'GitGod', exact: true }).click();
  assert.equal(await page.getByLabel('Dosya yolları (her satırda bir yol)').inputValue(), 'main.py');
  await page.getByRole('button', { name: 'Komutu önizle', exact: true }).click();
  const confirm = page.getByLabel('Bu komutun değişiklik yapmasını onaylıyorum.');
  await confirm.waitFor();
  assert.equal(git('diff', '--cached'), '');
  assert.equal(await page.getByRole('button', { name: 'Çalıştır', exact: true }).isDisabled(), true);
  await confirm.check();
  await page.getByRole('button', { name: 'Çalıştır', exact: true }).click();
  await page.locator('.git-output').getByText(/Tamamlandı/).waitFor();
  await page.waitForFunction(() => {
    const button = [...document.querySelectorAll('button')].find(row => row.textContent.trim() === 'Komutu önizle');
    return button && !button.disabled;
  });
  assert.equal(await page.getByRole('region', { name: 'Değişen dosyalar', exact: true }).evaluate(element => element.scrollHeight <= element.clientHeight + 1), true,
    'A single changed file and its quick actions must fit in their card after command execution.');
  assert.equal(await page.locator('.git-output').evaluate(element => element.scrollHeight <= element.clientHeight + 1), true,
    'Short Git command output must be readable without a compressed card.');
  assert(git('diff', '--cached').includes('+value = 3'));
  checks.push('integrated GitGod refreshes external changes on reentry, preserves prepared forms, selects files without typing paths and stages through exact preview/confirmation');
  await page.screenshot({ path: join(output, '07-git-output.png') });
  await page.locator('.git-panel').evaluate(element => { element.scrollTop = 0; });
  await page.screenshot({ path: join(output, '02-gitgod.png') });

  await page.getByRole('button', { name: 'Sohbet', exact: true }).click();
  await page.getByRole('button', { name: /Yeni sohbet/ }).click();
  await page.getByRole('textbox', { name: 'Mesajınız' }).waitFor();
  await page.getByRole('button', { name: 'Sesli asistan', exact: true }).click();
  await page.getByText('Ses kapalı', { exact: true }).waitFor();
  await page.getByLabel('Ses özelliklerini etkinleştir').check();
  await page.getByLabel('Seslendirme sağlayıcısı').selectOption('system');
  await page.getByRole('button', { name: 'Ses ayarlarını kaydet', exact: true }).click();
  await page.getByText('Hazır', { exact: true }).waitFor();
  assert.equal(await page.getByRole('button', { name: 'Konuş', exact: true }).isEnabled(), true);
  checks.push('voice session binding and persisted settings without opening microphone or speaker');
  await page.screenshot({ path: join(output, '03-voice.png') });
  await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].setSize(900, 700));
  for (const name of ['Kodlama ajanı', 'GitGod', 'Sesli asistan']) {
    await page.getByRole(name === 'GitGod' ? 'tab' : 'button', { name, exact: true }).click();
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
  }
  checks.push('workspace navigation at the minimum supported width with no document overflow');
  assert.deepEqual(errors, []);
  await rm(join(output, 'failure.json'), { force: true });
  await rm(join(output, 'failure.png'), { force: true });
  await writeFile(join(output, 'verification.json'), JSON.stringify({ checks, errors, scope: 'source Electron UI with isolated shared backend; no microphone, speaker or live provider calls' }, null, 2));
  process.stdout.write(JSON.stringify({ checks, output }, null, 2) + '\n');
} catch (error) {
  await writeFile(join(output, 'failure.json'), JSON.stringify({ error: String(error), checks, errors }, null, 2));
  if (app) {
    const page = await app.firstWindow().catch(() => null);
    if (page) await page.screenshot({ path: join(output, 'failure.png') }).catch(() => {});
  }
  throw error;
} finally {
  if (app) await app.close();
  fixture.kill('SIGTERM');
}
