import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Command, History, Plus, Search } from 'lucide-react';
import type { CodingState } from '@firik/contracts';
import { Button } from './components/ui/button.js';
import { codingStatusName } from './coding-evidence.js';
import { useUI } from './store.js';

export function ProjectNavigation() {
  const projectId = useUI(state => state.project);
  const disconnected = useUI(state => state.disconnected);
  const navigation = useUI(state => state.codingNavigation);
  const [search, setSearch] = useState('');
  // Observe the workspace's cache without opening a session from the sidebar.
  const initial = useQuery<CodingState>({ queryKey: ['coding-open', projectId], enabled: false });
  const current = useQuery<CodingState>({ queryKey: ['coding', projectId, initial.data?.session_id], enabled: false });
  const data = current.data ?? initial.data;
  const history = useQuery({ queryKey: ['coding-history', projectId],
    queryFn: () => window.firik.coding.history({ project_id: projectId! }), enabled: !!projectId && !disconnected });
  const records = history.data?.records ?? [];
  const filtered = records.filter(row => `${row.title} ${codingStatusName(row.status)}`.toLocaleLowerCase('tr').includes(search.toLocaleLowerCase('tr')));
  const disabled = disconnected || !!data?.busy || !!navigation;

  return <>
    <Button className="new-chat coding-new-session" disabled={disabled || !projectId} onClick={() => useUI.getState().openCodingSession()}>
      <Plus size={17} /> Yeni kodlama oturumu <span className="shortcut"><Command size={11} /> N</span>
    </Button>
    <div className="history-search"><Search size={14} /><input aria-label="Kodlama oturumlarında ara" placeholder="Oturumlarda ara" value={search} onChange={event => setSearch(event.target.value)} /></div>
    <div className="section-caption">PROJE OTURUMLARI <span>{records.length}</span></div>
    <nav className="history-list coding-history" aria-label="Kodlama oturumları">
      {history.isPending && projectId && <p className="history-empty">Oturumlar yükleniyor…</p>}
      {history.isError && <p className="history-empty" role="alert">Oturumlar yüklenemedi. <button onClick={() => void history.refetch()}>Yeniden dene</button></p>}
      {filtered.map(row => <button key={row.record_id} data-record-id={row.record_id} aria-current={data?.record_id === row.record_id ? 'true' : undefined}
        disabled={disabled} className={`history-item ${data?.record_id === row.record_id ? 'selected' : ''}`}
        onClick={() => useUI.getState().openCodingSession(row.record_id)}>
        <History size={15} /><span className="coding-history-text"><span>{row.title}</span><small>{codingStatusName(row.status)}{row.created_at && ` · ${new Date(row.created_at).toLocaleDateString('tr-TR')}`}</small></span>
      </button>)}
      {!history.isPending && !history.isError && records.length === 0 && <p className="history-empty">Bu projede henüz kodlama çalışması yok. Yeni bir oturumla başlayın.</p>}
      {records.length > 0 && filtered.length === 0 && <p className="history-empty">Eşleşen oturum yok. <button onClick={() => setSearch('')}>Aramayı temizle</button></p>}
      {records.length >= 50 && <p className="history-empty">Son 50 çalışma gösteriliyor.</p>}
    </nav>
  </>;
}
