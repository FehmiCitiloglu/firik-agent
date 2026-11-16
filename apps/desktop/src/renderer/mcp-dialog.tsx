import { useState, type FormEvent } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import * as Dialog from '@radix-ui/react-dialog';
import { Check, FlaskConical, LoaderCircle, Pencil, Plug, Plus, RefreshCw, Trash2, Unplug, X } from 'lucide-react';
import type { MCPConfig, MCPCommandParams, MCPOperationResult, MCPServerInfo } from '@firik/contracts';
import { Button } from './components/ui/button.js';
import { useUI } from './store.js';
import './mcp.css';

const statusLabels: Record<string, string> = { configured: 'Yapılandırıldı', starting: 'Bağlanıyor',
  connected: 'Bağlı', disconnecting: 'Ayrılıyor', disconnected: 'Bağlı değil', error: 'Bağlantı hatası' };

function jsonMap(text: string, label: string): Record<string, string> {
  let value: unknown;
  try { value = JSON.parse(text); } catch { throw new Error(`${label}: geçerli bir JSON nesnesi girin.`); }
  if (!value || typeof value !== 'object' || Array.isArray(value) || Object.values(value).some(item => typeof item !== 'string'))
    throw new Error(`${label}: {"ANAHTAR": "değer"} biçimini kullanın.`);
  return value as Record<string, string>;
}

function MCPForm({ config, editing, disabled, onSave, onCancel }: { config: MCPConfig; editing: boolean;
  disabled: boolean; onSave: (config: MCPConfig) => Promise<void>; onCancel: () => void }) {
  const [name, setName] = useState(config.name);
  const [transport, setTransport] = useState(config.transport ?? 'stdio');
  const [command, setCommand] = useState(config.command ?? '');
  const [args, setArgs] = useState(JSON.stringify(config.args ?? []));
  const [cwd, setCwd] = useState(config.cwd ?? '');
  const [url, setURL] = useState(config.url ?? '');
  const [env, setEnv] = useState(JSON.stringify(config.env ?? {}, null, 2));
  const [secrets, setSecrets] = useState(JSON.stringify(config.secret_env ?? {}, null, 2));
  const [headers, setHeaders] = useState(JSON.stringify(config.headers ?? {}, null, 2));
  const [enabled, setEnabled] = useState(config.enabled ?? true);
  const [autostart, setAutostart] = useState(config.autostart ?? false);
  const [defaultForChat, setDefaultForChat] = useState(config.default_for_chat ?? false);
  const [policy, setPolicy] = useState(config.tool_policy?.mode ?? 'allow');
  const [error, setError] = useState('');

  async function submit(event: FormEvent) {
    event.preventDefault(); setError('');
    try {
      let argumentsList: unknown;
      try { argumentsList = transport === 'stdio' ? JSON.parse(args) : []; } catch { throw new Error('Argümanlar: geçerli bir JSON dizisi girin.'); }
      if (!Array.isArray(argumentsList) || argumentsList.some(item => typeof item !== 'string'))
        throw new Error('Argümanlar: ["-y", "paket-adı"] biçimini kullanın.');
      await onSave({ name: name.trim(), transport, command: command.trim(), args: argumentsList, cwd: cwd.trim(),
        url: url.trim(), env: jsonMap(env, 'Ortam değişkenleri'), secret_env: jsonMap(secrets, 'Gizli değişkenler'),
        headers: transport === 'stdio' ? {} : jsonMap(headers, 'HTTP başlıkları'), enabled, autostart, default_for_chat: defaultForChat,
        tool_policy: { ...config.tool_policy, mode: policy } });
    } catch (error) { setError(error instanceof Error ? error.message : 'Sunucu kaydedilemedi.'); }
  }

  return <form className="settings-form mcp-form" onSubmit={submit}>
    <h3>{editing ? `${config.name} · Düzenle` : 'Yeni MCP sunucusu'}</h3>
    <fieldset disabled={disabled}>
      <label>Sunucu adı<input required maxLength={256} value={name} readOnly={editing} placeholder="ör. dosyalar" onChange={e => setName(e.target.value)} /></label>
      <label>Bağlantı türü<select aria-label="Bağlantı türü" value={transport} onChange={e => setTransport(e.target.value as typeof transport)}>
        <option value="stdio">Yerel komut (stdio)</option><option value="http">Uzak sunucu (HTTP)</option><option value="sse">Uzak sunucu (SSE)</option>
      </select></label>
      {transport === 'stdio' ? <>
        <label>Komut<input required value={command} placeholder="npx, uvx veya çalıştırılabilir dosya yolu" onChange={e => setCommand(e.target.value)} /></label>
        <label>Argümanlar (JSON)<textarea aria-label="Argümanlar (JSON)" rows={2} value={args} placeholder={'["-y", "paket-adı"]'} spellCheck={false} onChange={e => setArgs(e.target.value)} /></label>
        <p className="mcp-help">Her argümanı ayrı bir metin olarak yazın. Komut alanına yalnızca program adını veya yolunu girin.</p>
        <label>Çalışma klasörü<input value={cwd} placeholder="İsteğe bağlı · /tam/klasör/yolu" onChange={e => setCwd(e.target.value)} /></label>
      </> : <>
        <label>Sunucu URL’si<input required type="url" value={url} placeholder="https://sunucunuz.example/mcp" onChange={e => setURL(e.target.value)} /></label>
        <label>HTTP başlıkları (JSON)<textarea aria-label="HTTP başlıkları (JSON)" rows={3} value={headers} spellCheck={false} onChange={e => setHeaders(e.target.value)} /></label>
        <p className="mcp-help">Örnek: {'{"Authorization": "Bearer anahtarınız"}'}. Kayıtlı değerler gösterilmez; boş değer mevcut başlığı korur. Silmek için anahtarı kaldırın.</p>
      </>}
      <details className="mcp-advanced"><summary>Ortam değişkenleri ve araç izinleri</summary>
        <label>Ortam değişkenleri (JSON)<textarea aria-label="Ortam değişkenleri (JSON)" rows={3} value={env} spellCheck={false} onChange={e => setEnv(e.target.value)} /></label>
        <label>Gizli ortam değişkenleri (JSON)<textarea aria-label="Gizli ortam değişkenleri (JSON)" rows={3} value={secrets} spellCheck={false} onChange={e => setSecrets(e.target.value)} /></label>
        <p className="mcp-help">Gizli değerler mevcut anahtar kasası altyapısına kaydedilir. Boş değer kayıtlı sırrı korur; silmek için anahtarı kaldırın.</p>
        <label>Araç izinleri<select aria-label="Araç izinleri" value={policy} onChange={e => setPolicy(e.target.value as typeof policy)}>
          <option value="allow">Araçlara izin ver</option><option value="confirm">Araçları onayla çalıştır</option><option value="deny">Araçları engelle</option>
        </select></label>
      </details>
      <div className="mcp-options">
        <label><input type="checkbox" checked={enabled} onChange={e => setEnabled(e.target.checked)} /> Sunucu etkin</label>
        <label><input type="checkbox" checked={autostart} onChange={e => setAutostart(e.target.checked)} /> Uygulama açıldığında bağlan</label>
        <label><input type="checkbox" checked={defaultForChat} onChange={e => setDefaultForChat(e.target.checked)} /> Yeni sohbetlerde varsayılan olarak seç</label>
      </div>
    </fieldset>
    {error && <p role="alert" className="inline-error">{error}</p>}
    <div className="mcp-actions"><Button type="button" variant="ghost" disabled={disabled} onClick={onCancel}>Vazgeç</Button>
      <Button type="submit" disabled={disabled}>{disabled ? <LoaderCircle size={15} className="spin" /> : <Check size={15} />} Sunucuyu kaydet</Button></div>
  </form>;
}

export default function MCPDialog() {
  const client = useQueryClient();
  const disconnected = useUI(state => state.disconnected);
  const running = useUI(state => Object.values(state.runs).some(run => run.status === 'running'));
  const [draft, setDraft] = useState<MCPConfig | null>(null);
  const [editing, setEditing] = useState(false);
  const [pending, setPending] = useState('');
  const [deleting, setDeleting] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState<MCPOperationResult | null>(null);
  const servers = useQuery({ queryKey: ['mcps'], queryFn: () => window.firik.mcp.list({}), refetchInterval: 2000 });
  const disabled = Boolean(pending) || running || disconnected;

  async function refresh() {
    await Promise.all([client.invalidateQueries({ queryKey: ['mcps'] }), client.invalidateQueries({ queryKey: ['snapshot'] }),
      client.invalidateQueries({ queryKey: ['chat'] })]);
  }

  async function operate(name: string, command: MCPCommandParams['command']) {
    setPending(name); setError(''); setNotice(null);
    try { setNotice(await window.firik.mcp.command({ name, command })); }
    catch (error) { setError(error instanceof Error ? error.message : 'MCP işlemi tamamlanamadı.'); }
    finally { await refresh(); setPending(''); }
  }

  async function remove(name: string) {
    setPending(name); setError(''); setNotice(null);
    try { await window.firik.mcp.remove({ name }); setDeleting(''); setNotice({ success: true, message: `${name} silindi.` }); }
    catch (error) { setError(error instanceof Error ? error.message : 'Sunucu silinemedi.'); }
    finally { await refresh(); setPending(''); }
  }

  async function save(config: MCPConfig) {
    setPending(config.name); setError(''); setNotice(null);
    try {
      await window.firik.mcp.save({ mode: editing ? 'update' : 'create', config });
      setDraft(null); setNotice({ success: true, message: `${config.name} kaydedildi. Sohbette Araçlar menüsünden seçebilirsiniz.` });
    } finally { await refresh(); setPending(''); }
  }

  function card(server: MCPServerInfo) {
    const { config, status } = server;
    const connected = status === 'connected';
    const transitioning = status === 'starting' || status === 'disconnecting';
    return <article className="mcp-card" key={config.name} aria-label={`${config.name} sunucusu`}>
      <div className="mcp-card-heading"><strong>{config.name}</strong><span className={`mcp-status ${status}`}>{config.enabled === false ? 'Devre dışı' : statusLabels[status] ?? status}</span></div>
      <p className="mcp-endpoint">{config.transport === 'stdio' ? config.command : config.url}</p>
      <p className="mcp-help">{config.transport?.toUpperCase()} · {server.tool_count} araç{config.default_for_chat ? ' · Yeni sohbetlerde seçili' : ''}</p>
      {server.error && <p className="inline-error">{server.error}</p>}
      {pending === config.name && <p className="mcp-help" role="status"><LoaderCircle size={13} className="spin" /> İşlem sürüyor…</p>}
      <div className="mcp-card-actions">
        <Button variant="outline" disabled={disabled || transitioning || (!connected && config.enabled === false)} onClick={() => void operate(config.name, connected ? 'disconnect' : 'connect')}>
          {connected ? <Unplug size={14} /> : <Plug size={14} />}{connected ? 'Bağlantıyı kes' : 'Bağlan'}</Button>
        <Button variant="ghost" disabled={disabled || transitioning} onClick={() => void operate(config.name, 'test')}><FlaskConical size={14} /> Test et</Button>
        {connected && <Button variant="ghost" disabled={disabled} aria-label={`${config.name} yeniden başlat`} onClick={() => void operate(config.name, 'restart')}><RefreshCw size={14} /></Button>}
        <Button variant="ghost" disabled={disabled} aria-label={`${config.name} düzenle`} onClick={() => { setDraft(config); setEditing(true); setDeleting(''); setNotice(null); }}><Pencil size={14} /></Button>
        <Button variant="ghost" disabled={disabled} aria-label={`${config.name} sil`} onClick={() => setDeleting(config.name)}><Trash2 size={14} /></Button>
      </div>
      {deleting === config.name && <div className="mcp-delete"><p>{config.name} kaldırılacak ve sohbetlerdeki seçimi temizlenecek.</p>
        <Button variant="ghost" disabled={disabled} onClick={() => setDeleting('')}>Vazgeç</Button>
        <Button variant="outline" disabled={disabled} onClick={() => void remove(config.name)}>Sunucuyu sil</Button></div>}
    </article>;
  }

  return <Dialog.Root open onOpenChange={open => { if (!open && !pending) useUI.setState({ mcpOpen: false }); }}>
    <Dialog.Portal><Dialog.Overlay className="dialog-overlay" /><Dialog.Content className="dialog-content mcp-dialog">
      <div className="dialog-heading"><div><span className="eyebrow">ARAÇ BAĞLANTILARI</span><Dialog.Title>MCP sunucuları</Dialog.Title></div>
        <Dialog.Close asChild><Button variant="ghost" disabled={Boolean(pending)} aria-label="MCP yönetimini kapat"><X size={18} /></Button></Dialog.Close></div>
      <Dialog.Description>Yerel ve uzak MCP sunucularını ekleyin, bağlantılarını test edin ve sohbet araçlarınızı yönetin.</Dialog.Description>
      {running && <p className="mcp-help">Değişiklik yapmadan önce çalışan yanıtı durdurun.</p>}
      {disconnected && <p role="alert" className="inline-error">Python bağlantısı kesildi. Uygulamayı yeniden başlatın.</p>}
      {error && <p role="alert" className="inline-error">{error}</p>}
      {notice && <div role={notice.success ? 'status' : 'alert'} className={notice.success ? 'mcp-notice' : 'inline-error'}>{notice.message}
        {Boolean(notice.tool_names?.length) && <p className="mcp-help">{notice.tool_names!.join(', ')}</p>}</div>}
      {draft ? <MCPForm key={`${editing}:${draft.name}`} config={draft} editing={editing} disabled={disabled} onSave={save} onCancel={() => setDraft(null)} /> : <>
        <div className="mcp-toolbar"><span>{servers.data?.servers.length ?? 0} sunucu</span>
          <Button variant="ghost" aria-label="MCP listesini yenile" disabled={Boolean(pending)} onClick={() => void refresh()}><RefreshCw size={15} /></Button>
          <Button disabled={disabled || !servers.data} onClick={() => { setDraft({ name: '', transport: 'stdio' }); setEditing(false); setDeleting(''); setNotice(null); }}><Plus size={15} /> MCP ekle</Button></div>
        {servers.isPending && <p className="mcp-help" role="status">Sunucular yükleniyor…</p>}
        {servers.isError && <p role="alert" className="inline-error">MCP listesi yüklenemedi. Listeyi yenileyerek tekrar deneyin.</p>}
        {servers.data?.servers.length === 0 && <div className="mcp-empty"><Plug size={26} /><h3>İlk MCP sunucunuzu ekleyin</h3><p>“MCP ekle” ile bir komut veya sunucu URL’si tanımlayın.</p></div>}
        <div className="mcp-list">{servers.data?.servers.map(card)}</div>
        <p className="mcp-help">Kaydetme sunucuyu listeye ekler. Kullanmak için Bağlan’a basın veya sohbetin Araçlar menüsünden seçin.</p>
      </>}
    </Dialog.Content></Dialog.Portal>
  </Dialog.Root>;
}
