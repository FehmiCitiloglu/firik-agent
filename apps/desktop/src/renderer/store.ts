import { create } from 'zustand';
import type { ApprovalState, RunState } from '@firik/contracts';

interface UIState {
  active: string | null;
  drafts: Record<string, string>;
  runs: Record<string, RunState>;
  approvals: Record<string, ApprovalState>;
  settingsOpen: boolean;
  error: string | null;
  disconnected: boolean;
  select: (id: string) => void;
  draft: (id: string, text: string) => void;
  updateRun: (run: RunState) => void;
}

export const useUI = create<UIState>(set => ({
  active: null, drafts: {}, runs: {}, approvals: {}, settingsOpen: false, error: null, disconnected: false,
  select: id => set({ active: id }),
  draft: (id, text) => set(state => ({ drafts: { ...state.drafts, [id]: text } })),
  updateRun: run => set(state => {
    const previous = state.runs[run.conversation_id];
    if (previous && previous.generation > run.generation) return {};
    if (previous?.run_id === run.run_id && previous.status !== 'running' && run.status === 'running') return {};
    return { runs: { ...state.runs, [run.conversation_id]: run } };
  }),
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
