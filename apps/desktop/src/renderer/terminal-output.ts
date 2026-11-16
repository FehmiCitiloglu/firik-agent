import type { TerminalCommand } from '@firik/contracts';

/** Render captured text, never terminal escape sequences or hyperlinks. */
export function terminalText(text: string): string {
  return text
    .replace(/\x1b\][\s\S]*?(?:\x07|\x1b\\)/g, '')
    .replace(/\x1b\[[0-?]*[ -/]*[@-~]/g, '')
    .replace(/\r\n/g, '\n').replace(/\r/g, '\n')
    .replace(/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/g, '');
}

export function terminalContext(command: TerminalCommand): string {
  const stdout = terminalText(command.stdout ?? '');
  const stderr = terminalText(command.stderr ?? '');
  // Leave room for the user's request within the coding composer's 64 KiB cap.
  const output = [stdout && `stdout:\n${stdout}`, stderr && `stderr:\n${stderr}`].filter(Boolean).join('\n\n');
  const limit = 12000;
  return [
    'Aşağıdaki komut sonucunu incele ve gereken sonraki adımı öner:',
    `Komut: ${terminalText(command.command)}`,
    `Çalışma klasörü: ${command.cwd || '.'}`,
    `Durum: ${command.status ?? 'starting'} · Çıkış kodu: ${command.exit_code ?? 'yok'} · Yürütme: ${command.backend ?? 'host'}`,
    output.slice(0, limit),
    output.length > limit || command.truncated ? '[Çıktı kısaltıldı.]' : '',
  ].filter(Boolean).join('\n\n');
}
