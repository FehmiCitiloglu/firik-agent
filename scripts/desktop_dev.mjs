import { createServer } from 'vite';
import { spawn } from 'node:child_process';
import { resolve } from 'node:path';
import electron from 'electron';
import { projectRoot } from './hybrid_paths.mjs';
import './build_desktop.mjs';

const server = await createServer({ configFile: resolve(projectRoot, 'apps/desktop/vite.config.ts') });
await server.listen();
const child = spawn(electron, [resolve(projectRoot, 'apps/desktop')], {
  cwd: projectRoot, stdio: 'inherit', env: { ...process.env, FIRIK_DESKTOP_DEV_URL: 'http://127.0.0.1:5173' },
});
child.on('exit', async code => { await server.close(); process.exitCode = code ?? 1; });
for (const signal of ['SIGINT', 'SIGTERM']) process.once(signal, () => child.kill(signal));
