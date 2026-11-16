import { describe, expect, it } from 'vitest';
import { parseEvent, parseParams } from '@firik/contracts';

describe('project terminal wire boundary', () => {
  it('bounds explicit commands without letting the renderer select extra execution authority', () => {
    const input = { project_id: 'project', command: 'npm test', cwd: '.', timeout: 300 };
    expect(parseParams('terminal.start', input)).toMatchObject(input);
    for (const extra of [
      { shell: '/bin/sh' }, { backend: 'host' }, { permissions: { allow_terminal: true } },
      { source: 'agent' }, { session_id: 'other-session' },
    ]) expect(() => parseParams('terminal.start', { ...input, ...extra })).toThrow();
    for (const invalid of [
      { command: '' }, { command: 'x'.repeat(8001) }, { cwd: 'x'.repeat(4097) },
      { timeout: 0 }, { timeout: 3601 }, { timeout: true },
    ]) expect(() => parseParams('terminal.start', { ...input, ...invalid })).toThrow();
  });

  it('binds inspection and cancellation to a registered project and exact command identity', () => {
    expect(parseParams('terminal.list', { project_id: 'project' })).toEqual({ project_id: 'project' });
    expect(parseParams('terminal.cancel', { project_id: 'project', command_id: 'command-1' }))
      .toEqual({ project_id: 'project', command_id: 'command-1' });
    for (const input of [{ command_id: 'command-1' }, { project_id: 'project' },
      { project_id: 'project', command_id: '' }, { project_id: 'project', command_id: 'command-1', pid: 123 }])
      expect(() => parseParams('terminal.cancel', input)).toThrow();
    expect(() => parseParams('terminal.list', { project_id: 'project', cwd: '/another-project' })).toThrow();
  });

  it('accepts typed terminal invalidations and rejects raw output additions to events', () => {
    const event = { backend_instance_id: 'backend', sequence: 1, kind: 'terminal',
      project_id: 'project', command_id: 'command-1', event_type: 'output' };
    expect(parseEvent(event)).toMatchObject(event);
    expect(() => parseEvent({ ...event, stdout: 'raw unreviewed output' })).toThrow();
  });
});
