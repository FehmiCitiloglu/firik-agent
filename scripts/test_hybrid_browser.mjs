import { spawnSync } from 'node:child_process';
import { createRequire } from 'node:module';
import { dirname, join } from 'node:path';
import { projectRoot } from './hybrid_paths.mjs';

const require = createRequire(import.meta.url);
const extraArgs = process.argv.slice(2);
const result = spawnSync(process.execPath, [join(dirname(require.resolve('vitest/package.json')), 'vitest.mjs'), 'run', '--config',
  'apps/desktop/vitest.config.ts', 'apps/desktop/tests/browser-chromium.test.ts', ...extraArgs], {
  cwd: projectRoot, stdio: 'inherit', env: { ...process.env,
    FIRIK_HYBRID_BROWSER_INTEGRATION: '1', PLAYWRIGHT_BROWSERS_PATH: join(projectRoot, 'build/hybrid-browser') },
});
if ((result.status ?? 1) !== 0) {
  process.exitCode = result.status ?? 1;
} else if (extraArgs.length === 0) {
  const embedded = spawnSync(process.execPath, [join(projectRoot, 'scripts/test_embedded_browser.mjs')], {
    cwd: projectRoot,
    stdio: 'inherit',
  });
  process.exitCode = embedded.status ?? 1;
} else {
  process.exitCode = 0;
}
