import { readFile, writeFile } from 'node:fs/promises';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { compile } from 'json-schema-to-typescript';
import { pythonCommand, projectRoot } from './hybrid_paths.mjs';

const exported = spawnSync(pythonCommand(), ['scripts/export_desktop_contracts.py'], {
  cwd: projectRoot, stdio: 'inherit',
});
if (exported.status !== 0) throw new Error('Python sözleşmeleri dışa aktarılamadı.');
const path = new URL('../packages/contracts/wire.schema.json', import.meta.url);
const schema = JSON.parse(await readFile(path, 'utf8'));
const types = await compile(schema, 'DesktopWireTypes', {
  bannerComment: '/* Generated from Python desktop_rpc/models.py. Do not edit. */',
  additionalProperties: false, style: { singleQuote: true },
});
const methods = Object.entries(schema['x-methods']).map(([method, pair]) =>
  `  '${method}': { params: ${pair.params}; result: ${pair.result} };`).join('\n');
await writeFile(fileURLToPath(new URL('../packages/contracts/src/wire.ts', import.meta.url)),
  `${types}\nexport interface DesktopMethods {\n${methods}\n}\n`);
