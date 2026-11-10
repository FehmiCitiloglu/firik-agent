import './generate_desktop_contracts.mjs';
import { build } from 'esbuild';
import { build as viteBuild } from 'vite';
import { resolve } from 'node:path';
import { projectRoot } from './hybrid_paths.mjs';

export async function buildHost() {
  await build({
    absWorkingDir: projectRoot,
    entryPoints: {
      'main/index': 'apps/desktop/src/main/index.ts',
      'preload/index': 'apps/desktop/src/preload/index.ts',
      'browser-worker/index': 'apps/desktop/src/browser-worker/index.ts',
    },
    outdir: 'apps/desktop/out', outExtension: { '.js': '.cjs' }, bundle: true,
    platform: 'node', format: 'cjs', target: 'node22',
    external: ['electron', 'playwright', 'playwright-core'], sourcemap: true,
  });
}
await buildHost();
await viteBuild({ configFile: resolve(projectRoot, 'apps/desktop/vite.config.ts') });
