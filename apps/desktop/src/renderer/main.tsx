import { StrictMode, Suspense, lazy, useEffect, useRef, useState, type FormEvent } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider, useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowUp, ChevronDown, Command, Globe, LoaderCircle, MessageSquare, PanelLeftClose, PanelLeftOpen, Plus, RefreshCw,
  Search, Settings, ShieldCheck, Square, Terminal, X } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { ChatState, DesktopEvent, Snapshot } from '@firik/contracts';
import { BrowserPanel } from './browser-panel.js';
import { Button } from './components/ui/button.js';
import {
  BROWSER_RESIZER_WIDTH,
  MIN_BROWSER_PANEL_WIDTH,
  MIN_CHAT_COLUMN_WIDTH,
  clampBrowserPanelWidth,
  disconnect,
  showError,
  useUI,
} from './store.js';
import './style.css';

const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity,
  refetchOnWindowFocus: false }, mutations: { retry: false } } });

const SettingsDialog = lazy(() => import('./settings-dialog.js'));

function App() {
  const client = useQueryClient();
  const active = useUI(state => state.active);
  const drafts = useUI(state => state.drafts);
  const runs = useUI(state => state.runs);
  const approvals = useUI(state => state.approvals);
  const error = useUI(state => state.error);
  const disconnected = useUI(state => state.disconnected);
  const settingsOpen = useUI(state => state.settingsOpen);
  const browserOpen = useUI(state => state.browserOpen);
  const browserPanelWidth = useUI(state => state.browserPanelWidth);
  const sidebarCollapsed = useUI(state => state.sidebarCollapsed);
  const snapshot = useQuery({ queryKey: ['snapshot'], queryFn: () => window.firik.snapshot({}) });
  const conversation = useQuery({ queryKey: ['chat', active], queryFn: () => window.firik.chat.get({ conversation_id: active! }), enabled: !!active });
  const [search, setSearch] = useState('');
  const [sending, setSending] = useState(false);
  const [toolsOpen, setToolsOpen] = useState(false);
  const [stage, setStage] = useState<Record<string, string>>({});
  const [workspaceWidth, setWorkspaceWidth] = useState<number | undefined>(undefined);
  const [resizingBrowser, setResizingBrowser] = useState(false);
  const end = useRef<HTMLDivElement>(null);
  const scrollArea = useRef<HTMLDivElement>(null);
  const workspaceRef = useRef<HTMLDivElement>(null);
  const following = useRef(true);
  const sequence = useRef({ instance: '', value: 0 });

  useEffect(() => {
    const unsubscribe = window.firik.onEvent((event: DesktopEvent) => {
      const last = sequence.current;
      if (last.instance && (last.instance !== event.backend_instance_id || event.sequence > last.value + 1)) {
        void client.invalidateQueries({ queryKey: ['snapshot'] });
        void client.invalidateQueries({ queryKey: ['chat'] });
      }
      if (last.instance === event.backend_instance_id && event.sequence <= last.value) return;
      sequence.current = { instance: event.backend_instance_id, value: event.sequence };
      const sid = event.conversation_id;
      if (event.kind === 'agent' && sid && event.run_id) {
        if (event.run) useUI.getState().updateRun(event.run);
        if (event.event_type === 'text_delta') client.setQueryData<ChatState>(['chat', sid], old => {
          if (!old) return old;
          const messages = [...old.messages];
          const lastMessage = messages.at(-1);
          if (lastMessage?.role === 'assistant' && lastMessage.pending)
            messages[messages.length - 1] = { ...lastMessage, content: lastMessage.content + (event.content ?? '') };
          return { ...old, messages };
        });
        const labels: Record<string, string> = { model_request_started: 'Yanıt hazırlanıyor', reasoning_delta: 'Düşünüyor',
          text_delta: 'Yanıt yazılıyor', tool_call_started: event.tool_name ? `${event.tool_name} çalışıyor` : 'Araç çalışıyor',
          tool_call_result: 'Araç tamamlandı', recovery: 'Bağlantı kurtarılıyor', mcp_connecting: 'Araçlar bağlanıyor' };
        if (event.event_type && labels[event.event_type]) setStage(old => ({ ...old, [sid]: labels[event.event_type!] }));
        if (event.tool_name?.startsWith('browser_')) useUI.setState({ browserOpen: true });
      }
      if (event.kind === 'run' && event.run) {
        useUI.getState().updateRun(event.run);
        if (event.run.error) showError(new Error(event.run.error));
        void client.invalidateQueries({ queryKey: ['chat', event.run.conversation_id] });
        void client.invalidateQueries({ queryKey: ['snapshot'] });
      }
      if (event.kind === 'history') void client.invalidateQueries({ queryKey: ['snapshot'] });
      if (event.kind === 'warning' && event.content) showError(new Error(event.content));
      if (event.kind === 'approval' && event.approval) {
        const approval = event.approval;
        useUI.setState(state => ({ approvals: { ...state.approvals, [approval.approval_id]: approval } }));
      }
      if (event.kind === 'approval_closed' && event.approval_id) useUI.setState(state => {
        const next = { ...state.approvals }; delete next[event.approval_id!]; return { approvals: next };
      });
    });
    const unsubscribeDisconnect = window.firik.onDisconnect(message => {
      disconnect(message);
      client.setQueriesData<ChatState>({ queryKey: ['chat'] }, old => old ? {
        ...old, messages: old.messages.map(row => row.pending ? { ...row, pending: false } : row), run: null,
      } : old);
    });
    return () => { unsubscribe(); unsubscribeDisconnect(); };
  }, [client]);

  useEffect(() => {
    if (!snapshot.data) return;
    const data = snapshot.data;
    const last = sequence.current;
    if (last.instance === data.backend_instance_id && data.sequence < last.value) return;
    for (const run of data.runs) useUI.getState().updateRun(run);
    useUI.setState({ approvals: Object.fromEntries(data.approvals.map(row => [row.approval_id, row])) });
    if (!useUI.getState().active && data.conversations[0]) useUI.getState().select(data.conversations[0].conversation_id);
  }, [snapshot.data]);

  useEffect(() => {
    if (conversation.data?.run) useUI.getState().updateRun(conversation.data.run);
  }, [conversation.data?.run]);

  useEffect(() => {
    if (following.current) end.current?.scrollIntoView({ behavior: 'instant', block: 'end' });
  }, [conversation.data?.messages]);

  async function newChat(initialDraft = '') {
    try {
      const chat = await window.firik.chat.create({ model: snapshot.data?.settings.default_model ?? '' });
      client.setQueryData(['chat', chat.conversation_id], chat);
      useUI.getState().select(chat.conversation_id);
      if (initialDraft) useUI.getState().draft(chat.conversation_id, initialDraft);
      following.current = true;
      await client.invalidateQueries({ queryKey: ['snapshot'] });
    } catch (failure) { showError(failure); }
  }

  useEffect(() => {
    function shortcut(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'n' && !disconnected) {
        event.preventDefault();
        if (snapshot.data) void newChat();
      }
    }
    window.addEventListener('keydown', shortcut);
    return () => window.removeEventListener('keydown', shortcut);
  }, [snapshot.data?.settings.default_model, disconnected]);

  async function send(event: FormEvent) {
    event.preventDefault();
    const chat = conversation.data;
    if (!chat || !active || sending) return;
    const text = (drafts[active] ?? '').trim();
    if (!text) return;
    setSending(true); following.current = true;
    client.setQueryData<ChatState>(['chat', active], old => old ? { ...old, messages: [...old.messages,
      { id: `${active}:local-user`, role: 'user', content: text, pending: false, timestamp: '' },
      { id: `${active}:local-assistant`, role: 'assistant', content: '', pending: true, timestamp: '' }] } : old);
    useUI.getState().draft(active, '');
    try {
      const run = await window.firik.chat.start({ conversation_id: active, prompt: text, model: chat.model,
        reasoning_effort: chat.reasoning_effort });
      useUI.getState().updateRun(run);
    } catch (failure) {
      showError(failure); useUI.getState().draft(active, text);
      await Promise.all([client.invalidateQueries({ queryKey: ['chat', active] }),
        client.invalidateQueries({ queryKey: ['snapshot'] })]);
    } finally { setSending(false); }
  }

  const data = snapshot.data;
  const chat = conversation.data;
  const run = active ? runs[active] : undefined;
  const busy = run?.status === 'running';
  const pendingApprovals = Object.values(approvals).filter(row => row.conversation_id === active);

  const browserEngine = useUI(state => state.browserEngine);

  useEffect(() => {
    window.firik?.browser?.getEngine?.()
      .then(mode => useUI.getState().setBrowserEngine(mode))
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (!browserOpen || !active || browserEngine !== 'embedded') {
      void window.firik?.browser?.setViewLayout?.({
        conversation_id: active ?? null,
        visible: false,
        obscured: false,
        x: 0,
        y: 0,
        width: 0,
        height: 0,
        fit_viewport: true,
      }).catch(() => {});
    }
  }, [active, browserEngine, browserOpen]);

  useEffect(() => {
    const element = workspaceRef.current;
    if (!element) {
      setWorkspaceWidth(undefined);
      return;
    }
    const measure = () => {
      const next = element.clientWidth;
      if (next > 0) setWorkspaceWidth(next);
    };
    measure();
    if (typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, [Boolean(data)]);

  const effectiveBrowserWidth = clampBrowserPanelWidth(browserPanelWidth, workspaceWidth);
  const maxBrowserPanelWidth = workspaceWidth
    ? Math.max(MIN_BROWSER_PANEL_WIDTH, Math.floor(workspaceWidth - MIN_CHAT_COLUMN_WIDTH - BROWSER_RESIZER_WIDTH))
    : 960;

  return <div className={`app-shell ${resizingBrowser ? 'resizing' : ''}`}>
    <aside className={sidebarCollapsed ? 'sidebar collapsed' : 'sidebar'}>
      <div className="brand"><div className="brand-icon">f<span>·</span></div><div><strong>firik<span>agent</span></strong><div className="brand-caption">KİŞİSEL ÇALIŞMA ALANINIZ</div></div></div>
      <Button className="new-chat" onClick={() => void newChat()} disabled={!data || disconnected}><Plus size={17} /> Yeni sohbet <span className="shortcut"><Command size={11} /> N</span></Button>
      <div className="history-search"><Search size={14} /><input aria-label="Sohbetlerde ara" placeholder="Sohbetlerde ara" value={search} onChange={e => setSearch(e.target.value)} /></div>
      <div className="section-caption">SOHBETLER <span>{data?.conversations.length ?? 0}</span></div>
      <nav className="history-list" aria-label="Sohbet geçmişi">
        {data?.conversations.filter(row => row.title.toLocaleLowerCase('tr').includes(search.toLocaleLowerCase('tr'))).map(row =>
          <button key={row.conversation_id} className={`history-item ${active === row.conversation_id ? 'selected' : ''}`} onClick={() => { useUI.getState().select(row.conversation_id); following.current = true; }}>
            <MessageSquare size={15} /><span>{row.title}</span>{runs[row.conversation_id]?.status === 'running' && <span className="live-dot" />}
          </button>)}
        {data && data.conversations.length === 0 && <p className="history-empty">İlk sohbetiniz burada görünecek.</p>}
      </nav>
      <div className="sidebar-bottom"><div className="backend-status"><span className={`status-dot ${disconnected || !data ? 'offline' : ''}`} /><span>{disconnected ? 'Bağlantı kesildi' : data ? 'Python çekirdeği bağlı' : 'Başlatılıyor…'}</span><span className="preview-badge">Önizleme</span></div>
        <Button variant="ghost" className="settings-button" onClick={() => useUI.setState({ settingsOpen: true })} disabled={!data}><Settings size={17} /> Ayarlar</Button>
      </div>
    </aside>

    <main className="main-content">
      <header className="topbar">
        <div className="topbar-left">
          <Button
            variant="ghost"
            className="sidebar-toggle"
            aria-label={sidebarCollapsed ? 'Kenar çubuğunu göster' : 'Kenar çubuğunu gizle'}
            title={sidebarCollapsed ? 'Kenar çubuğunu göster' : 'Kenar çubuğunu gizle'}
            onClick={() => useUI.getState().toggleSidebar()}
          >
            {sidebarCollapsed ? <PanelLeftOpen size={16} /> : <PanelLeftClose size={16} />}
          </Button>
          <div className="page-title"><span className="breadcrumb">Çalışma alanı</span><ChevronDown size={12} /><span>{chat?.title ?? 'Yeni sohbet'}</span></div>
        </div>
        <div className="topbar-right"><span className="local-badge"><span /> Masaüstü</span>
          <Button variant={browserOpen ? 'outline' : 'ghost'} aria-label="Tarayıcı panelini aç" disabled={!active || disconnected} onClick={() => useUI.setState({ browserOpen: !browserOpen })}><Globe size={15} /> Tarayıcı</Button>
          <Button variant="ghost" aria-label="Model listesini yenile" title="Model listesini yenile" disabled={!data || disconnected} onClick={() => {
            void window.firik.models.list({}).then(models => client.setQueryData<Snapshot>(['snapshot'], old => old ? { ...old, models } : old)).catch(showError);
          }}><RefreshCw size={16} /></Button></div>
      </header>

      {error && <div className="error-banner" role="alert"><span>{error}</span><button aria-label="Bildirimi kapat" onClick={() => useUI.setState({ error: null })}><X size={16} /></button></div>}
      {snapshot.isError && <div className="startup-error" role="alert"><Terminal size={30} /><h2>Çekirdek başlatılamadı</h2><p>{snapshot.error.message}</p><p>Python bağımlılıklarını kontrol edip uygulamayı yeniden başlatın.</p></div>}
      {!data && !snapshot.isError && <div className="loading-state"><LoaderCircle size={24} className="spin" /><p>Çalışma alanınız hazırlanıyor…</p></div>}

      {data && <div className="workspace-body" ref={workspaceRef}>
        <div className="chat-column">
        <div className="messages-area" ref={scrollArea} onScroll={() => {
          const element = scrollArea.current;
          if (element) following.current = element.scrollHeight - element.scrollTop - element.clientHeight < 100;
        }}>
          {!chat?.messages.length ? <div className="welcome">
            <div className="welcome-mark">f<span>·</span></div><span className="eyebrow">FIRIK AGENT</span>
            <h1>Birlikte ne yapalım?</h1><p>Bir fikirle başlayın. Sorularınızı yanıtlayalım,<br />birlikte düşünelim ve işlerinizi ilerletelim.</p>
            <div className="suggestions">{['Bir fikri birlikte geliştirelim', 'Bir konuyu araştırıp açıklayalım', 'Kodumu inceleyip iyileştirelim'].map(text =>
              <button key={text} onClick={() => { if (active) useUI.getState().draft(active, text); else void newChat(text); }}><MessageSquare size={17} />{text}<ArrowUp size={13} /></button>)}</div>
            {!active && <Button variant="outline" onClick={() => void newChat()}><Plus size={15} /> Sohbete başla</Button>}
          </div> : <div className="messages">
            {chat.earlier_messages > 0 && <p className="earlier-note">Son 100 mesaj gösteriliyor. Önceki mesajlar geçmişte korunuyor.</p>}
            {chat.messages.map(message => <article key={message.id} className={`message message-${message.role}`}>
              <div className={`avatar ${message.role}`}>{message.role === 'user' ? 'S' : 'f·'}</div><div className="message-body"><div className="message-author">{message.role === 'user' ? 'Siz' : 'Firik'}{message.pending && <span className="working-label"><LoaderCircle size={12} className="spin" />{active ? stage[active] ?? 'Yanıt hazırlanıyor' : ''}</span>}</div>
                <div className="markdown"><ReactMarkdown remarkPlugins={[remarkGfm]} components={{
                  a: ({ href, children }) => <a href={href} onClick={event => { event.preventDefault(); if (href) void window.firik.openLink(href).catch(showError); }}>{children}</a>,
                  img: ({ alt }) => <span className="muted">[Görsel{alt ? `: ${alt}` : ''}]</span>,
                }}>{message.content || (message.pending ? ' ' : '')}</ReactMarkdown>{message.pending && !message.content && <span className="typing-dots"><i /><i /><i /></span>}</div>
              </div></article>)}
            <div ref={end} />
          </div>}
        </div>

        <div className="composer-zone">
          {pendingApprovals.map(approval => <div className="approval-card" key={approval.approval_id} role="region" aria-label="İşlem onayı">
            <strong><ShieldCheck size={17} /> İşlem onayınızı bekliyor: {approval.tool_name}</strong><details><summary>İşlem ayrıntıları</summary><pre>{approval.preview}</pre></details>
            <div className="approval-actions">{[false, true].map(allowed => <Button variant={allowed ? 'default' : 'outline'} key={String(allowed)} onClick={() => {
              void window.firik.approvals.resolve({ approval_id: approval.approval_id, run_id: approval.run_id, allowed }).catch(showError);
            }}>{allowed ? 'Onayla' : 'Reddet'}</Button>)}</div>
          </div>)}
          {!data.settings.has_api_key && <button className="key-notice" onClick={() => useUI.setState({ settingsOpen: true })}><ShieldCheck size={15} /> Başlamak için Ayarlar bölümünden API anahtarınızı ekleyin.</button>}
          {conversation.isError && <p className="inline-error">{conversation.error.message}</p>}
          <form className={`composer ${busy ? 'composer-busy' : ''}`} onSubmit={event => void send(event)}>
            <textarea aria-label="Mesajınız" placeholder="Firik'e bir mesaj yazın…" value={active ? drafts[active] ?? '' : ''} maxLength={65536} disabled={!active || disconnected}
              onChange={event => active && useUI.getState().draft(active, event.target.value)} onKeyDown={event => {
                if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); if (!busy) event.currentTarget.form?.requestSubmit(); }
              }} />
            <div className="composer-toolbar"><div className="model-controls">
              <select aria-label="Sohbet modeli" value={chat?.model ?? data.settings.default_model} disabled={!chat || busy || sending} onChange={event => {
                client.setQueryData<ChatState>(['chat', active], old => old ? { ...old, model: event.target.value, reasoning_effort: null } : old);
              }}>{[...new Set([chat?.model ?? data.settings.default_model, ...data.models.models])].map(model => <option key={model}>{model}</option>)}</select>
              {chat && (data.models.effort_levels[chat.model]?.length ?? 0) > 0 && <select aria-label="Düşünme seviyesi" value={chat.reasoning_effort ?? ''} disabled={busy} onChange={event => {
                client.setQueryData<ChatState>(['chat', active], old => old ? { ...old, reasoning_effort: event.target.value || null } : old);
              }}><option value="">Varsayılan düşünme</option>{data.models.effort_levels[chat.model].map(level => <option key={level}>{level}</option>)}</select>}
              <div className="tools-picker"><Button type="button" variant="ghost" className="tools-trigger" onClick={() => setToolsOpen(!toolsOpen)} disabled={!chat || busy}><Terminal size={14} /> Araçlar <span>{chat?.active_mcp_servers.length ?? 0}</span></Button>
                {toolsOpen && <div className="tools-popover"><strong>Sohbet araçları</strong>{data.mcps.map(mcp => <label key={mcp.name}><input type="checkbox" checked={chat?.active_mcp_servers.includes(mcp.name) ?? false} onChange={event => {
                  if (!active) return;
                  void window.firik.mcp.set({ conversation_id: active, server_name: mcp.name, enabled: event.target.checked }).then(result => client.setQueryData(['chat', active], result)).catch(showError);
                }} />{mcp.name}<span>{mcp.status}</span></label>)}{data.mcps.length === 0 && <p>Yapılandırılmış MCP bulunmuyor.</p>}</div>}
              </div>
            </div>
              {busy && run ? <Button type="button" className="send-button stop-button" aria-label="Yanıtı durdur" onClick={() => void window.firik.runs.cancel({ run_id: run.run_id }).catch(showError)}><Square size={14} fill="currentColor" /></Button>
                : <Button type="submit" className="send-button" aria-label="Mesajı gönder" disabled={!active || !drafts[active]?.trim() || sending || disconnected || !data.settings.has_api_key}><ArrowUp size={19} /></Button>}
            </div>
          </form>
          <div className="composer-hint"><span>Enter ile gönder <span className="hint-separator">·</span> Shift + Enter ile yeni satır</span><span>Firik Agent <span className="hint-separator">/</span> Hibrit önizleme</span></div>
        </div>
        </div>
        {browserOpen && active && (
          <>
            <div
              role="separator"
              tabIndex={0}
              aria-orientation="vertical"
              aria-label="Tarayıcı paneli genişliğini ayarla"
              aria-valuemin={MIN_BROWSER_PANEL_WIDTH}
              aria-valuemax={maxBrowserPanelWidth}
              aria-valuenow={effectiveBrowserWidth}
              className={`browser-resizer ${resizingBrowser ? 'active' : ''}`}
              onDoubleClick={() => useUI.getState().resetBrowserPanelWidth(workspaceWidth)}
              onKeyDown={event => {
                const step = event.shiftKey ? 40 : 16;
                if (event.key === 'ArrowLeft') {
                  event.preventDefault();
                  useUI.getState().setBrowserPanelWidth(effectiveBrowserWidth + step, workspaceWidth);
                } else if (event.key === 'ArrowRight') {
                  event.preventDefault();
                  useUI.getState().setBrowserPanelWidth(effectiveBrowserWidth - step, workspaceWidth);
                } else if (event.key === 'Home') {
                  event.preventDefault();
                  useUI.getState().resetBrowserPanelWidth(workspaceWidth);
                }
              }}
              onPointerDown={event => {
                if (event.button !== 0) return;
                event.preventDefault();
                const startX = event.clientX;
                const startWidth = effectiveBrowserWidth;
                const currentWorkspaceWidth = workspaceRef.current?.clientWidth ?? workspaceWidth;
                setResizingBrowser(true);
                const target = event.currentTarget;
                if (typeof target.setPointerCapture === 'function') {
                  try {
                    target.setPointerCapture(event.pointerId);
                  } catch {
                    // ignore synthetic pointer capture failure
                  }
                }
                const onMove = (moveEvent: PointerEvent) => {
                  const deltaX = startX - moveEvent.clientX;
                  useUI.getState().setBrowserPanelWidth(startWidth + deltaX, currentWorkspaceWidth);
                };
                const onUp = () => {
                  setResizingBrowser(false);
                  window.removeEventListener('pointermove', onMove);
                  window.removeEventListener('pointerup', onUp);
                  window.removeEventListener('pointercancel', onUp);
                };
                window.addEventListener('pointermove', onMove);
                window.addEventListener('pointerup', onUp);
                window.addEventListener('pointercancel', onUp);
              }}
            />
            <BrowserPanel
              key={active}
              conversationId={active}
              initialGrant={chat?.browser}
              width={effectiveBrowserWidth}
              obscured={settingsOpen || toolsOpen}
              onClose={() => useUI.setState({ browserOpen: false })}
            />
          </>
        )}
        {settingsOpen && <Suspense fallback={null}><SettingsDialog snapshot={data} /></Suspense>}
      </div>}
    </main>
  </div>;
}

createRoot(document.getElementById('root')!).render(<StrictMode><QueryClientProvider client={queryClient}><App /></QueryClientProvider></StrictMode>);
