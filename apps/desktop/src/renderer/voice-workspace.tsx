import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Mic, Pause, Play, Square, Volume2 } from 'lucide-react';
import type { ApprovalState, ChatState, VoiceCommandParams, VoicePreferences, VoiceState } from '@firik/contracts';
import { Button } from './components/ui/button.js';
import { WorkspaceMarkdown } from './workspace-markdown.js';
import { showError, useUI } from './store.js';

const labels: Record<string, string> = { disabled: 'Ses kapalı', idle: 'Hazır', ready: 'Hazır', listening: 'Dinliyor',
  speech_detected: 'Konuşma algılandı', transcribing: 'Ses çözümleniyor', awaiting_confirmation: 'Metni inceleyin',
  thinking: 'Yanıt hazırlanıyor', executing: 'İşlem yürütülüyor', speaking: 'Konuşuyor', interrupted: 'Duraklatıldı',
  permission_required: 'Mikrofon izni gerekiyor', error: 'Ses işlemi tamamlanamadı' };

export default function VoiceWorkspace({ chat, approvals, onCreate }: {
  chat?: ChatState; approvals: ApprovalState[]; onCreate: () => void;
}) {
  if (!chat) return <div className="workspace-empty"><Mic size={32} /><h2>Sesli asistan</h2><p>Sesli görüşme için bir sohbet açın.</p><Button onClick={onCreate}>Yeni sohbet</Button></div>;
  return <VoiceSession key={chat.conversation_id} chat={chat} approvals={approvals} />;
}

function VoiceSession({ chat, approvals }: { chat: ChatState; approvals: ApprovalState[] }) {
  const client = useQueryClient();
  const disconnected = useUI(state => state.disconnected);
  const sid = chat.conversation_id;
  const voice = useQuery({ queryKey: ['voice', sid], queryFn: () => window.firik.voice.get({ conversation_id: sid }),
    enabled: !disconnected, refetchInterval: disconnected ? false : 1200, staleTime: 0 });
  const latest = useRef<VoiceState | undefined>(undefined);
  latest.current = voice.data;
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    const off = window.firik.onEvent(event => {
      if (event.kind === 'voice' && event.conversation_id === sid && !timer) timer = setTimeout(() => {
        timer = undefined; void client.invalidateQueries({ queryKey: ['voice', sid] });
      }, 80);
    });
    return () => {
      off(); if (timer) clearTimeout(timer);
      const current = latest.current;
      if (current) void window.firik.voice.command({ conversation_id: sid, scope_id: current.scope_id, action: 'cancel' }).catch(() => {});
    };
  }, [client, sid]);
  const command = useMutation({ mutationFn: (p: Pick<VoiceCommandParams, 'action' | 'text' | 'review_id'>) => window.firik.voice.command({
    conversation_id: sid, scope_id: voice.data!.scope_id, ...p }), onSuccess: result => {
      client.setQueryData(['voice', sid], result);
      void client.invalidateQueries({ queryKey: ['chat', sid] });
      void client.invalidateQueries({ queryKey: ['snapshot'] });
    }, onError: showError });
  const data = voice.data;
  const busy = command.isPending || disconnected || !data;
  const listening = data?.state === 'listening' || data?.state === 'speech_detected';
  const lastReply = chat.messages.filter(m => m.role === 'assistant' && !m.pending).at(-1)?.content;

  return <section className="voice-workspace" aria-label="Sesli asistan"><div className="voice-session-controls workspace-card">
    <div className={`voice-orb ${listening ? 'listening' : ''}`}><Mic size={35} /></div><h2>Sesli asistan</h2>
    <p role="status">{disconnected ? 'Bağlantı kesildi' : labels[data?.state ?? ''] ?? 'Ses oturumu hazırlanıyor…'}</p>
    <p className="muted">{chat.title} · {chat.model}</p>
    {voice.isError && <p className="workspace-error" role="alert">{voice.error.message}</p>}
    {data?.error && <p className="workspace-error" role="alert">{data.error}</p>}
    <div className="voice-actions"><Button disabled={busy || !data?.settings.enabled || !!data.review_id || data.state === 'transcribing' || !!chat.run}
      onClick={() => command.mutate({ action: listening ? 'stop_recording' : 'record' })}>{listening ? <Square size={16} /> : <Mic size={16} />}{listening ? 'Kaydı bitir' : 'Konuş'}</Button>
      <Button variant="outline" disabled={busy || !data?.settings.enabled || data.conversation_active || !!data.review_id || !!chat.run}
        onClick={() => command.mutate({ action: 'conversation' })}>Görüşmeyi başlat</Button>
      {data?.conversation_active && <Button variant="outline" disabled={busy} onClick={() => command.mutate({ action: data.paused ? 'resume' : 'pause' })}>
        {data.paused ? <Play size={15} /> : <Pause size={15} />}{data.paused ? 'Dinlemeye devam et' : 'Dinlemeyi duraklat'}</Button>}
      <Button variant="outline" disabled={busy} onClick={() => command.mutate({ action: 'cancel' })}><Square size={15} /> Sesi durdur</Button>
      <Button variant="ghost" disabled={busy || !data?.settings.enabled || !lastReply || listening} onClick={() => command.mutate({ action: 'speak', text: lastReply })}><Volume2 size={15} /> Son yanıtı oku</Button>
    </div>
    {data?.review_id && <TranscriptReview key={data.review_id} transcript={data.transcript ?? ''} disabled={busy || !!chat.run}
      onSubmit={text => command.mutate({ action: 'submit', text, review_id: data.review_id })}
      onDiscard={() => command.mutate({ action: 'discard', review_id: data.review_id })} />}
    {data && <VoiceSettings key={JSON.stringify(data.settings)} settings={data.settings} disabled={busy || !!chat.run || data.conversation_active} />}
  </div><div className="voice-conversation workspace-card"><h3>Sohbet</h3>
    {chat.messages.length === 0 && <p className="muted">Konuşmanız metne çevrildiğinde göndermeden önce burada inceleyebilirsiniz.</p>}
    {chat.messages.map(message => <article className="workspace-message" key={message.id}><strong>{message.role === 'user' ? 'Siz' : 'Firik'}</strong><WorkspaceMarkdown text={message.content} /></article>)}
    {approvals.map(approval => <div className="approval-card" key={approval.approval_id}><strong>İşlem onayı: {approval.tool_name}</strong><pre>{approval.preview}</pre><div className="approval-actions">
      {[false, true].map(allowed => <Button key={String(allowed)} disabled={disconnected} variant={allowed ? 'default' : 'outline'} onClick={() => {
        void window.firik.approvals.resolve({ approval_id: approval.approval_id, run_id: approval.run_id, allowed }).catch(showError);
      }}>{allowed ? 'Onayla' : 'Reddet'}</Button>)}</div></div>)}
    {chat.run && <Button variant="outline" disabled={disconnected} onClick={() => void window.firik.runs.cancel({ run_id: chat.run!.run_id }).catch(showError)}>Ajan yanıtını durdur</Button>}
  </div></section>;
}

function TranscriptReview({ transcript, disabled, onSubmit, onDiscard }: {
  transcript: string; disabled: boolean; onSubmit: (text: string) => void; onDiscard: () => void;
}) {
  const [text, setText] = useState(transcript);
  return <form className="transcript-review" onSubmit={e => { e.preventDefault(); onSubmit(text.trim()); }}><label>Göndermeden önce metni inceleyin
    <textarea aria-label="Ses transkripti" value={text} onChange={e => setText(e.target.value)} maxLength={32000} rows={4} disabled={disabled} /></label>
    <div className="voice-actions"><Button type="button" variant="outline" disabled={disabled} onClick={onDiscard}>Vazgeç</Button><Button disabled={disabled || !text.trim()}>İnceledim, sohbete gönder</Button></div></form>;
}

function VoiceSettings({ settings, disabled }: { settings: VoicePreferences; disabled: boolean }) {
  const [value, setValue] = useState(settings);
  const client = useQueryClient();
  const save = useMutation({ mutationFn: () => window.firik.voice.settings(value), onSuccess: () => client.invalidateQueries({ queryKey: ['voice'] }), onError: showError });
  return <details className="voice-settings" open={!settings.enabled}><summary>Ses ayarları</summary><form onSubmit={e => { e.preventDefault(); save.mutate(); }}>
    <label className="workspace-check"><input type="checkbox" checked={value.enabled} disabled={disabled} onChange={e => setValue({ ...value, enabled: e.target.checked })} /> Ses özelliklerini etkinleştir</label>
    <label>Konuşma dili<select aria-label="Konuşma dili" value={value.language} disabled={disabled} onChange={e => setValue({ ...value, language: e.target.value as VoicePreferences['language'] })}>
      <option value="auto">Otomatik</option><option value="tr">Türkçe</option><option value="en">İngilizce</option></select></label>
    <label>Ses çözümleme<select aria-label="Ses çözümleme sağlayıcısı" value={value.asr_provider} disabled={disabled} onChange={e => setValue({ ...value, asr_provider: e.target.value as VoicePreferences['asr_provider'] })}>
      <option value="evren">Evren</option><option value="local">Yerel</option></select></label>
    <label>Seslendirme<select aria-label="Seslendirme sağlayıcısı" value={value.tts_provider} disabled={disabled} onChange={e => setValue({ ...value, tts_provider: e.target.value as VoicePreferences['tts_provider'] })}>
      <option value="evren">Evren</option><option value="system">Sistem sesi</option></select></label>
    <label className="workspace-check"><input type="checkbox" checked={value.read_replies} disabled={disabled} onChange={e => setValue({ ...value, read_replies: e.target.checked })} /> Yanıtları sesli oku</label>
    <label>Oynatma hızı<input aria-label="Oynatma hızı" type="number" min={0.25} max={4} step={0.25} value={value.playback_speed} disabled={disabled}
      onChange={e => setValue({ ...value, playback_speed: Number(e.target.value) })} /></label>
    <Button variant="outline" disabled={disabled || save.isPending}>Ses ayarlarını kaydet</Button>
  </form></details>;
}
