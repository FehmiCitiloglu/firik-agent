import { create } from 'zustand';
import type { ApprovalState, RunState } from '@firik/contracts';
import type { BrowserEngineMode } from '@firik/contracts/desktop-api';

export const DEFAULT_BROWSER_PANEL_WIDTH = 480;
export const MIN_BROWSER_PANEL_WIDTH = 320;
export const MIN_CHAT_COLUMN_WIDTH = 260;
export const BROWSER_RESIZER_WIDTH = 6;

export type SearchEngine = 'duckduckgo' | 'google' | 'startpage' | 'bing';

const VALID_SEARCH_ENGINES = new Set<SearchEngine>(['duckduckgo', 'google', 'startpage', 'bing']);

const STORAGE_KEYS = {
  browserPanelWidth: 'firik.browserPanelWidth',
  sidebarCollapsed: 'firik.sidebarCollapsed',
  searchEngine: 'firik.searchEngine',
  browserFitViewport: 'firik.browserFitViewport',
} as const;

export function clampBrowserPanelWidth(desiredWidth: number, workspaceWidth?: number): number {
  const base = Number.isFinite(desiredWidth) ? Math.round(desiredWidth) : DEFAULT_BROWSER_PANEL_WIDTH;
  if (!workspaceWidth || !Number.isFinite(workspaceWidth) || workspaceWidth <= 0) {
    return Math.max(MIN_BROWSER_PANEL_WIDTH, Math.min(960, base));
  }
  const available = Math.floor(workspaceWidth - MIN_CHAT_COLUMN_WIDTH - BROWSER_RESIZER_WIDTH);
  const maxAllowed = Math.max(220, available);
  const minAllowed = Math.min(MIN_BROWSER_PANEL_WIDTH, maxAllowed);
  return Math.min(maxAllowed, Math.max(minAllowed, base));
}

function getBrowserStorage(): Storage | null {
  try {
    if (typeof window === 'undefined' || !window.localStorage) return null;
    return window.localStorage;
  } catch {
    return null;
  }
}

function readStorage(key: string): string | null {
  try {
    return getBrowserStorage()?.getItem(key) ?? null;
  } catch {
    return null;
  }
}

function writeStorage(key: string, value: string): void {
  try {
    getBrowserStorage()?.setItem(key, value);
  } catch {
    // ignore storage errors in restricted environments
  }
}

function readStoredNumber(key: string, fallback: number): number {
  const raw = readStorage(key);
  if (raw === null) return fallback;
  const parsed = Number(raw);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function readStoredBool(key: string, fallback: boolean): boolean {
  const raw = readStorage(key);
  if (raw === 'true') return true;
  if (raw === 'false') return false;
  return fallback;
}

function readStoredSearchEngine(): SearchEngine {
  const raw = readStorage(STORAGE_KEYS.searchEngine);
  if (raw && VALID_SEARCH_ENGINES.has(raw as SearchEngine)) {
    return raw as SearchEngine;
  }
  return 'duckduckgo';
}

interface UIState {
  active: string | null;
  drafts: Record<string, string>;
  runs: Record<string, RunState>;
  approvals: Record<string, ApprovalState>;
  settingsOpen: boolean;
  browserOpen: boolean;
  browserPanelWidth: number;
  sidebarCollapsed: boolean;
  searchEngine: SearchEngine;
  browserFitViewport: boolean;
  browserEngine: BrowserEngineMode;
  error: string | null;
  disconnected: boolean;
  select: (id: string) => void;
  draft: (id: string, text: string) => void;
  updateRun: (run: RunState) => void;
  setBrowserPanelWidth: (width: number, workspaceWidth?: number) => void;
  resetBrowserPanelWidth: (workspaceWidth?: number) => void;
  toggleSidebar: (collapsed?: boolean) => void;
  setSearchEngine: (engine: SearchEngine) => void;
  setBrowserFitViewport: (fit: boolean) => void;
  setBrowserEngine: (engine: BrowserEngineMode) => void;
}

export const useUI = create<UIState>(set => ({
  active: null,
  drafts: {},
  runs: {},
  approvals: {},
  settingsOpen: false,
  browserOpen: false,
  browserPanelWidth: clampBrowserPanelWidth(
    readStoredNumber(STORAGE_KEYS.browserPanelWidth, DEFAULT_BROWSER_PANEL_WIDTH),
  ),
  sidebarCollapsed: readStoredBool(STORAGE_KEYS.sidebarCollapsed, false),
  searchEngine: readStoredSearchEngine(),
  browserFitViewport: readStoredBool(STORAGE_KEYS.browserFitViewport, false),
  browserEngine: 'embedded',
  error: null,
  disconnected: false,
  select: id => set({ active: id }),
  draft: (id, text) => set(state => ({ drafts: { ...state.drafts, [id]: text } })),
  updateRun: run => set(state => {
    const previous = state.runs[run.conversation_id];
    if (previous && previous.generation > run.generation) return {};
    if (previous?.run_id === run.run_id && previous.status !== 'running' && run.status === 'running') return {};
    return { runs: { ...state.runs, [run.conversation_id]: run } };
  }),
  setBrowserPanelWidth: (width, workspaceWidth) => set(() => {
    const nextWidth = clampBrowserPanelWidth(width, workspaceWidth);
    writeStorage(STORAGE_KEYS.browserPanelWidth, String(nextWidth));
    return { browserPanelWidth: nextWidth };
  }),
  resetBrowserPanelWidth: workspaceWidth => set(() => {
    const nextWidth = clampBrowserPanelWidth(DEFAULT_BROWSER_PANEL_WIDTH, workspaceWidth);
    writeStorage(STORAGE_KEYS.browserPanelWidth, String(nextWidth));
    return { browserPanelWidth: nextWidth };
  }),
  toggleSidebar: collapsed => set(state => {
    const next = typeof collapsed === 'boolean' ? collapsed : !state.sidebarCollapsed;
    writeStorage(STORAGE_KEYS.sidebarCollapsed, String(next));
    return { sidebarCollapsed: next };
  }),
  setSearchEngine: engine => set(() => {
    const next: SearchEngine = VALID_SEARCH_ENGINES.has(engine) ? engine : 'duckduckgo';
    writeStorage(STORAGE_KEYS.searchEngine, next);
    return { searchEngine: next };
  }),
  setBrowserFitViewport: fit => set(() => {
    const next = Boolean(fit);
    writeStorage(STORAGE_KEYS.browserFitViewport, String(next));
    return { browserFitViewport: next };
  }),
  setBrowserEngine: engine => set(() => ({
    browserEngine: engine === 'playwright' ? 'playwright' : 'embedded',
  })),
}));

export function showError(error: unknown) {
  useUI.setState({ error: error instanceof Error ? error.message : 'İşlem tamamlanamadı.' });
}

export function disconnect(message: string) {
  useUI.setState(state => ({
    disconnected: true, error: message, approvals: {},
    runs: Object.fromEntries(Object.entries(state.runs).map(([id, run]) => [id,
      run.status === 'running' ? { ...run, status: 'failed' as const, error: message } : run])),
  }));
}
