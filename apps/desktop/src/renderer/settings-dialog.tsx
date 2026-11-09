import { useEffect, useState, type FormEvent } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import * as Dialog from '@radix-ui/react-dialog';
import { Check, LoaderCircle, ShieldCheck, X } from 'lucide-react';
import type { Snapshot } from '@firik/contracts';
import { Button } from './components/ui/button.js';
import { showError, useUI } from './store.js';

export default function SettingsDialog({ snapshot }: { snapshot: Snapshot }) {
  const open = useUI(state => state.settingsOpen);
  const client = useQueryClient();
  const [url, setURL] = useState(snapshot.settings.base_url);
  const [model, setModel] = useState(snapshot.settings.default_model);
  const [timeout, setTimeoutValue] = useState(snapshot.settings.timeout);
  const [key, setKey] = useState('');
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    if (open) { setURL(snapshot.settings.base_url); setModel(snapshot.settings.default_model);
      setTimeoutValue(snapshot.settings.timeout); setKey(''); }
  }, [open, snapshot.settings.base_url, snapshot.settings.default_model, snapshot.settings.timeout]);

  async function save(event: FormEvent) {
    event.preventDefault(); setSaving(true);
    try {
      await window.firik.settings.update({ base_url: url.trim(), default_model: model.trim(), timeout });
      if (key.trim()) await window.firik.settings.setKey({ api_key: key.trim() });
      setKey('');
      await client.invalidateQueries({ queryKey: ['snapshot'] });
      useUI.setState({ settingsOpen: false });
    } catch (error) { showError(error); await client.invalidateQueries({ queryKey: ['snapshot'] }); }
    finally { setSaving(false); }
  }

  return <Dialog.Root open={open} onOpenChange={settingsOpen => { if (!saving) useUI.setState({ settingsOpen }); }}>
    <Dialog.Portal><Dialog.Overlay className="dialog-overlay" /><Dialog.Content className="dialog-content">
      <div className="dialog-heading"><div><span className="eyebrow">ÇALIŞMA ALANINIZ</span>
        <Dialog.Title>Ayarlar</Dialog.Title></div><Dialog.Close asChild><Button variant="ghost" aria-label="Ayarları kapat"><X size={18} /></Button></Dialog.Close></div>
      <Dialog.Description>Firik bağlantınızı ve yeni sohbetlerin varsayılan modelini düzenleyin.</Dialog.Description>
      <form onSubmit={save} className="settings-form">
        <label>API adresi<input required type="url" value={url} onChange={e => setURL(e.target.value)} /></label>
        <label>Varsayılan model<input required value={model} maxLength={256} onChange={e => setModel(e.target.value)} /></label>
        <label>Zaman aşımı <span className="muted">saniye</span><input required type="number" min={5} max={1800} value={timeout} onChange={e => setTimeoutValue(Number(e.target.value))} /></label>
        <label>API anahtarı<span className="key-state">{snapshot.settings.has_api_key ? <><Check size={13} /> Anahtar yapılandırılmış</> : 'Henüz yapılandırılmadı'}</span>
          <input type="password" autoComplete="new-password" value={key} maxLength={8192} placeholder="Değiştirmek için yeni anahtarı girin" onChange={e => setKey(e.target.value)} /></label>
        <p className="form-note"><ShieldCheck size={15} /> Anahtar işletim sistemi kasasında saklanır.</p>
        <Button type="submit" disabled={saving || Object.values(useUI.getState().runs).some(run => run.status === 'running')}>
          {saving ? <LoaderCircle className="spin" size={16} /> : <Check size={16} />} Kaydet
        </Button>
      </form>
    </Dialog.Content></Dialog.Portal>
  </Dialog.Root>;
}

