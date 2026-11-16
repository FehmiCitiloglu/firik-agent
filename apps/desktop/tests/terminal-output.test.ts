import { describe, expect, it } from 'vitest';
import { terminalContext, terminalText } from '../src/renderer/terminal-output.js';

describe('captured command output', () => {
  it('keeps readable text while removing ANSI styles, hidden hyperlinks and control bytes', () => {
    expect(terminalText('\x1b[31mfailed\x1b[0m\r\n\x1b]8;;https://example.test\x07source.ts\x1b]8;;\x07\x00\n'))
      .toBe('failed\nsource.ts\n');
  });

  it('attaches a bounded result with actual command, status and both output streams', () => {
    const context = terminalContext({ command_id: 'c1', project_id: 'p1', source: 'user', command: 'python test.py',
      cwd: 'src', status: 'completed', exit_code: 7, stdout: 'collected tests', stderr: 'assertion failure', backend: 'host' });
    for (const text of ['python test.py', 'src', '7', 'collected tests', 'assertion failure']) expect(context).toContain(text);
    const large = terminalContext({ command_id: 'c2', project_id: 'p1', source: 'agent', command: 'test', cwd: '.',
      status: 'cancelled', exit_code: null, stdout: 'x'.repeat(30000), stderr: '', truncated: true });
    expect(large.length).toBeLessThan(12500);
    expect(large).toContain('Çıktı kısaltıldı');
    expect(large).toContain('Çıkış kodu: yok');
  });
});
