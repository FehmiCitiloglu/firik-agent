import { existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

export const projectRoot = fileURLToPath(new URL('../', import.meta.url));
export function pythonCommand() {
  if (process.env.FIRIK_DESKTOP_PYTHON) return process.env.FIRIK_DESKTOP_PYTHON;
  const local = fileURLToPath(new URL(process.platform === 'win32'
    ? '../.venv/Scripts/python.exe' : '../.venv/bin/python', import.meta.url));
  return existsSync(local) ? local : process.platform === 'win32' ? 'python' : 'python3';
}
