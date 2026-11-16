// Real Electron + shared execution backend; isolated projects and loopback provider.
import { _electron as electron } from 'playwright';
import assert from 'node:assert/strict';
import { spawn, execFileSync } from 'node:child_process';
import { access, mkdtemp, readFile, realpath, mkdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { projectRoot, pythonCommand } from './hybrid_paths.mjs';

const directory = await realpath(await mkdtemp(join(tmpdir(), 'firik-terminal-smoke-')));
const output = join(projectRoot, 'test-results/hybrid-terminal');
const root = join(directory, 'project with spaces');
const secondRoot = join(directory, 'second-project');
await mkdir(output, { recursive: true });
await mkdir(root);
await mkdir(secondRoot);
await mkdir(join(root, 'nested'));
const scripts = {
  'output.py': 'import sys\nprint("terminal-stdout", flush=True)\nprint("terminal-stderr", file=sys.stderr, flush=True)\n',
  'failure.py': 'import sys\nprint("terminal-failure", file=sys.stderr, flush=True)\nsys.exit(7)\n',
  'live.py': 'import time\nprint("terminal-live-start", flush=True)\ntime.sleep(3)\nprint("terminal-live-end", flush=True)\n',
  'cancel.py': 'import subprocess, sys, time\nsubprocess.Popen([sys.executable, "-c", "from pathlib import Path; import time; time.sleep(3); Path(\\\"child-leak.txt\\\").write_text(\\\"unexpected\\\")"])\nprint("terminal-cancel-start", flush=True)\ntime.sleep(30)\n',
  'agent_command.py': 'from pathlib import Path\nPath("agent-result.txt").write_text("approved-agent-command")\nprint("agent-terminal-output", flush=True)\n',
};
await Promise.all(Object.entries(scripts).map(([name, content]) => writeFile(join(root, name), content)));
await writeFile(join(root, 'nested', 'cwd.py'), 'from pathlib import Path\nprint("terminal-cwd:" + Path.cwd().name)\n');
const git = (...args) => execFileSync('git', ['-C', root, ...args], { encoding: 'utf8' });
git('init', '-b', 'main');
git('add', '.');
git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.test', 'commit', '-m', 'Initial');
let fixtureError = '';
const fixture = spawn(pythonCommand(), ['tests/fixtures/desktop_provider.py', '--directory', directory], { cwd: projectRoot, stdio: ['ignore', 'ignore', 'pipe'] });
fixture.stderr.on('data', chunk => { fixtureError = (fixtureError + chunk).slice(-4000); });
const checks = [];
const errors = [];
let app;
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const exists = path => access(path).then(() => true, () => false);
const shellArg = value => process.platform === 'win32' ? `"${value.replaceAll('"', '\\"')}"` : `'${value.replaceAll("'", "'\\''")}'`;
const python = script => `${shellArg(pythonCommand())} ${shellArg(script)}`;

try {
  const deadline = Date.now() + 10000;
  while (!(await exists(join(directory, 'ready.json')))) {
    if (fixture.exitCode !== null) throw new Error(`Yerel sağlayıcı başlatılamadı: ${fixtureError}`);
    if (Date.now() > deadline) throw new Error('Yerel sağlayıcı başlatılamadı.');
    await pause(50);
  }
  const env = { ...process.env, FIRIK_DESKTOP_PYTHON: pythonCommand(), FIRIK_DESKTOP_CONFIG: join(directory, 'config.yaml'), FIRIK_DESKTOP_TEST_KEY: 'firik_llm_local_desktop_fixture' };
  delete env.ELECTRON_RUN_AS_NODE;
  delete env.FIRIK_DESKTOP_DEV_URL;
  app = await electron.launch({ args: [join(projectRoot, 'apps/desktop'), `--user-data-dir=${join(directory, 'electron-profile')}`], cwd: projectRoot, env, timeout: 30000 });
  const page = await app.firstWindow();
  page.on('pageerror', error => errors.push(error.message));
  await page.getByText('Python çekirdeği bağlı', { exact: true }).waitFor({ timeout: 20000 });
  await page.getByRole('button', { name: 'Kodlama ajanı', exact: true }).click();
  async function addProject(name, path) {
    await page.getByRole('button', { name: 'Proje ekle', exact: true }).click();
    await page.getByLabel('Proje adı', { exact: true }).fill(name);
    await page.getByLabel('Proje klasörü', { exact: true }).fill(path);
    await page.getByRole('button', { name: 'Projeyi ekle', exact: true }).click();
    await page.waitForFunction(expected => document.querySelector('select[aria-label="Çalışılacak proje"]')?.selectedOptions[0]?.textContent === expected, name);
    await page.getByLabel('Kodlama isteği').waitFor();
    return page.getByLabel('Çalışılacak proje').inputValue();
  }
  const projectId = await addProject('Terminal doğrulaması', root);
  const terminal = page.getByRole('region', { name: 'Proje terminali', exact: true });
  async function openTerminal() {
    if (!(await terminal.isVisible())) await page.getByRole('button', { name: 'Terminal', exact: true }).click();
    await terminal.waitFor();
  }
  const commands = id => page.evaluate(project => window.firik.terminal.list({ project_id: project }), id);
  async function waitCommand(id, predicate, timeout = 15000) {
    const until = Date.now() + timeout;
    while (true) {
      const result = await commands(projectId);
      const command = result.commands.find(row => row.command_id === id);
      if (command && predicate(command)) return command;
      if (Date.now() > until) throw new Error(`Terminal komutu beklenen duruma gelmedi: ${JSON.stringify(command)}`);
      await pause(70);
    }
  }
  async function run(command, cwd = '.') {
    await openTerminal();
    const before = new Set((await commands(projectId)).commands.map(row => row.command_id));
    await terminal.getByLabel('Terminal komutu', { exact: true }).fill(command);
    await terminal.getByLabel('Komut çalışma klasörü', { exact: true }).fill(cwd);
    await terminal.getByRole('button', { name: 'Komutu çalıştır', exact: true }).click();
    const until = Date.now() + 10000;
    while (true) {
      const result = (await commands(projectId)).commands.find(row => !before.has(row.command_id) && row.source === 'user');
      if (result) return result;
      if (Date.now() > until) throw new Error('Terminal komutu başlamadı.');
      await pause(70);
    }
  }
  const finished = row => !['starting', 'preparing', 'running', 'stopping'].includes(row.status);

  let command = await run(python('output.py'));
  let result = await waitCommand(command.command_id, finished);
  assert.equal(result.exit_code, 0);
  assert.equal(result.source, 'user');
  await terminal.getByRole('region', { name: 'Komut çıktısı', exact: true }).getByText('terminal-stdout', { exact: false }).waitFor();
  await terminal.getByRole('region', { name: 'Komut çıktısı', exact: true }).getByText('terminal-stderr', { exact: false }).waitFor();
  await page.getByRole('tab', { name: 'GitGod', exact: true }).click();
  assert.equal(await terminal.isVisible(), true);
  await page.getByRole('tab', { name: 'Ajan', exact: true }).click();
  await terminal.getByRole('region', { name: 'Komut çıktısı', exact: true }).getByText('terminal-stdout', { exact: false }).waitFor();
  await page.screenshot({ path: join(output, '01-user-command.png') });
  checks.push('manual shell command runs in the selected project, keeps stdout/stderr separate and remains visible across Ajan/GitGod tabs');

  command = await run(python('failure.py'));
  result = await waitCommand(command.command_id, finished);
  assert.equal(result.exit_code, 7);
  await terminal.getByText('terminal-failure', { exact: false }).waitFor();
  checks.push('nonzero command exit is preserved with stderr rather than presented as success');

  command = await run(python('cwd.py'), 'nested');
  result = await waitCommand(command.command_id, finished);
  assert.equal(result.exit_code, 0);
  assert.equal(result.cwd, 'nested');
  await terminal.getByText('terminal-cwd:nested', { exact: false }).waitFor();
  checks.push('explicit relative working directory is applied to a command inside a project path containing spaces');

  command = await run(python('live.py'));
  await terminal.getByText('terminal-live-start', { exact: false }).waitFor();
  result = (await commands(projectId)).commands.find(row => row.command_id === command.command_id);
  assert.equal(result.status, 'running');
  assert.equal(result.stdout.includes('terminal-live-end'), false);
  result = await waitCommand(command.command_id, finished);
  assert(result.stdout.includes('terminal-live-end'));
  checks.push('incremental command output reaches the Electron UI before the process exits');

  command = await run(python('cancel.py'));
  await terminal.getByText('terminal-cancel-start', { exact: false }).waitFor();
  await page.getByRole('button', { name: 'Komutu durdur', exact: true }).click();
  result = await waitCommand(command.command_id, finished);
  assert.equal(result.status, 'cancelled');
  await pause(3600);
  assert.equal(await exists(join(root, 'child-leak.txt')), false);
  checks.push('manual command cancellation terminates its process group and prevents a delayed child-process write');

  command = await run(python('output.py'));
  await waitCommand(command.command_id, finished);
  await page.getByLabel('Kodlama isteği').fill('Önceki istek korunur.');
  await terminal.getByRole('button', { name: 'Çıktıyı ajana ekle', exact: true }).click();
  const draft = await page.getByLabel('Kodlama isteği').inputValue();
  assert(draft.includes('Önceki istek korunur.'));
  assert(draft.includes('terminal-stdout') && draft.includes('terminal-stderr'));
  assert.equal(await page.locator('.coding-messages .workspace-message').count(), 0);
  checks.push('command output can be attached to the coding draft while preserving its text and requiring an explicit send');

  const secondProject = await addProject('İkinci terminal projesi', secondRoot);
  assert.notEqual(secondProject, projectId);
  await openTerminal();
  assert.equal((await commands(secondProject)).commands.length, 0);
  assert.equal(await terminal.getByText('terminal-stdout', { exact: false }).count(), 0);
  await page.getByLabel('Çalışılacak proje').selectOption(projectId);
  await openTerminal();
  assert((await commands(projectId)).commands.length >= 6);
  checks.push('terminal history and command results are scoped to the selected project');

  if (await terminal.isVisible()) await page.getByRole('button', { name: 'Terminal', exact: true }).click();
  await page.getByLabel('Kodlama çalışma modu').selectOption('assisted');
  await page.getByLabel('Kodlama isteği').fill('TERMINAL_SMOKE_AGENT_COMMAND: Terminal komutunu çalıştır.');
  await page.getByRole('button', { name: 'Gönder', exact: true }).click();
  await page.getByText('İşlem onayı: run_command', { exact: true }).waitFor({ timeout: 25000 });
  assert.equal(await exists(join(root, 'agent-result.txt')), false);
  assert.equal((await commands(projectId)).commands.filter(row => row.source === 'agent' && ['running', 'completed'].includes(row.status)).length, 0);
  await page.getByRole('button', { name: 'Onayla', exact: true }).click();
  await page.locator('.coding-messages').getByText(/Ajan komutu tamamlandı/).waitFor({ timeout: 25000 });
  assert.equal(await readFile(join(root, 'agent-result.txt'), 'utf8'), 'approved-agent-command');
  await openTerminal();
  const agent = (await commands(projectId)).commands.find(row => row.source === 'agent');
  assert(agent, 'Native agent run_command must be represented in terminal history.');
  result = await waitCommand(agent.command_id, finished);
  assert.equal(result.exit_code, 0);
  assert(result.stdout.includes('agent-terminal-output'));
  await terminal.getByRole('navigation', { name: 'Terminal komut geçmişi', exact: true }).locator(`[data-command-id="${agent.command_id}"]`).click();
  await terminal.getByText('agent-terminal-output', { exact: false }).waitFor();
  const fixtureState = await (await fetch(JSON.parse(await readFile(join(directory, 'ready.json'), 'utf8')).browser_origin + '/fixture-state')).json();
  assert(fixtureState.tools.some(row => row.name === 'run_command' && row.exit_code === 0));
  await page.screenshot({ path: join(output, '02-agent-command.png') });
  checks.push('the native coding agent runs a real shell command only after per-call approval, and the same command/result appears in terminal history without MCP');

  await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].setSize(940, 620));
  await openTerminal();
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  for (const locator of [terminal.getByLabel('Terminal komutu', { exact: true }), terminal.getByRole('button', { name: 'Komutu çalıştır', exact: true })]) {
    const bounds = await locator.boundingBox();
    assert(bounds && bounds.y >= 0 && bounds.y + bounds.height <= await page.evaluate(() => innerHeight));
  }
  await page.screenshot({ path: join(output, '03-terminal-compact.png') });
  checks.push('the built-in terminal remains usable at 940x620 without document overflow');

  assert.deepEqual(errors, []);
  await rm(join(output, 'failure.json'), { force: true });
  await rm(join(output, 'failure.png'), { force: true });
  await writeFile(join(output, 'verification.json'), JSON.stringify({ checks, errors, scope: 'real source Electron, native execution backend and isolated loopback provider; no live model or user data' }, null, 2));
  process.stdout.write(JSON.stringify({ checks, output }, null, 2) + '\n');
} catch (error) {
  await writeFile(join(output, 'failure.json'), JSON.stringify({ error: String(error), checks, errors, fixtureError }, null, 2));
  if (app) {
    const page = await app.firstWindow().catch(() => null);
    if (page) await page.screenshot({ path: join(output, 'failure.png') }).catch(() => {});
  }
  throw error;
} finally {
  if (app) await app.close();
  fixture.kill('SIGTERM');
}
