import { realpath, stat } from 'node:fs/promises';
import { basename, isAbsolute } from 'node:path';
import type { OpenDialogOptions, OpenDialogReturnValue } from 'electron';
import type { ProjectDirectorySelection, SelectProjectDirectoryParams } from '@firik/contracts/desktop-api';

export function parseProjectDirectoryParams(input: unknown): SelectProjectDirectoryParams {
  if (!input || typeof input !== 'object' || Array.isArray(input)) {
    throw new Error('Geçersiz proje klasörü seçimi.');
  }
  const value = input as Record<string, unknown>;
  if (Object.keys(value).some(key => key !== 'default_path')) {
    throw new Error('Geçersiz proje klasörü seçimi.');
  }
  if (value.default_path === undefined) return {};
  if (typeof value.default_path !== 'string' || value.default_path.length > 4096
      || value.default_path.includes('\0') || !isAbsolute(value.default_path)) {
    throw new Error('Başlangıç klasörü için geçerli bir tam yol gerekir.');
  }
  return { default_path: value.default_path };
}

// Only the native dialog chooses a path; the renderer cannot supply dialog
// options, select files, or request arbitrary filesystem operations.
export function createProjectDirectoryPicker(
  openDialog: (options: OpenDialogOptions) => Promise<OpenDialogReturnValue>,
): (input: unknown) => Promise<ProjectDirectorySelection> {
  let pending: Promise<ProjectDirectorySelection> | null = null;
  return async input => {
    const params = parseProjectDirectoryParams(input);
    if (pending) return pending;
    pending = (async () => {
      const result = await openDialog({
        title: 'Proje klasörünü seçin',
        buttonLabel: 'Klasörü seç',
        properties: ['openDirectory'],
        ...(params.default_path ? { defaultPath: params.default_path } : {}),
      });
      if (result.canceled || result.filePaths.length === 0) {
        return { canceled: true, path: null, name: null };
      }
      const selected = result.filePaths[0];
      if (!isAbsolute(selected)) throw new Error('Seçilen proje klasörünün tam yolu geçersiz.');
      const path = await realpath(selected);
      if (!(await stat(path)).isDirectory()) throw new Error('Lütfen bir proje klasörü seçin.');
      return { canceled: false, path, name: basename(path) || path };
    })();
    try {
      return await pending;
    } finally {
      pending = null;
    }
  };
}
