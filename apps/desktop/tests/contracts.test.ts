import { describe, it, expect } from 'vitest';
import { parseParams, parseResult, parseEvent } from '@firik/contracts';
import { disconnect, useUI } from '../src/renderer/store.js';

describe('desktop wire boundary', () => {
  it('rejects extra authority fields and incompatible protocol versions', () => {
    expect(() => parseParams('chat.start', { conversation_id: 'chat', prompt: 'hello', model: 'model',
      reasoning_effort: null, approval: true })).toThrow();
    expect(() => parseParams('system.hello', { protocol_version: 2 })).toThrow();
    expect(() => parseParams('run.cancel', { run_id: 42 })).toThrow();
  });
  it('rejects accidental credential exposure and malformed backend events', () => {
    expect(() => parseResult('settings.get', { base_url: 'https://example.test', default_model: 'model',
      timeout: 30, has_api_key: true, api_key: 'must-not-return' })).toThrow();
    expect(() => parseEvent({ kind: 'unknown', sequence: 1 })).toThrow();
  });
  it('does not turn a completed run back into running when RPC/event delivery races', () => {
    const run = { conversation_id: 'test-chat', run_id: 'first', generation: 1, status: 'completed' as const, error: null };
    useUI.getState().updateRun(run);
    useUI.getState().updateRun({ ...run, status: 'running' });
    expect(useUI.getState().runs['test-chat'].status).toBe('completed');
    useUI.getState().updateRun({ ...run, run_id: 'second', generation: 2, status: 'running' });
    expect(useUI.getState().runs['test-chat'].run_id).toBe('second');
    useUI.getState().updateRun(run);
    expect(useUI.getState().runs['test-chat'].run_id).toBe('second');
  });
  it('clears pending authority and active runs when the backend disconnects', () => {
    useUI.getState().updateRun({ conversation_id: 'lost-chat', run_id: 'lost-run', generation: 3, status: 'running', error: null });
    useUI.setState({ approvals: { pending: { approval_id: 'pending', run_id: 'lost-run',
      conversation_id: 'lost-chat', tool_name: 'fixture', preview: 'fixture' } } });
    disconnect('Backend bağlantısı kesildi.');
    expect(useUI.getState().runs['lost-chat'].status).toBe('failed');
    expect(useUI.getState().approvals).toEqual({});
    expect(useUI.getState().disconnected).toBe(true);
  });
});
