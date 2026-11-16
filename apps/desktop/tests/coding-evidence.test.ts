import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { CodingEvidence, testEvidenceStatus } from '../src/renderer/coding-evidence.js';
import { useUI } from '../src/renderer/store.js';

describe('coding evidence presentation', () => {
  it('never presents stale or unexecuted evidence as a current success', () => {
    expect(testEvidenceStatus({ passed: true, fresh: false, status: 'stale' }).label).toBe('Yeniden doğrulanmalı');
    expect(testEvidenceStatus({ passed: true, stale: true }).tone).toBe('warning');
    expect(testEvidenceStatus({ passed: true, executed: false }).label).toBe('Çalıştırılmadı');
    expect(testEvidenceStatus({ executed: false, status: 'blocked', passed: false, fresh: false }).label).toBe('Engellendi');
    expect(testEvidenceStatus({ passed: true, status: 'unverified' }).label).toBe('Doğrulama eksik');
  });

  it('keeps recorded failures authoritative and missing legacy results unknown', () => {
    expect(testEvidenceStatus({ passed: true, exit_code: 1 }).label).toBe('Başarısız');
    expect(testEvidenceStatus({ passed: false, status: 'passed' }).label).toBe('Başarısız');
    expect(testEvidenceStatus({ passed: false, status: 'failed', fresh: false }).label).toBe('Başarısız');
    expect(testEvidenceStatus({ passed: true, timed_out: true }).label).toBe('Süre aşıldı');
    expect(testEvidenceStatus({ status: 'completed', exit_code: 0 }).label).toBe('Sonuç bilinmiyor');
    expect(testEvidenceStatus({}).tone).toBe('neutral');
    expect(testEvidenceStatus({ passed: true, fresh: true }).label).toBe('Başarılı');
    expect(testEvidenceStatus({ passed: true }).label).toBe('Başarılı · Güncellik bilinmiyor');
  });

  it('renders stderr together with stdout and explains stale results', () => {
    const html = renderToStaticMarkup(createElement(CodingEvidence, { kind: 'tests', text: JSON.stringify([
      { passed: false, command: 'pytest', stdout: 'test collection', stderr: 'assertion failure', exit_code: 1 },
      { passed: true, fresh: false, status: 'stale', stale_reason: 'Kaynak değişti', evidence_id: 'proof-1' },
    ]) }));
    for (const expected of ['Standart çıktı', 'test collection', 'Hata çıktısı', 'assertion failure', 'Yeniden doğrulanmalı', 'Kaynak değişti', 'proof-1'])
      expect(html).toContain(expected);
    expect(html).not.toContain('>Başarılı<');
  });

  it('preserves older text records without assigning a fabricated outcome', () => {
    const html = renderToStaticMarkup(createElement(CodingEvidence, { kind: 'tests', text: 'legacy command output' }));
    expect(html).toContain('legacy command output');
    expect(html).not.toContain('>Başarılı<');
    expect(html).not.toContain('>Başarısız<');
  });

  it('routes a new coding session to the selected project without losing chat or drafts', () => {
    const previous = useUI.getState();
    try {
      useUI.setState({ project: 'project-a', workspace: 'coding', codingTool: 'git', active: 'chat-a', drafts: { 'coding:session-a': 'Taslak' } });
      useUI.getState().openCodingSession();
      expect(useUI.getState()).toMatchObject({ workspace: 'coding', codingTool: 'agent', active: 'chat-a',
        codingNavigation: { projectId: 'project-a', recordId: null }, drafts: { 'coding:session-a': 'Taslak' } });
      useUI.getState().openCodingSession('record-a');
      expect(useUI.getState().codingNavigation).toEqual({ projectId: 'project-a', recordId: 'record-a' });
    } finally { useUI.setState(previous); }
  });
});
