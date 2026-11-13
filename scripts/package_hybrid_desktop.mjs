import './build_desktop.mjs';
import { spawnSync } from 'node:child_process';
import { createRequire } from 'node:module';
import { dirname, join } from 'node:path';
import { pythonCommand, projectRoot } from './hybrid_paths.mjs';

const require = createRequire(import.meta.url);
function run(command, args) {
  const result = spawnSync(command, args, { cwd: projectRoot, stdio: 'inherit', env: {
    ...process.env, ELECTRON_BUILDER_CACHE: join(projectRoot, 'build/hybrid-builder-cache'),
  } });
  if (result.status !== 0) process.exit(result.status ?? 1);
}
run(process.execPath, ['scripts/prepare_hybrid_browser.mjs']);
run(pythonCommand(), ['scripts/build_hybrid_backend.py']);
// Electron 45+ installs its platform binary lazily instead of via postinstall.
run(process.execPath, [join(dirname(require.resolve('electron/package.json')), 'install.js')]);
run(process.execPath, [require.resolve('electron-builder/cli.js'), '--projectDir', 'apps/desktop',
  '--config', 'electron-builder.yml', '--dir', ...process.argv.slice(2)]);
