import { describe, it, expect } from 'vitest';
import { parseParams, parseResult, parseEvent } from '@firik/contracts';
import {
  DEFAULT_BROWSER_PANEL_WIDTH,
  clampBrowserPanelWidth,
  disconnect,
  useUI,
} from '../src/renderer/store.js';

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
  it('clamps browser panel width cleanly and validates resize contracts', () => {
    expect(useUI.getState().browserPanelWidth).toBe(DEFAULT_BROWSER_PANEL_WIDTH);
    expect(useUI.getState().searchEngine).toBe('duckduckgo');
    expect(useUI.getState().browserFitViewport).toBe(false);
    expect(useUI.getState().browserEngine).toBe('embedded');

    expect(clampBrowserPanelWidth(120, 1000)).toBe(320);
    expect(clampBrowserPanelWidth(900, 1000)).toBe(734);
    expect(clampBrowserPanelWidth(Number.NaN)).toBe(DEFAULT_BROWSER_PANEL_WIDTH);

    useUI.getState().setBrowserPanelWidth(640, 1100);
    expect(useUI.getState().browserPanelWidth).toBe(640);
    useUI.getState().resetBrowserPanelWidth(1100);
    expect(useUI.getState().browserPanelWidth).toBe(DEFAULT_BROWSER_PANEL_WIDTH);
    useUI.getState().toggleSidebar();
    expect(useUI.getState().sidebarCollapsed).toBe(true);
    useUI.getState().setSearchEngine('startpage');
    expect(useUI.getState().searchEngine).toBe('startpage');
    useUI.getState().setBrowserFitViewport(true);
    expect(useUI.getState().browserFitViewport).toBe(true);
    useUI.getState().setBrowserEngine('playwright');
    expect(useUI.getState().browserEngine).toBe('playwright');

    useUI.getState().toggleSidebar(false);
    useUI.getState().setSearchEngine('duckduckgo');
    useUI.getState().setBrowserFitViewport(false);
    useUI.getState().setBrowserEngine('embedded');

    expect(
      parseParams('browser.userCommand', {
        conversation_id: 'chat',
        command: 'resize',
        width: 640,
        height: 480,
      }),
    ).toMatchObject({ command: 'resize', width: 640, height: 480 });
    expect(() =>
      parseParams('browser.userCommand', {
        conversation_id: 'chat',
        command: 'resize',
        width: 99999,
      }),
    ).toThrow();
  });
});
