import { describe, expect, it } from 'vitest';
import { parseEvent, parseParams, parseResult } from '@firik/contracts';

describe('migrated workspace wire contracts', () => {
  it('bounds coding requests and keeps permissions in the existing host service', () => {
    const input = { project_id: 'project', session_id: 'session', prompt: 'main.py dosyasını açıkla', model: 'model' };
    expect(parseParams('coding.start', input)).toMatchObject(input);
    for (const extra of [{ cwd: '/another-project' }, { permissions: { allow_edits: true } }, { mode: 'auto' }])
      expect(() => parseParams('coding.start', { ...input, ...extra })).toThrow();
    expect(() => parseParams('coding.start', { ...input, prompt: 'x'.repeat(65537) })).toThrow();
    expect(() => parseParams('coding.control', { project_id: 'p', session_id: 's', action: 'approve', allowed: true })).toThrow();
  });

  it('accepts only a preview token for Git execution', () => {
    expect(parseParams('git.execute', { project_id: 'p', token: 'single-use-token', confirmed: true })).toMatchObject({ confirmed: true });
    expect(() => parseParams('git.execute', { project_id: 'p', token: 't', args: ['reset', '--hard'] })).toThrow();
    expect(() => parseParams('git.preview', { project_id: 'p', tool_id: 'diff', arguments: { staged: { nested: true } } })).toThrow();
  });

  it('binds voice controls to a scope and prevents key or audio fields on results', () => {
    const input = { conversation_id: 'chat', scope_id: 'scope', action: 'submit', text: 'Merhaba', review_id: 'review' };
    expect(parseParams('voice.command', input)).toMatchObject(input);
    expect(() => parseParams('voice.command', { ...input, action: 'approve' })).toThrow();
    expect(() => parseParams('voice.command', { ...input, scope_id: '' })).toThrow();
    const settings = { enabled: true, asr_provider: 'evren', tts_provider: 'system', language: 'tr', read_replies: true, playback_speed: 1 };
    expect(parseResult('voice.settings', settings)).toMatchObject(settings);
    expect(() => parseResult('voice.settings', { ...settings, api_key: 'private' })).toThrow();
    expect(() => parseParams('voice.settings', { ...settings, playback_speed: 5 })).toThrow();
    expect(parseEvent({ backend_instance_id: 'backend', sequence: 1, kind: 'coding', project_id: 'p', conversation_id: 's' })).toMatchObject({ kind: 'coding' });
  });
});
