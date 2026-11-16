import { mkdtemp, mkdir, realpath, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { OpenDialogReturnValue } from 'electron';
import { createProjectDirectoryPicker, parseProjectDirectoryParams } from '../src/main/project-directory-picker.js';

const temporaryDirectories: string[] = [];
async function fixture() {
  const directory = await mkdtemp(join(tmpdir(), 'firik-project-picker-'));
  temporaryDirectories.push(directory);
  return directory;
}

afterEach(async () => {
  await Promise.all(temporaryDirectories.splice(0).map(path => rm(path, { recursive: true, force: true })));
});

describe('native project directory selection', () => {
  it('selects an existing Unicode directory through a directory-only native dialog', async () => {
    const root = await fixture();
    const project = join(root, 'Türkçe proje');
    await mkdir(project);
    const openDialog = vi.fn().mockResolvedValue({ canceled: false, filePaths: [project] });
    const choose = createProjectDirectoryPicker(openDialog);
    await expect(choose({ default_path: root })).resolves.toEqual({
      canceled: false, path: await realpath(project), name: 'Türkçe proje',
    });
    expect(openDialog).toHaveBeenCalledWith({
      title: 'Proje klasörünü seçin', buttonLabel: 'Klasörü seç',
      properties: ['openDirectory'], defaultPath: root,
    });
  });

  it('returns cancellation without registering or fabricating a project path', async () => {
    const choose = createProjectDirectoryPicker(vi.fn().mockResolvedValue({ canceled: true, filePaths: [] }));
    await expect(choose({})).resolves.toEqual({ canceled: true, path: null, name: null });
  });

  it('rejects untrusted dialog options and invalid initial paths before opening', async () => {
    const openDialog = vi.fn();
    const choose = createProjectDirectoryPicker(openDialog);
    for (const input of [null, [], { default_path: 42 }, { default_path: 'relative/path' },
      { default_path: '/bad\0path' }, { default_path: `/${'a'.repeat(4096)}` },
      { properties: ['openFile'] }, { default_path: '/tmp', extra: true }]) {
      await expect(choose(input)).rejects.toThrow();
    }
    expect(parseProjectDirectoryParams({})).toEqual({});
    expect(openDialog).not.toHaveBeenCalled();
  });

  it('rejects files and unavailable selections and recovers for the next dialog', async () => {
    const root = await fixture();
    const file = join(root, 'source.ts');
    await writeFile(file, 'export {};');
    const openDialog = vi.fn()
      .mockResolvedValueOnce({ canceled: false, filePaths: [file] })
      .mockResolvedValueOnce({ canceled: false, filePaths: [join(root, 'missing')] })
      .mockResolvedValueOnce({ canceled: true, filePaths: [] });
    const choose = createProjectDirectoryPicker(openDialog);
    await expect(choose({})).rejects.toThrow('Lütfen bir proje klasörü seçin.');
    await expect(choose({})).rejects.toThrow();
    await expect(choose({})).resolves.toEqual({ canceled: true, path: null, name: null });
  });

  it('coalesces repeated clicks into one native dialog and permits reopening after cancellation', async () => {
    let finish!: (value: OpenDialogReturnValue) => void;
    const openDialog = vi.fn().mockImplementation(() => new Promise<OpenDialogReturnValue>(resolve => { finish = resolve; }));
    const choose = createProjectDirectoryPicker(openDialog);
    const first = choose({});
    const second = choose({});
    expect(openDialog).toHaveBeenCalledTimes(1);
    finish({ canceled: true, filePaths: [] });
    expect(await first).toEqual(await second);
    const third = choose({});
    expect(openDialog).toHaveBeenCalledTimes(2);
    finish({ canceled: true, filePaths: [] });
    await third;
  });
});
