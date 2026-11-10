import { spawnSync } from 'node:child_process';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { dirname, join, relative } from 'node:path';
import { projectRoot } from './hybrid_paths.mjs';

const require = createRequire(import.meta.url);
const cache = join(projectRoot, 'build/hybrid-browser');
const core = require.resolve('playwright-core/package.json');
const metadata = JSON.parse(await readFile(core, 'utf8'));
const browsers = JSON.parse(await readFile(join(dirname(core), 'browsers.json'), 'utf8'));
const chromium = browsers.browsers.find(browser => browser.name === 'chromium');
await mkdir(cache, { recursive: true });
const install = spawnSync(process.execPath, [join(dirname(require.resolve('playwright/package.json')), 'cli.js'), 'install', '--no-shell',
  ...(process.argv.includes('--with-deps') ? ['--with-deps'] : []), 'chromium'], {
  cwd: projectRoot, stdio: 'inherit', env: { ...process.env, PLAYWRIGHT_BROWSERS_PATH: cache },
});
if (install.status !== 0) process.exit(install.status ?? 1);
process.env.PLAYWRIGHT_BROWSERS_PATH = cache;
const { chromium: driver } = await import('playwright');
const executable = driver.executablePath();
const { access } = await import('node:fs/promises');
await access(executable);
await writeFile(join(cache, 'firik-browser-manifest.json'), JSON.stringify({
  driver_version: metadata.version, revision: chromium.revision, browser_version: chromium.browserVersion,
  platform: process.platform, arch: process.arch, executable: relative(cache, executable),
}, null, 2) + '\n');
console.log(`Prepared matching Chromium ${chromium.revision} for Playwright ${metadata.version}.`);
