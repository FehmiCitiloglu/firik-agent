import { useEffect, useId, useRef, useState, type CSSProperties, type FormEvent } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowDown, ChevronDown, ChevronUp, Copy, LoaderCircle, RefreshCw, Send, Square, Terminal } from 'lucide-react';
import type { TerminalCommand } from '@firik/contracts';
import { Button } from './components/ui/button.js';
import { showError, useUI } from './store.js';
import { terminalContext, terminalText } from './terminal-output.js';
import './terminal.css';

const running = (command?: TerminalCommand) => !!command && ['starting', 'preparing', 'running', 'stopping'].includes(command.status ?? 'starting');
const statuses: Record<string, string> = { starting: 'Başlatılıyor', preparing: 'Hazırlanıyor', running: 'Çalışıyor', stopping: 'Durduruluyor',
  completed: 'Tamamlandı', cancelled: 'Durduruldu', timed_out: 'Süre aşıldı', error: 'Başlatılamadı', failed: 'Başarısız', uncertain: 'Sonuç belirsiz' };
function commandStatus(command: TerminalCommand) {
  if (command.status === 'completed' && command.exit_code !== null && command.exit_code !== undefined && command.exit_code !== 0) return 'Başarısız';
  return statuses[command.status ?? 'starting'] ?? command.status;
}

export function CodingTerminal({ projectId, codingState }: { projectId: string; codingState?: { busy?: boolean; session_id: string } }) {
  const client = useQueryClient();
  const disconnected = useUI(state => state.disconnected);
  const open = useUI(state => state.terminalOpen);
  const setOpen = (value: boolean) => useUI.setState({ terminalOpen: value });
  const draftId = `terminal:${projectId}`;
  const command = useUI(state => state.drafts[draftId] ?? '');
  const setCommand = (value: string) => useUI.getState().draft(draftId, value);
  const [cwd, setCwd] = useState('.');
  const [timeout, setTimeoutSeconds] = useState(60);
  const [selectedId, setSelectedId] = useState('');
  const [newOutput, setNewOutput] = useState(false);
  const [copied, setCopied] = useState(false);
  const [height, setHeight] = useState(240);
  const [maxHeight, setMaxHeight] = useState(440);
  const panelId = useId();
  const container = useRef<HTMLElement>(null);
  const resizeStart = useRef<{ y: number; height: number } | null>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const output = useRef<HTMLDivElement>(null);
  const following = useRef(true);
  const list = useQuery({ queryKey: ['terminal', projectId], queryFn: () => window.firik.terminal.list({ project_id: projectId }),
    enabled: !disconnected, refetchInterval: query => query.state.data?.busy ? 500 : false, staleTime: 0 });
  const preferences = useQuery({ queryKey: ['coding-preferences', projectId], queryFn: () => window.firik.coding.preferences({ project_id: projectId }), enabled: !disconnected });
  const commands = list.data?.commands ?? [];
  const selected = commands.find(row => row.command_id === selectedId) ?? commands.at(-1);
  const live = commands.find(row => row.source === 'user' && running(row));
  const busy = !!list.data?.busy;
  const maxTimeout = list.data?.max_timeout ?? 300;
  const runTimeout = Math.min(timeout, maxTimeout);
  const timeoutOptions = [...new Set([30, 60, 120, 300, maxTimeout].filter(value => value <= maxTimeout))].sort((a, b) => a - b);
  const docker = list.data?.backend === 'docker';
  const start = useMutation({ mutationFn: (text: string) => window.firik.terminal.start({ project_id: projectId, command: text, cwd: cwd.trim() || '.', timeout: runTimeout }),
    onSuccess: async (result, submitted) => {
      setSelectedId(result.command_id); following.current = true; setNewOutput(false);
      if (useUI.getState().drafts[draftId]?.trim() === submitted) setCommand('');
      await client.invalidateQueries({ queryKey: ['terminal', projectId] });
    }, onError: showError });
  const cancel = useMutation({ mutationFn: () => window.firik.terminal.cancel({ project_id: projectId, command_id: live!.command_id }),
    onSuccess: () => client.invalidateQueries({ queryKey: ['terminal', projectId] }), onError: showError });

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    const off = window.firik.onEvent(event => {
      if (event.kind !== 'terminal' || event.project_id !== projectId) return;
      if (!timer) timer = setTimeout(() => {
        timer = undefined;
        void client.invalidateQueries({ queryKey: ['terminal', projectId] });
        void client.invalidateQueries({ queryKey: ['git', projectId], refetchType: 'none' });
        void client.invalidateQueries({ queryKey: ['project-files', projectId], refetchType: 'none' });
        void client.invalidateQueries({ queryKey: ['project-file', projectId], refetchType: 'none' });
      }, 100);
    });
    return () => { off(); if (timer) clearTimeout(timer); };
  }, [client, projectId]);
  useEffect(() => {
    following.current = true; setNewOutput(false); setCopied(false);
  }, [selected?.command_id]);
  useEffect(() => {
    if (!open || !output.current) return;
    if (following.current) output.current.scrollTop = output.current.scrollHeight;
    else setNewOutput(true);
  }, [selected?.stdout, selected?.stderr, selected?.command_id, open]);
  useEffect(() => { if (open) input.current?.focus(); }, [open]);
  useEffect(() => {
    const parent = container.current?.parentElement;
    if (!parent) return;
    const measure = () => setMaxHeight(Math.max(210, Math.min(440, Math.floor(parent.clientHeight * .49))));
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(parent);
    return () => observer.disconnect();
  }, []);
  function resize(value: number) { setHeight(Math.round(Math.max(210, Math.min(maxHeight, value)))); }

  const disabled = busy || !!codingState?.busy || start.isPending || disconnected || list.isPending || preferences.data?.allow_terminal !== true || preferences.data?.allow_reads !== true;
  function submit(event: FormEvent) {
    event.preventDefault();
    if (!disabled && command.trim()) start.mutate(command.trim());
  }
  function attach() {
    if (!selected || !codingState?.session_id) return;
    const target = `coding:${codingState.session_id}`;
    const current = useUI.getState().drafts[target] ?? '';
    const text = [current, terminalContext(selected)].filter(Boolean).join('\n\n');
    if (text.length > 65536) { showError(new Error('Kodlama taslağı dolu. Komut çıktısını eklemeden önce taslağı kısaltın.')); return; }
    useUI.getState().draft(target, text);
    useUI.setState({ codingTool: 'agent', terminalOpen: false });
  }
  async function copy() {
    if (!selected) return;
    try { await navigator.clipboard.writeText(terminalContext(selected)); setCopied(true); }
    catch (error) { showError(error); }
  }
  return <section ref={container} className={`coding-terminal${open ? ' open' : ''}`} aria-label="Terminal paneli" style={{ '--terminal-height': `${Math.min(height, maxHeight)}px` } as CSSProperties}>
    {open && <div className="terminal-resizer" role="separator" aria-label="Terminal yüksekliğini ayarla" aria-orientation="horizontal" tabIndex={0}
      aria-valuemin={210} aria-valuemax={maxHeight} aria-valuenow={Math.min(height, maxHeight)}
      onKeyDown={event => {
        const next = event.key === 'ArrowUp' ? height + 20 : event.key === 'ArrowDown' ? height - 20 : event.key === 'Home' ? 210 : event.key === 'End' ? maxHeight : null;
        if (next !== null) { event.preventDefault(); resize(next); }
      }}
      onPointerDown={event => { if (event.button !== 0) return; event.preventDefault(); resizeStart.current = { y: event.clientY, height: container.current?.clientHeight ?? height }; event.currentTarget.setPointerCapture(event.pointerId); }}
      onPointerMove={event => { if (resizeStart.current) resize(resizeStart.current.height + resizeStart.current.y - event.clientY); }}
      onPointerUp={event => { resizeStart.current = null; if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId); }}
      onPointerCancel={() => { resizeStart.current = null; }} />}
    <div className="terminal-heading"><Button variant="ghost" aria-expanded={open} aria-controls={panelId} onClick={() => setOpen(!open)}><Terminal size={15} /> Terminal {open ? <ChevronDown size={13} /> : <ChevronUp size={13} />}</Button>
      <span className="terminal-summary" role="status">{disconnected ? 'Bağlantı kesildi' : busy ? <><LoaderCircle size={12} className="spin" /> Komut çalışıyor</> : `${commands.length} komut · ${docker ? 'Docker' : 'Yerel makine'}`}</span>
      {live && <Button className="terminal-stop" variant="outline" disabled={cancel.isPending || disconnected} onClick={() => cancel.mutate()}><Square size={12} /> Komutu durdur</Button>}
      <Button variant="ghost" aria-label="Terminali yenile" disabled={disconnected || list.isFetching} onClick={() => void list.refetch()}><RefreshCw size={13} /></Button>
    </div>
    <div className="terminal-body" id={panelId} role="region" aria-label="Proje terminali" hidden={!open} onKeyDown={event => {
      if (event.key === 'Escape') { event.stopPropagation(); setOpen(false); }
    }}>
      {list.isError && <p className="workspace-error" role="alert">{list.error.message}</p>}
      <div className="terminal-content"><nav className="terminal-history" aria-label="Terminal komut geçmişi" title="Bu uygulama oturumundaki son 10 komut">
        {commands.slice().reverse().map(row => <button key={row.command_id} data-command-id={row.command_id} aria-current={selected?.command_id === row.command_id ? 'true' : undefined} className={selected?.command_id === row.command_id ? 'selected' : ''}
          onClick={() => setSelectedId(row.command_id)}><span>{row.source === 'agent' ? 'Ajan' : 'Siz'} · {commandStatus(row)}</span><code title={terminalText(row.command)}>{terminalText(row.command)}</code></button>)}
        {!commands.length && <p>{list.isPending ? 'Komutlar yükleniyor…' : 'Henüz komut çalıştırılmadı.'}</p>}
      </nav><div className="terminal-result" data-command-id={selected?.command_id} data-status={selected?.status}>
        {selected ? <>
          <div className="terminal-result-heading"><code title={terminalText(selected.command)}>{terminalText(selected.command)}</code><span className="terminal-result-state" data-error={selected.status === 'error' || (!!selected.exit_code && selected.exit_code !== 0)}>{commandStatus(selected)}</span></div>
          <div className="terminal-metadata"><span>{selected.source === 'agent' ? 'Ajan komutu' : 'Kullanıcı komutu'} · {selected.cwd || '.'}</span><span>{selected.backend === 'docker' ? 'Docker' : 'Yerel makine'}</span>
            {selected.exit_code !== null && selected.exit_code !== undefined && <span>Çıkış: {selected.exit_code}</span>}{!running(selected) && <span>{(selected.duration_seconds ?? 0).toFixed(2)} sn</span>}
            <Button variant="ghost" disabled={running(selected)} onClick={() => { setCommand(selected.command); setCwd(selected.cwd || '.'); input.current?.focus(); }}>Komutu düzenle</Button>
            <Button variant="ghost" onClick={() => void copy()} aria-label="Komut sonucunu kopyala"><Copy size={12} />{copied ? 'Kopyalandı' : 'Kopyala'}</Button>
            <Button variant="ghost" disabled={!codingState?.session_id || running(selected)} onClick={attach}><Send size={12} /> Çıktıyı ajana ekle</Button></div>
          <div ref={output} className="terminal-output" role="region" aria-label="Komut çıktısı" tabIndex={0} onScroll={() => {
            if (!output.current) return;
            following.current = output.current.scrollHeight - output.current.scrollTop - output.current.clientHeight < 32;
            if (following.current) setNewOutput(false);
          }}>
            {selected.stdout && <section aria-label="Terminal standart çıktı"><strong>stdout</strong><pre>{terminalText(selected.stdout)}</pre></section>}
            {selected.stderr && <section className="terminal-stderr" aria-label="Terminal hata çıktısı"><strong>stderr</strong><pre>{terminalText(selected.stderr)}</pre></section>}
            {!selected.stdout && !selected.stderr && <p>{running(selected) ? 'Çıktı bekleniyor…' : 'Komut çıktı üretmedi.'}</p>}
            {selected.error && <p className="terminal-stderr">{selected.error}</p>}
            {selected.truncated && <p>Çıktı boyut sınırında kısaltıldı.</p>}
            {selected.source === 'agent' && running(selected) && <p>Ajan komutunu durdurmak için kodlama çalışmasının Durdur düğmesini kullanın.</p>}
          </div>
          {newOutput && <Button className="terminal-latest" variant="ghost" onClick={() => { following.current = true; setNewOutput(false); if (output.current) output.current.scrollTop = output.current.scrollHeight; }}><ArrowDown size={12} /> Yeni çıktıya git</Button>}
        </> : <p className="terminal-empty">Proje komutlarını burada çalıştırın. Ajanın onaylanan komutları ve test sonuçları da bu alanda görünür.</p>}
      </div></div>
      <form className="terminal-composer" onSubmit={submit}>
        <textarea ref={input} rows={1} aria-label="Terminal komutu" placeholder="Çalıştırılacak komutu yazın…" value={command} onChange={event => setCommand(event.target.value)} maxLength={8000} spellCheck={false} disabled={disconnected}
          onKeyDown={event => { if (event.key === 'Enter' && (event.ctrlKey || event.metaKey) && !event.repeat && !event.nativeEvent.isComposing) submit(event); }} />
        <div className="terminal-controls"><label>Klasör<input aria-label="Komut çalışma klasörü" value={cwd} maxLength={4096} disabled={start.isPending || disconnected} onChange={event => setCwd(event.target.value)} /></label>
          <label>Süre<select aria-label="Komut zaman aşımı" value={runTimeout} disabled={start.isPending || disconnected} onChange={event => setTimeoutSeconds(Number(event.target.value))}>{timeoutOptions.map(value => <option key={value} value={value}>{value} sn</option>)}</select></label>
          <span className="terminal-submit-hint">⌘ / Ctrl + Enter</span><Button disabled={disabled || !command.trim()}>{start.isPending ? 'Başlatılıyor…' : 'Komutu çalıştır'}</Button></div>
        <p className="terminal-hint">Her komut ayrı çalışır; <code>cd</code> ve ortam değişkenleri sonraki komuta taşınmaz. Etkileşimli programlar desteklenmez.
          {codingState?.busy ? ' Kullanıcı komutu için ajan çalışmasının bitmesini bekleyin.' : preferences.data?.allow_terminal === false || preferences.data?.allow_reads === false ? ' Komut ve dosya okuma izinlerini İzinler sekmesinden açın.' : docker ? ' Docker komutu geçici proje kopyasında çalışır; dosya değişiklikleri projeye uygulanmaz.' : ' Başlangıç klasörü proje içindedir; yerel komutlar kullanıcı yetkileriyle çalışır.'}</p>
      </form>
    </div>
  </section>;
}
