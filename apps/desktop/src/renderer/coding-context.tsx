import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowLeft, FileCode2, Folder, LoaderCircle, RefreshCw, Search, X } from 'lucide-react';
import type { DesktopMethods } from '@firik/contracts';
import { Button } from './components/ui/button.js';
import { showError, useUI } from './store.js';

type Preferences = DesktopMethods['coding.preferences']['result'];
const permissionLabels: { key: keyof Preferences; label: string; description: string }[] = [
  { key: 'allow_reads', label: 'Dosyaları oku', description: 'Ajan proje dosyalarını inceleyebilir.' },
  { key: 'allow_edits', label: 'Dosyaları düzenle', description: 'Uygulama modlarında proje dosyalarını değiştirebilir.' },
  { key: 'allow_terminal', label: 'Komut çalıştır', description: 'İşlem onayından sonra proje komutlarını çalıştırabilir.' },
  { key: 'allow_tests', label: 'Test çalıştır', description: 'İşlem onayından sonra test ve doğrulama komutlarını çalıştırabilir.' },
];

export function CodingPermissions({ projectId, busy }: { projectId: string; busy: boolean }) {
  const client = useQueryClient();
  const disconnected = useUI(state => state.disconnected);
  const preferences = useQuery({ queryKey: ['coding-preferences', projectId], queryFn: () => window.firik.coding.preferences({ project_id: projectId }), enabled: !disconnected });
  const [draft, setDraft] = useState<Preferences | null>(null);
  const [saved, setSaved] = useState(false);
  useEffect(() => { if (preferences.data) setDraft(preferences.data); }, [preferences.data]);
  const save = useMutation({ mutationFn: () => window.firik.coding.updatePreferences({ project_id: projectId, ...draft! }),
    onSuccess: async result => {
      client.setQueryData(['coding-preferences', projectId], result); setDraft(result); setSaved(true);
      await Promise.all([client.invalidateQueries({ queryKey: ['project-files', projectId] }), client.invalidateQueries({ queryKey: ['project-file', projectId] })]);
    }, onError: showError });
  return <section className="coding-permissions" aria-label="Proje çalışma izinleri">
    <p>Bu projede ajanın kullanabileceği araçları seçin. Projeye sor ve Planla modları dosya değiştirmez.</p>
    {preferences.isPending && <p role="status">İzinler yükleniyor…</p>}
    {preferences.isError && <p role="alert">İzinler yüklenemedi. <button onClick={() => void preferences.refetch()}>Yeniden dene</button></p>}
    {draft && <form onSubmit={event => { event.preventDefault(); if (!busy && !save.isPending && !disconnected) save.mutate(); }}>
      {permissionLabels.map(item => <label key={item.key}><input type="checkbox" checked={draft[item.key]} disabled={busy || save.isPending || disconnected}
        onChange={event => { setDraft({ ...draft, [item.key]: event.target.checked }); setSaved(false); }} /><span><strong>{item.label}</strong><small>{item.description}</small></span></label>)}
      <p className="muted">Proje politikası bu izinleri daraltabilir. Kaydedildikten sonra etkili izinler gösterilir. İzinler sonraki çalışmalarda geçerlidir.</p>
      <Button disabled={busy || save.isPending || disconnected}>{save.isPending ? 'Kaydediliyor…' : 'İzinleri kaydet'}</Button>
      {busy && <p role="status">İzinleri değiştirmek için çalışmanın bitmesini bekleyin.</p>}
      {saved && <p role="status">Proje izinleri kaydedildi.</p>}
    </form>}
  </section>;
}

export function ContextBrowser({ projectId, selected, onChange, canRead }: { projectId: string; selected: string[]; onChange: (paths: string[]) => void; canRead: boolean }) {
  const disconnected = useUI(state => state.disconnected);
  const [path, setPath] = useState('');
  const [search, setSearch] = useState('');
  const [query, setQuery] = useState('');
  const [preview, setPreview] = useState('');
  useEffect(() => { const timer = setTimeout(() => setQuery(search.trim()), 180); return () => clearTimeout(timer); }, [search]);
  const files = useQuery({ queryKey: ['project-files', projectId, path, query], queryFn: () => window.firik.projects.files({ project_id: projectId, path, query }), enabled: canRead && !disconnected, staleTime: 0 });
  const file = useQuery({ queryKey: ['project-file', projectId, preview], queryFn: () => window.firik.projects.read({ project_id: projectId, path: preview }), enabled: canRead && !!preview && !disconnected, staleTime: 0 });
  function toggle(filePath: string) {
    if (selected.includes(filePath)) onChange(selected.filter(item => item !== filePath));
    else if (selected.length < 24) onChange([...selected, filePath]);
  }
  function navigate(next: string) { setPath(next); setSearch(''); setQuery(''); setPreview(''); }
  const parent = path.split('/').slice(0, -1).join('/');
  if (!canRead) return <p className="evidence-empty">Proje dosyalarını görüntülemek için İzinler sekmesinde dosya okuma iznini açın.</p>;
  return <section className="coding-context-browser" aria-label="Proje dosyaları">
    <p>İlgili dosya veya klasörleri seçerek sonraki isteğe bağlam ekleyin. Ajan ayrıca izin verilen proje bağlamını kullanır.</p>
    <div className="context-search"><Search size={13} /><input aria-label="Proje dosyalarında ara" placeholder="Projede dosya ara…" value={search} onChange={event => setSearch(event.target.value)} disabled={disconnected} /></div>
    <div className="context-path"><Button variant="ghost" disabled={!path || !!query} aria-label="Üst klasöre git" onClick={() => navigate(parent)}><ArrowLeft size={14} /></Button>
      <button onClick={() => navigate('')}>Proje kökü</button><span title={path}>{query ? 'Arama sonuçları' : path ? `/ ${path}` : '/'}</span>
      <Button variant="ghost" aria-label="Proje dosyalarını yenile" disabled={files.isFetching || disconnected} onClick={() => { void files.refetch(); if (preview) void file.refetch(); }}><RefreshCw size={13} /></Button></div>
    <div className="context-selection-status"><span>{selected.length} / 24 bağlam öğesi</span>{!!selected.length && <button onClick={() => onChange([])}>Seçimi temizle</button>}</div>
    {selected.length >= 24 && <p role="status">Daha fazla dosya eklemek için seçimden bir öğe çıkarın.</p>}
    {files.isPending && <p role="status"><LoaderCircle size={14} className="spin" /> Dosyalar yükleniyor…</p>}
    {files.isError && <p className="workspace-error" role="alert">{files.error.message} <button onClick={() => void files.refetch()}>Yeniden dene</button></p>}
    <div className="context-file-list">{files.data?.entries.map(entry => <div className="context-file" key={entry.path}>
      <input type="checkbox" aria-label={`${entry.path} bağlama ekle`} checked={selected.includes(entry.path)} disabled={disconnected || (!selected.includes(entry.path) && selected.length >= 24)} onChange={() => toggle(entry.path)} />
      <button type="button" title={entry.path} onClick={() => entry.is_dir ? navigate(entry.path) : setPreview(entry.path)}>{entry.is_dir ? <Folder size={14} /> : <FileCode2 size={14} />}<span>{query ? entry.path : entry.name}</span></button>
    </div>)}</div>
    {files.data && !files.data.entries.length && <p className="evidence-empty">{query ? 'Aramayla eşleşen dosya yok.' : 'Bu klasörde gösterilecek dosya yok.'}</p>}
    {files.data?.limited && <p className="muted">Liste sınırına ulaşıldı. Aramayı daraltın veya bir klasör açın.</p>}
    {preview && <section className="context-preview" aria-label="Dosya önizlemesi"><div><strong title={preview}>{preview}</strong><Button variant="ghost" aria-label="Dosya önizlemesini kapat" onClick={() => setPreview('')}><X size={13} /></Button></div>
      {file.isPending && <p role="status">Dosya yükleniyor…</p>}{file.isError && <p role="alert">{file.error.message}</p>}
      {file.data && <><pre>{file.data.content}</pre>{file.data.truncated && <p className="muted">Dosyanın ilk bölümü gösteriliyor.</p>}
        <Button variant="outline" disabled={!selected.includes(preview) && selected.length >= 24} onClick={() => toggle(preview)}>{selected.includes(preview) ? 'Bağlamdan çıkar' : 'Bağlama ekle'}</Button></>}
    </section>}
  </section>;
}
