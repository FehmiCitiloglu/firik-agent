import { useEffect, useId, useRef, useState, type FormEvent, type KeyboardEvent } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowDown, Code2, FilePlus2, FolderOpen, GitBranch, LoaderCircle, PanelRight, Plus, RefreshCw, ShieldCheck, Square, X } from 'lucide-react';
import type { CodingControlParams, CodingStartParams, CodingState, GitPreview, GitTool, ProjectsResult, TerminalListResult } from '@firik/contracts';
import { Button } from './components/ui/button.js';
import { WorkspaceMarkdown } from './workspace-markdown.js';
import { CodingEvidence, codingStatusName as statusName, type EvidenceKind } from './coding-evidence.js';
import { showError, useUI } from './store.js';
import { ContextBrowser, CodingPermissions } from './coding-context.js';
import { CodingTerminal } from './coding-terminal.js';

type CodingMode = NonNullable<CodingStartParams['mode']>;
const modes: Record<CodingMode, { label: string; description: string }> = {
  ask: { label: 'Projeye sor', description: 'Projeyi inceleyip soruları yanıtlar; dosya değiştirmez ve komut çalıştırmaz.' },
  plan: { label: 'Planla', description: 'Değişiklik planı oluşturur; dosya değiştirmez ve komut çalıştırmaz.' },
  approved: { label: 'Onaylı uygula', description: 'Plan üzerinden ilerler. Dosya değişiklikleri proje politikasına, komutlar işlem onayına tabidir.' },
  assisted: { label: 'Yardımlı uygula', description: 'İzin verilen dosya değişikliklerini uygulayabilir. Komutlar işlem onayına tabidir.' },
};
type Detail = EvidenceKind | 'context' | 'permissions';
const detailNames: Record<Detail, string> = { plan: 'Plan', changes: 'Değişiklikler', tests: 'Testler', context: 'Bağlam', permissions: 'İzinler' };
const details: Detail[] = ['plan', 'changes', 'tests', 'context', 'permissions'];
const emptyPaths: string[] = [];
const starters: { title: string; mode: CodingMode; prompt: string }[] = [
  { title: 'Projeyi tanı', mode: 'ask', prompt: 'Bu projenin mimarisini, giriş noktalarını ve geliştirme/test komutlarını dosyalara atıf yaparak açıkla. İlk katkı için bilmem gerekenleri belirt.' },
  { title: 'Özellik geliştir', mode: 'approved', prompt: 'İstenen özellik: \n\nKabul kriterleri: \n\nÖnce ilgili kodu incele ve kısa bir plan çıkar. Değişikliği uygula, uygun testlerle doğrula ve sonucu dosya referanslarıyla açıkla.' },
  { title: 'Hata gider', mode: 'approved', prompt: 'Sorun: \nTekrarlama adımları: \nBeklenen davranış: \n\nKök nedeni ilgili kodda bul, düzelt ve tekrarını önleyecek uygun bir testle doğrula.' },
  { title: 'Test ekle', mode: 'approved', prompt: 'Seçili dosyaların davranışını incele. Eksik olan anlamlı testleri ve sınır durumlarını belirle, mevcut test yapısına uygun testler ekle ve çalıştır. Gerçek sonuçları raporla.' },
  { title: 'Değişiklikleri incele', mode: 'ask', prompt: 'Çalışma ağacındaki değişiklikleri hata, regresyon ve eksik test açısından incele. Bulguları önem sırasıyla dosya ve satır referanslarıyla açıkla.' },
];

export default function ProjectWorkspace({ models, effortLevels }: { models: string[]; effortLevels: Record<string, string[]> }) {
  const disconnected = useUI(state => state.disconnected);
  const selected = useUI(state => state.project);
  const client = useQueryClient();
  const projects = useQuery({ queryKey: ['projects'], queryFn: () => window.firik.projects.list({}), enabled: !disconnected });
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState('');
  const [path, setPath] = useState('');
  const [choosing, setChoosing] = useState(false);
  const tool = useUI(state => state.codingTool);
  const workspaceId = useId();
  async function chooseDirectory() {
    if (choosing) return;
    setChoosing(true);
    try {
      const result = await window.firik.desktop.selectProjectDirectory(/^(?:\/|[a-z]:[\\/]|\\\\)/i.test(path) ? { default_path: path } : {});
      if (!result.canceled && result.path) { setPath(result.path); if (!name.trim() || name === path.split(/[\\/]/).filter(Boolean).at(-1)) setName(result.name ?? ''); }
    } catch (error) { showError(error); }
    finally { setChoosing(false); }
  }
  const create = useMutation({ mutationFn: () => window.firik.projects.create({ name: name.trim(), local_path: path }),
    onSuccess: async project => {
      client.setQueryData<ProjectsResult>(['projects'], previous => ({
        projects: [...(previous?.projects ?? []).filter(row => row.project_id !== project.project_id), project],
      }));
      useUI.setState({ project: project.project_id, codingNavigation: null, codingTool: 'agent' });
      setAdding(false); setName(''); setPath('');
      await client.invalidateQueries({ queryKey: ['projects'] });
    }, onError: showError });
  const projectId = projects.data?.projects.some(p => p.project_id === selected) ? selected : projects.data?.projects[0]?.project_id;
  useEffect(() => {
    if (projectId && projectId !== selected) useUI.setState({ project: projectId });
  }, [projectId, selected]);

  const currentProject = projects.data?.projects.find(project => project.project_id === projectId);
  const initial = useQuery<CodingState>({ queryKey: ['coding-open', projectId], enabled: false });
  const active = useQuery<CodingState>({ queryKey: ['coding', projectId, initial.data?.session_id], enabled: false });
  const currentRun = active.data ?? initial.data;
  const stop = useMutation({ mutationFn: () => window.firik.coding.control({ project_id: projectId!, session_id: currentRun!.session_id, run_id: currentRun!.run_id!, action: 'cancel' }),
    onSuccess: () => client.invalidateQueries({ queryKey: ['coding', projectId] }), onError: showError });

  return <section className="project-workspace" aria-label="Kodlama ajanı">
    <div className="workspace-toolbar project-toolbar"><label><FolderOpen size={16} /> Proje
      <select aria-label="Çalışılacak proje" value={projectId ?? ''} disabled={disconnected} onChange={e => useUI.setState({ project: e.target.value, codingNavigation: null, codingTool: 'agent' })}>
        {!projectId && <option value="">Proje seçin</option>}
        {projects.data?.projects.map(p => <option key={p.project_id} value={p.project_id}>{p.name}</option>)}
      </select></label>
      <Button variant="outline" onClick={() => setAdding(!adding)} disabled={disconnected}><Plus size={15} /> Proje ekle</Button>
      {currentProject && <span className="project-path" title={currentProject.local_path}>{currentProject.local_path}</span>}
    </div>
    {adding && <form className="project-add workspace-card" onSubmit={e => { e.preventDefault(); if (name.trim() && path.trim() && !create.isPending && !choosing) create.mutate(); }}>
      <Button type="button" variant="outline" disabled={choosing || create.isPending || disconnected} onClick={() => void chooseDirectory()}><FolderOpen size={16} /> {choosing ? 'Klasör seçiliyor…' : 'Klasör seç'}</Button>
      <label>Proje adı<input aria-label="Proje adı" value={name} onChange={e => setName(e.target.value)} required maxLength={200} /></label>
      <label>Proje klasörü<input aria-label="Proje klasörü" placeholder="Klasör seçin veya yolu yapıştırın" value={path} onChange={e => setPath(e.target.value)} required maxLength={4096} /></label>
      <Button disabled={create.isPending || disconnected || choosing || !name.trim() || !path.trim()}>{create.isPending ? 'Ekleniyor…' : 'Projeyi ekle'}</Button>
      <Button type="button" variant="ghost" disabled={create.isPending} onClick={() => setAdding(false)}>Vazgeç</Button>
    </form>}
    {projects.isError && <p className="workspace-error" role="alert">{projects.error.message}</p>}
    {projects.isPending ? <p className="workspace-empty">Projeler yükleniyor…</p> : projectId ? <>
      <nav className="project-tool-tabs" role="tablist" aria-label="Geliştirme araçları" onKeyDown={event => {
        if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
        event.preventDefault(); const next = event.key === 'Home' ? 'agent' : event.key === 'End' ? 'git' : tool === 'agent' ? 'git' : 'agent';
        useUI.setState({ codingTool: next }); document.getElementById(`${workspaceId}-${next}`)?.focus();
      }}>{(['agent', 'git'] as const).map(item => <Button key={item} role="tab" id={`${workspaceId}-${item}`} aria-controls={`${workspaceId}-${item}-panel`} aria-selected={tool === item} tabIndex={tool === item ? 0 : -1}
        variant={tool === item ? 'outline' : 'ghost'} onClick={() => useUI.setState({ codingTool: item })}>{item === 'agent' ? <Code2 size={15} /> : <GitBranch size={15} />}{item === 'agent' ? 'Ajan' : 'GitGod'}</Button>)}
        <span className="project-tools-hint">{tool === 'agent' ? 'İncele · Planla · Uygula · Doğrula' : 'Sürüm kontrolü · Aynı proje'}</span>
      </nav>
      {tool === 'git' && currentRun?.busy && <div className="coding-background-run" role="status"><LoaderCircle size={14} className="spin" /><span>Ajan: {statusName(currentRun.status)}. Değişiklik yapan Git işlemleri çalışma bitince kullanılabilir.</span>
        <Button variant="outline" disabled={stop.isPending || disconnected} onClick={() => stop.mutate()}><Square size={13} /> Durdur</Button></div>}
      <div role="tabpanel" id={`${workspaceId}-agent-panel`} aria-labelledby={`${workspaceId}-agent`} hidden={tool !== 'agent'} className="project-tool-panel agent-panel">
        <CodingWorkspace key={projectId} projectId={projectId} models={models} effortLevels={effortLevels} /></div>
      <div role="tabpanel" id={`${workspaceId}-git-panel`} aria-labelledby={`${workspaceId}-git`} hidden={tool !== 'git'} className="project-tool-panel git-panel"><GitWorkspace key={projectId} projectId={projectId} active={tool === 'git'} /></div>
      <CodingTerminal key={`terminal:${projectId}`} projectId={projectId} codingState={currentRun} />
    </> : <div className="workspace-empty"><FolderOpen size={32} /><h2>Bir proje ile başlayın</h2><p>Proje klasörünüzü seçin; dosyaları bağlama ekleyin, ajana iş verin ve sonucu GitGod ile inceleyin.</p>
      <Button variant="outline" disabled={disconnected} onClick={() => { setAdding(true); void chooseDirectory(); }}><FolderOpen size={16} /> Proje klasörü seç</Button></div>}
  </section>;
}

function CodingWorkspace({ projectId, models, effortLevels }: { projectId: string; models: string[]; effortLevels: Record<string, string[]> }) {
  const client = useQueryClient();
  const disconnected = useUI(state => state.disconnected);
  const navigation = useUI(state => state.codingNavigation);
  const [starting, setStarting] = useState(false);
  const initial = useQuery({ queryKey: ['coding-open', projectId], queryFn: () => window.firik.coding.open({ project_id: projectId }), enabled: !disconnected });
  const sessionId = initial.data?.session_id;
  const key = ['coding', projectId, sessionId];
  const state = useQuery({ queryKey: key, queryFn: () => window.firik.coding.get({ project_id: projectId, session_id: sessionId! }),
    enabled: !!sessionId && !disconnected, refetchInterval: query => starting || query.state.data?.busy ? 1000 : false });
  const draftId = `coding:${sessionId ?? projectId}`;
  const prompt = useUI(state => state.drafts[draftId] ?? '');
  const setPrompt = (text: string) => useUI.getState().draft(draftId, text);
  const [model, setModel] = useState('');
  const [mode, setMode] = useState<CodingMode>('approved');
  const [detail, setDetail] = useState<Detail>('plan');
  const [effort, setEffort] = useState<string | null>(null);
  const contextPaths = useUI(state => state.codingContexts[draftId] ?? emptyPaths);
  const setContextPaths = (paths: string[]) => useUI.setState(state => ({ codingContexts: { ...state.codingContexts, [draftId]: paths } }));
  const preferences = useQuery({ queryKey: ['coding-preferences', projectId], queryFn: () => window.firik.coding.preferences({ project_id: projectId }), enabled: !disconnected });
  const [inspectorOpen, setInspectorOpen] = useState(() => window.innerWidth > 1050);
  const [newOutput, setNewOutput] = useState(false);
  const messages = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const following = useRef(true);
  const inspectorId = useId();
  const modeDescriptionId = useId();
  const composerHintId = useId();
  const data = state.data ?? initial.data;
  const terminal = useQuery<TerminalListResult>({ queryKey: ['terminal', projectId], enabled: false });
  const manualCommandBusy = !!terminal.data?.commands.some(command => command.source === 'user' && ['starting', 'running', 'stopping'].includes(command.status ?? 'starting'));

  useEffect(() => {
    const stored = initial.data?.mode;
    if (stored === 'ask' || stored === 'plan' || stored === 'approved' || stored === 'assisted') setMode(stored);
    setEffort(initial.data?.reasoning_effort ?? null);
  }, [initial.data?.session_id, initial.data?.record_id]);

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    const off = window.firik.onEvent(event => {
      if (event.kind !== 'coding' || event.project_id !== projectId) return;
      if (event.event_type === 'finished') {
        void client.invalidateQueries({ queryKey: ['git', projectId] });
        void client.invalidateQueries({ queryKey: ['project-files', projectId] });
        void client.invalidateQueries({ queryKey: ['project-file', projectId] });
      }
      if (!timer) timer = setTimeout(() => {
        timer = undefined;
        void client.invalidateQueries({ queryKey: ['coding', projectId] });
        void client.invalidateQueries({ queryKey: ['coding-history', projectId] });
      }, 200);
    });
    return () => { off(); if (timer) clearTimeout(timer); };
  }, [client, projectId]);

  useEffect(() => {
    const media = window.matchMedia('(max-width: 1050px)');
    const compact = () => { if (media.matches) setInspectorOpen(false); };
    media.addEventListener('change', compact);
    return () => media.removeEventListener('change', compact);
  }, []);

  const lastMessage = data?.messages?.at(-1);
  useEffect(() => {
    following.current = true;
    setNewOutput(false);
  }, [sessionId]);
  useEffect(() => {
    if (following.current && messages.current) messages.current.scrollTop = messages.current.scrollHeight;
    else if (data?.messages?.length) setNewOutput(true);
  }, [sessionId, data?.messages?.length, lastMessage?.id, lastMessage?.content]);

  async function remember(result: CodingState) {
    await client.cancelQueries({ queryKey: ['coding', projectId, result.session_id] });
    client.setQueryData(['coding', projectId, result.session_id], result);
    client.setQueryData(['coding-open', projectId], result);
  }
  const open = useMutation({ mutationFn: (recordId: string | null) => window.firik.coding.open({ project_id: projectId,
    ...(recordId ? { record_id: recordId } : { new_session: true }) }), onSuccess: result => {
      setModel(''); requestAnimationFrame(() => input.current?.focus()); return remember(result);
    }, onError: showError });
  const start = useMutation({ mutationFn: (text: string) => window.firik.coding.start({ project_id: projectId,
    session_id: sessionId!, prompt: text, model: model || data!.model, mode,
    reasoning_effort: effortLevels[model || data!.model]?.includes(effort ?? '') ? effort : null, context_paths: contextPaths }),
    onMutate: () => { setStarting(true); following.current = true; setNewOutput(false); },
    onSuccess: (result, submitted) => {
      // A context request can finish after the user has started their next draft.
      if (useUI.getState().drafts[draftId]?.trim() === submitted) setPrompt('');
      return remember(result);
    }, onError: showError,
    onSettled: () => setStarting(false) });
  const control = useMutation({ mutationFn: (params: Pick<CodingControlParams, 'action' | 'request_id' | 'allowed'>) => window.firik.coding.control({
    project_id: projectId, session_id: sessionId!, run_id: data!.run_id!, ...params }),
    onSuccess: () => client.invalidateQueries({ queryKey: key }), onError: showError });
  const busy = !!data?.busy || start.isPending || open.isPending;
  const contextBlocked = contextPaths.length > 0 && preferences.data?.allow_reads === false;
  useEffect(() => {
    if (!navigation || navigation.projectId !== projectId || !data || disconnected) return;
    // Consume once even if this effect is mounted twice in React StrictMode.
    if (useUI.getState().codingNavigation !== navigation) return;
    useUI.setState({ codingNavigation: null });
    if (open.isPending) return;
    if (busy) { showError(new Error('Yeni oturum açmadan önce bu projedeki çalışmayı durdurun.')); return; }
    open.mutate(navigation.recordId);
  }, [navigation, projectId, data?.session_id, busy, open.isPending, open.mutate, disconnected]);
  function send(e: FormEvent) {
    e.preventDefault();
    if (prompt.trim() && !busy && !manualCommandBusy && !disconnected && data && !preferences.isPending && !contextBlocked) start.mutate(prompt.trim());
  }
  function selectDetail(event: KeyboardEvent<HTMLElement>) {
    const index = details.indexOf(detail);
    const next = event.key === 'ArrowRight' ? (index + 1) % details.length
      : event.key === 'ArrowLeft' ? (index + details.length - 1) % details.length
      : event.key === 'Home' ? 0 : event.key === 'End' ? details.length - 1 : -1;
    if (next < 0) return;
    event.preventDefault(); setDetail(details[next]);
    document.getElementById(`${inspectorId}-${details[next]}`)?.focus();
  }
  function closeInspector() {
    setInspectorOpen(false);
    document.getElementById(`${inspectorId}-toggle`)?.focus();
  }
  function showDetail(next: Detail) { setDetail(next); setInspectorOpen(true); }

  return <div className="coding-workspace" data-session-id={sessionId} data-run-id={data?.run_id}>
    <div className="coding-runbar" aria-label="Kodlama çalışma kontrolleri">
      <div className="coding-runbar-heading"><strong><Code2 size={17} /> Kodlama ajanı</strong>
        <span className="workspace-status" role="status">{disconnected ? 'Bağlantı kesildi' : starting && !data?.busy ? 'Bağlam hazırlanıyor' : statusName(data?.status)}</span>
        <div className="coding-run-actions"><Button variant="outline" disabled={busy || disconnected || !data} onClick={() => open.mutate(null)}><Plus size={14} /> Yeni oturum</Button>
          {data?.busy && <><Button variant="outline" disabled={control.isPending || disconnected} onClick={() => control.mutate({ action: data.status === 'paused' ? 'resume' : 'pause' })}>{data.status === 'paused' ? 'Devam et' : 'Duraklat'}</Button>
            <Button className="coding-stop" variant="outline" disabled={control.isPending || disconnected} onClick={() => control.mutate({ action: 'cancel' })}><Square size={13} /> Durdur</Button></>}
          <Button id={`${inspectorId}-toggle`} variant={inspectorOpen ? 'outline' : 'ghost'} aria-expanded={inspectorOpen} aria-controls={inspectorId} onClick={() => setInspectorOpen(!inspectorOpen)}><PanelRight size={15} /> İnceleme</Button>
        </div>
      </div>
      <div className="coding-configuration"><label>Model<select aria-label="Kodlama modeli" value={model || data?.model || ''} disabled={busy || disconnected || !data} onChange={e => { setModel(e.target.value); setEffort(null); }}>
        {[...new Set([model || data?.model || '', ...models])].filter(Boolean).map(name => <option key={name}>{name}</option>)}</select></label>
        <label>Çalışma modu<select aria-label="Kodlama çalışma modu" aria-describedby={modeDescriptionId} value={mode} disabled={busy || disconnected || !data} onChange={e => setMode(e.target.value as CodingMode)}>
          {Object.entries(modes).map(([value, item]) => <option key={value} value={value}>{item.label}</option>)}</select></label>
        {!!effortLevels[model || data?.model || '']?.length && <label>Düşünme<select aria-label="Kodlama düşünme seviyesi" value={effort ?? ''} disabled={busy || disconnected || !data} onChange={event => setEffort(event.target.value || null)}>
          <option value="">Varsayılan</option>{effortLevels[model || data?.model || ''].map(level => <option key={level}>{level}</option>)}</select></label>}
      </div>
      <p className="coding-mode-description" id={modeDescriptionId}>{modes[mode].description}</p>
      <button type="button" className="coding-permission-summary" onClick={() => showDetail('permissions')}><ShieldCheck size={13} />{preferences.data
        ? `Okuma ${preferences.data.allow_reads ? 'açık' : 'kapalı'} · Düzenleme ${preferences.data.allow_edits ? 'açık' : 'kapalı'} · Komut ${preferences.data.allow_terminal ? 'onayla' : 'kapalı'} · Test ${preferences.data.allow_tests ? 'onayla' : 'kapalı'}`
        : preferences.isError ? 'İzinler yüklenemedi' : 'Proje izinleri yükleniyor…'}</button>
    </div>
    {(initial.isError || state.isError) && <p className="workspace-error" role="alert">{initial.error?.message ?? state.error?.message}</p>}
    <div className="coding-columns"><div className="coding-conversation">
      <div className="coding-messages" ref={messages} aria-label="Kodlama konuşması" onScroll={() => {
        const element = messages.current;
        if (!element) return;
        following.current = element.scrollHeight - element.scrollTop - element.clientHeight < 48;
        if (following.current) setNewOutput(false);
      }}>
        {!data ? <p className="workspace-empty" role="status">Kodlama oturumu yükleniyor…</p>
          : !data.messages?.length && <div className="workspace-empty coding-welcome"><Code2 size={32} /><h2>Projede ne yapalım?</h2><p>İlgili dosyaları bağlama ekleyin. Hedefi ve kabul kriterlerini belirtin; planı, değişiklikleri ve test sonuçlarını inceleyin.</p>
            <div className="coding-starters">{starters.map(starter => <button key={starter.title} disabled={busy || disconnected} onClick={() => { setMode(starter.mode); setPrompt(starter.prompt); input.current?.focus(); }}>{starter.title}</button>)}</div></div>}
        {data?.messages?.map(message => <article className={`workspace-message ${message.role}`} key={message.id}>
          <strong>{message.role === 'user' ? 'Siz' : 'Kodlama ajanı'}{message.pending && <LoaderCircle size={13} className="spin" />}</strong>
          <WorkspaceMarkdown text={message.content} /></article>)}
      </div>
      {newOutput && <Button className="coding-latest" variant="outline" onClick={() => {
        following.current = true; setNewOutput(false);
        if (messages.current) messages.current.scrollTop = messages.current.scrollHeight;
      }}><ArrowDown size={14} /> Yeni çıktıya git</Button>}
      {data?.error && <p className="workspace-error coding-run-error" role="alert">{data.error}</p>}
      {!!data?.approvals?.length && <div className="coding-approvals" aria-label="Bekleyen işlem onayları">
      {data.approvals.map(approval => <div className="approval-card" key={approval.request_id}><strong>İşlem onayı: {approval.tool_name}</strong><pre>{approval.preview}</pre>
        <div className="approval-actions">{[false, true].map(allowed => <Button key={String(allowed)} variant={allowed ? 'default' : 'outline'} disabled={control.isPending || disconnected}
          onClick={() => control.mutate({ action: 'approve', request_id: approval.request_id, allowed })}>{allowed ? 'Onayla' : 'Reddet'}</Button>)}</div></div>)}
      </div>}
      <form className="workspace-composer" onSubmit={send}>
        {manualCommandBusy && <p className="terminal-coding-wait" role="status">Terminal komutu çalışıyor. Yeni ajan isteği için bitmesini bekleyin veya terminalden durdurun.</p>}
        {contextBlocked && <p className="workspace-error context-blocked" role="status">Seçili bağlamı göndermek için dosya okuma iznini açın veya eklenen dosyaları çıkarın.</p>}
        <div className="coding-context-chips" aria-label="İsteğe eklenen dosyalar">{contextPaths.map(path => <span key={path} title={path}>{path}<button type="button" aria-label={`${path} bağlamdan çıkar`} onClick={() => setContextPaths(contextPaths.filter(item => item !== path))}><X size={12} /></button></span>)}</div>
        <textarea ref={input} aria-label="Kodlama isteği" aria-describedby={composerHintId} placeholder="Projede yapılacak işi yazın…" value={prompt} onChange={e => setPrompt(e.target.value)} maxLength={65536} disabled={disconnected || !data}
          onKeyDown={event => { if (event.key === 'Enter' && (event.metaKey || event.ctrlKey) && !event.nativeEvent.isComposing && !event.repeat) send(event); }} />
        <div className="workspace-toolbar coding-composer-footer"><Button type="button" variant="ghost" onClick={() => showDetail('context')} disabled={disconnected || !data}><FilePlus2 size={14} /> Dosya ekle{contextPaths.length ? ` (${contextPaths.length})` : ''}</Button>
          <span className="muted" id={composerHintId}>⌘ / Ctrl + Enter: gönder · Enter: yeni satır</span>
          <Button disabled={busy || manualCommandBusy || disconnected || !data || !prompt.trim() || preferences.isPending || contextBlocked}>{start.isPending ? 'Bağlam hazırlanıyor…' : 'Gönder'}</Button></div>
      </form>
    </div><aside className="coding-inspector" id={inspectorId} hidden={!inspectorOpen} aria-label="Kodlama inceleyicisi"
      onKeyDown={event => { if (event.key === 'Escape') { event.stopPropagation(); closeInspector(); } }}>
      <div className="coding-inspector-heading"><strong>İnceleme</strong><Button variant="ghost" aria-label="İnceleyiciyi kapat" onClick={closeInspector}><X size={15} /></Button></div>
      <nav role="tablist" aria-label="Kodlama ayrıntıları" onKeyDown={selectDetail}>{details.map(tab => <Button key={tab} role="tab" id={`${inspectorId}-${tab}`}
        aria-selected={detail === tab} aria-controls={`${inspectorId}-panel-${tab}`} tabIndex={detail === tab ? 0 : -1}
        variant={detail === tab ? 'outline' : 'ghost'} onClick={() => setDetail(tab)}>{detailNames[tab]}</Button>)}</nav>
      {details.map(tab => <div key={tab} role="tabpanel" id={`${inspectorId}-panel-${tab}`} aria-labelledby={`${inspectorId}-${tab}`} hidden={detail !== tab} tabIndex={0}>
        {tab === 'context' ? <ContextBrowser projectId={projectId} selected={contextPaths} onChange={setContextPaths} canRead={preferences.data?.allow_reads === true} />
          : tab === 'permissions' ? <CodingPermissions projectId={projectId} busy={busy || manualCommandBusy} />
          : data && <CodingEvidence kind={tab} text={data[tab] ?? ''} readOnly={mode === 'ask' || mode === 'plan'} />}
      </div>)}
    </aside></div>
  </div>;
}

function GitWorkspace({ projectId, active }: { projectId: string; active: boolean }) {
  const disconnected = useUI(state => state.disconnected);
  const client = useQueryClient();
  const overview = useQuery({ queryKey: ['git', projectId], queryFn: () => window.firik.git.overview({ project_id: projectId }), enabled: active && !disconnected, staleTime: 0 });
  const [selected, setSelected] = useState('status');
  const [search, setSearch] = useState('');
  const [values, setValues] = useState<Record<string, string>>({});
  const [preview, setPreview] = useState<GitPreview | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [output, setOutput] = useState('');
  const [selectedPaths, setSelectedPaths] = useState<string[]>([]);
  const [advanced, setAdvanced] = useState(false);
  const tool = overview.data?.tools.find(row => row.id === selected);
  const prepare = useMutation({ mutationFn: (current: GitTool) => window.firik.git.preview({ project_id: projectId, tool_id: current.id,
    arguments: Object.fromEntries(current.fields.map(field => {
      const value = values[field.name] ?? String(field.default ?? '');
      return [field.name, typeof field.default === 'boolean' ? value === 'true' : typeof field.default === 'number' ? Number(value) : value];
    })) }), onSuccess: result => { setPreview(result); setConfirmed(false); }, onError: showError });
  const execute = useMutation({ mutationFn: () => window.firik.git.execute({ project_id: projectId, token: preview!.token, confirmed }),
    onSuccess: async result => { setOutput(`${result.ok ? 'Tamamlandı' : 'İşlem tamamlanamadı'}\n${result.command}\n\n${result.stdout}${result.stderr}${result.truncated ? '\nÇıktı boyut sınırında kısaltıldı.' : ''}${result.conflicts.length ? `\nÇakışmalar: ${result.conflicts.join(', ')}` : ''}`);
      setPreview(null); setConfirmed(false); setSelectedPaths([]); await client.invalidateQueries({ queryKey: ['git', projectId] }); },
    onError: error => { setPreview(null); setConfirmed(false); showError(error); } });
  const busy = prepare.isPending || execute.isPending || disconnected;
  function chooseTool(id: string, arguments_: Record<string, string> = {}) { setSelected(id); setValues(arguments_); setPreview(null); setConfirmed(false); }
  const commonTools = ['status', 'diff', 'stage', 'unstage', 'commit'];
  const availablePaths = selectedPaths.filter(path => overview.data?.files?.some(file => file.path === path));
  return <div className="git-workspace"><div className="workspace-toolbar"><strong><GitBranch size={17} /> GitGod</strong><span className="workspace-status">{overview.data?.branch}</span>
    <Button variant="ghost" aria-label="Git durumunu yenile" disabled={busy} onClick={() => void overview.refetch()}><RefreshCw size={15} /></Button></div>
    {overview.isError && <p className="workspace-error" role="alert">{overview.error.message}</p>}
    {overview.isPending && <p>Git durumu yükleniyor…</p>}
    {overview.data && <div className="git-columns"><aside className="git-tools"><input aria-label="Git araçlarında ara" placeholder="Git araçlarında ara…" value={search} onChange={e => setSearch(e.target.value)} />
      {overview.data.tools.filter(row => (advanced || search || commonTools.includes(row.id)) && `${row.title} ${row.category}`.toLocaleLowerCase('tr').includes(search.toLocaleLowerCase('tr'))).map(row => <button key={row.id} className={selected === row.id ? 'selected' : ''}
        disabled={busy} onClick={() => chooseTool(row.id)}><span>{row.category}</span>{row.title}</button>)}
      <Button variant="ghost" aria-expanded={advanced} onClick={() => setAdvanced(!advanced)}>{advanced ? 'Sık kullanılan araçlar' : `Tüm Git araçları (${overview.data.tools.length})`}</Button></aside>
      <div className="git-content"><section className="workspace-card git-changed-files" aria-label="Değişen dosyalar"><h3>Değişen dosyalar <span className="muted">{overview.data.files?.length ?? 0}</span></h3>
        <p className="muted">Dosyaları seçin, işlemi önizleyin ve onaylayın.</p>
        {overview.data.files?.map(file => <div className="git-file" key={file.path}><label><input type="checkbox" aria-label={`${file.path} Git için seç`} disabled={busy} checked={availablePaths.includes(file.path)} onChange={event => setSelectedPaths(event.target.checked ? [...availablePaths, file.path] : availablePaths.filter(path => path !== file.path))} />
          <code title={`Index: ${file.index}, çalışma ağacı: ${file.worktree}`}>{file.index}{file.worktree}</code><span title={file.original_path ? `${file.original_path} → ${file.path}` : file.path}>{file.path}</span></label>
          <Button variant="ghost" disabled={busy} aria-label={`${file.path} farkını incele`} onClick={() => chooseTool('diff', { path: file.path, staged: file.index !== ' ' && file.index !== '?' && file.worktree === ' ' ? 'true' : 'false' })}>Diff</Button></div>)}
        {!overview.data.files?.length && <p className="evidence-empty">Gösterilecek değişen dosya yok.</p>}
        <div className="workspace-toolbar git-quick-actions"><Button variant="outline" disabled={busy || !availablePaths.length} onClick={() => chooseTool('stage', { paths: availablePaths.join('\n') })}>Seçilenleri hazırla</Button>
          <Button variant="outline" disabled={busy || !availablePaths.length} onClick={() => chooseTool('unstage', { paths: availablePaths.join('\n') })}>Hazırlıktan çıkar</Button>
          <Button variant="outline" disabled={busy} onClick={() => chooseTool('commit')}>Commit oluştur</Button></div></section>
        <div className="workspace-card"><h3>Çalışma ağacı</h3><pre>{overview.data.status}</pre><details><summary>Değişiklikler</summary><pre>{overview.data.diff || 'Değişiklik yok.'}</pre></details>
        <details><summary>Son commitler</summary><pre>{overview.data.commits}</pre></details></div>
        {tool && <form className="workspace-card git-tool-form" onSubmit={e => { e.preventDefault(); setPreview(null); prepare.mutate(tool); }}><h3>{tool.title}</h3><p>{tool.description}</p>
          {tool.fields.map(field => <label key={field.name}>{field.label}{typeof field.default === 'boolean'
            ? <select aria-label={field.label} disabled={busy} value={values[field.name] ?? String(field.default)} onChange={e => { setValues({ ...values, [field.name]: e.target.value }); setPreview(null); }}><option value="false">Hayır</option><option value="true">Evet</option></select>
            : <textarea aria-label={field.label} disabled={busy} required={field.required} maxLength={16000} value={values[field.name] ?? String(field.default ?? '')}
              onChange={e => { setValues({ ...values, [field.name]: e.target.value }); setPreview(null); }} rows={field.name === 'paths' ? 3 : 1} />}</label>)}
          <Button variant="outline" disabled={busy}>Komutu önizle</Button></form>}
        {preview && <div className="workspace-card git-confirmation"><h3>{preview.title}</h3><pre>{preview.command}</pre>{preview.warnings.map(w => <p key={w}>{w}</p>)}
          {preview.requires_confirmation && <label className="workspace-check"><input type="checkbox" checked={confirmed} disabled={busy} onChange={e => setConfirmed(e.target.checked)} /> Bu komutun değişiklik yapmasını onaylıyorum.</label>}
          <Button disabled={busy || (preview.requires_confirmation && !confirmed)} onClick={() => execute.mutate()}>Çalıştır</Button></div>}
        {execute.isPending && <p role="status">Git komutu çalışıyor…</p>}{output && <pre className="workspace-card git-output" aria-live="polite">{output}</pre>}
      </div></div>}
  </div>;
}
