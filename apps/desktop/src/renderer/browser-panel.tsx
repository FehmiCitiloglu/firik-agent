import { useCallback, useEffect, useRef, useState, type FormEvent, type KeyboardEvent, type MouseEvent, type WheelEvent } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import {
  ArrowLeft,
  ArrowRight,
  Eye,
  Globe,
  Hand,
  Play,
  Plus,
  RefreshCw,
  Settings2,
  ShieldCheck,
  X,
} from 'lucide-react';
import type { BrowserGrantState, BrowserStatusState, ChatState } from '@firik/contracts';
import { Button } from './components/ui/button.js';
import { showError } from './store.js';

export function normalizeAddressEntry(raw: string): string {
  const value = raw.trim();
  if (!value) {
    throw new Error('Bir web adresi veya arama terimi girin.');
  }
  if ([...value].some(ch => ch.charCodeAt(0) < 32)) {
    throw new Error('Adres çubuğunda denetim karakteri kullanılamaz.');
  }
  if (/^[a-zA-Z][a-zA-Z0-9+.-]*:\/\//.test(value)) {
    const parsed = new URL(value);
    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
      throw new Error('Yalnız http veya https adresleri açılabilir.');
    }
    return value;
  }
  // `scheme:payload` without slashes (javascript:, data:, file:, about:...) is
  // never a navigation target; `host:port` forms are still accepted.
  const schemeLike = /^([a-zA-Z][a-zA-Z0-9+.-]*):(.*)$/.exec(value);
  if (schemeLike && !/^\d+(?:[/?#].*)?$/.test(schemeLike[2])) {
    throw new Error('Yalnız http veya https adresleri açılabilir.');
  }
  const hostPart = value.split('/')[0] ?? '';
  const isLoopback = /^(localhost|127\.\d+\.\d+\.\d+|\[::1\])(:\d+)?$/i.test(hostPart);
  if (isLoopback) {
    // Local development servers rarely have TLS; they still need an explicit
    // private-origin grant before the worker will load them.
    return `http://${value}`;
  }
  const looksLikeHost = !value.includes(' ') && hostPart.includes('.');
  if (looksLikeHost) {
    return `https://${value}`;
  }
  return `https://www.google.com/search?q=${encodeURIComponent(value)}`;
}

interface BrowserPanelProps {
  conversationId: string;
  initialGrant?: BrowserGrantState;
  onClose: () => void;
}

const activeStates = new Set(['ready', 'busy', 'manual_control']);
function canPreview(status: BrowserStatusState | null): boolean {
  return Boolean(status && activeStates.has(status.state ?? '') && status.grant?.enabled && status.grant?.screenshots);
}

export function BrowserPanel({ conversationId, initialGrant, onClose }: BrowserPanelProps) {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<BrowserStatusState | null>(null);
  const [address, setAddress] = useState('');
  const [manualText, setManualText] = useState('');
  const [busy, setBusy] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [enabledFlag, setEnabledFlag] = useState(Boolean(initialGrant?.enabled));
  const [screenshotsFlag, setScreenshotsFlag] = useState(Boolean(initialGrant?.screenshots));
  const [draftEnabled, setDraftEnabled] = useState(enabledFlag);
  const [draftScreenshots, setDraftScreenshots] = useState(screenshotsFlag);
  const [originsText, setOriginsText] = useState((initialGrant?.origins ?? []).join('\n'));
  const [privateOriginsText, setPrivateOriginsText] = useState((initialGrant?.private_origins ?? []).join('\n'));
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [connectionError, setConnectionError] = useState<string | null>(null);
  const previewUrlRef = useRef<string | null>(null);
  const statusRef = useRef<BrowserStatusState | null>(null);
  const alive = useRef(true);
  const previewEpoch = useRef(0);
  const previewPending = useRef(false);
  const statusPending = useRef(false);
  const operationEpoch = useRef(0);
  const inputPending = useRef(false);

  const clearPreview = useCallback(() => {
    previewEpoch.current++;
    if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current);
    previewUrlRef.current = null;
    if (alive.current) setPreviewUrl(null);
  }, []);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      clearPreview();
    };
  }, [clearPreview]);

  const applyStatus = useCallback(
    (next: BrowserStatusState) => {
      if (!alive.current) return;
      const previous = statusRef.current;
      statusRef.current = next;
      setStatus(next);
      setConnectionError(null);
      // Polling must not overwrite an address the user is still typing.
      if (next.url && next.url !== previous?.url) setAddress(next.url === 'about:blank' ? '' : next.url);
      setEnabledFlag(Boolean(next.grant?.enabled));
      setScreenshotsFlag(Boolean(next.grant?.screenshots));
      if (!canPreview(next) || previous?.active_tab_id !== next.active_tab_id ||
          previous?.revision !== next.revision || JSON.stringify(previous?.grant) !== JSON.stringify(next.grant)) {
        clearPreview();
      }
      queryClient.setQueryData<ChatState>(['chat', conversationId], old =>
        old && next.grant ? { ...old, browser: next.grant } : old,
      );
    },
    [clearPreview, conversationId, queryClient],
  );

  const refreshStatus = useCallback(async () => {
    if (!enabledFlag || statusPending.current) return;
    statusPending.current = true;
    const epoch = operationEpoch.current;
    try {
      const next = await window.firik.browser.command({ conversation_id: conversationId, command: 'status' });
      if (operationEpoch.current === epoch) applyStatus(next);
    } catch (error) {
      if (alive.current && operationEpoch.current === epoch) {
        clearPreview();
        setConnectionError(error instanceof Error ? error.message : 'Tarayıcı durumu alınamadı.');
      }
    } finally {
      statusPending.current = false;
    }
  }, [applyStatus, clearPreview, conversationId, enabledFlag]);

  useEffect(() => {
    void refreshStatus();
    const timer = setInterval(() => void refreshStatus(), 1500);
    return () => clearInterval(timer);
  }, [refreshStatus]);

  const isSessionActive = Boolean(status && activeStates.has(status.state ?? ''));

  const capturePreview = useCallback(async () => {
    if (!alive.current || !canPreview(statusRef.current) || previewPending.current) return;
    previewPending.current = true;
    const epoch = previewEpoch.current;
    try {
      const bytes = await window.firik.browser.preview({ conversation_id: conversationId, width: 960, height: 600 });
      // The panel can unmount or lose permission while the binary IPC call is
      // outstanding. Never create a Blob URL for that obsolete response.
      if (!alive.current || previewEpoch.current !== epoch || !canPreview(statusRef.current)) return;
      if (!bytes?.byteLength) {
        clearPreview();
        return;
      }
      const copy = new Uint8Array(bytes.byteLength);
      copy.set(bytes);
      const nextUrl = URL.createObjectURL(new Blob([copy], { type: 'image/png' }));
      if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current);
      previewUrlRef.current = nextUrl;
      setPreviewUrl(nextUrl);
    } catch {
      if (alive.current && previewEpoch.current === epoch) clearPreview();
    } finally {
      previewPending.current = false;
    }
  }, [clearPreview, conversationId]);

  useEffect(() => {
    if (!enabledFlag || !screenshotsFlag || !isSessionActive) {
      clearPreview();
      return;
    }
    void capturePreview();
    const timer = setInterval(() => void capturePreview(), 1500);
    return () => clearInterval(timer);
  }, [capturePreview, clearPreview, enabledFlag, isSessionActive, screenshotsFlag]);

  function toggleSettings() {
    if (!settingsOpen) {
      const grant = statusRef.current?.grant ?? initialGrant;
      setDraftEnabled(Boolean(grant?.enabled));
      setDraftScreenshots(Boolean(grant?.screenshots));
      setOriginsText((grant?.origins ?? []).join('\n'));
      setPrivateOriginsText((grant?.private_origins ?? []).join('\n'));
    }
    setSettingsOpen(!settingsOpen);
  }

  async function savePermissions(nextEnabled: boolean, nextScreenshots: boolean) {
    setBusy(true);
    operationEpoch.current++;
    clearPreview();
    try {
      const origins = originsText.split(/[\n,]/).map(item => item.trim()).filter(Boolean);
      const privateOrigins = privateOriginsText.split(/[\n,]/).map(item => item.trim()).filter(Boolean);
      const next = await window.firik.browser.configure({
        conversation_id: conversationId, enabled: nextEnabled, screenshots: nextScreenshots,
        origins, private_origins: privateOrigins,
      });
      applyStatus(next);
      if (alive.current) setSettingsOpen(false);
    } catch (err) {
      if (alive.current) showError(err);
    } finally {
      if (alive.current) setBusy(false);
    }
  }

  async function runCommand(
    command: Parameters<typeof window.firik.browser.command>[0]['command'],
    extra?: { url?: string; tab_id?: string },
  ) {
    if (!enabledFlag || busy) return;
    setBusy(true);
    operationEpoch.current++;
    clearPreview();
    try {
      const next = await window.firik.browser.command({ conversation_id: conversationId, command, ...extra });
      applyStatus(next);
      if (command !== 'close') void capturePreview();
    } catch (err) {
      if (alive.current) showError(err);
    } finally {
      if (alive.current) setBusy(false);
    }
  }

  async function onNavigateSubmit(event: FormEvent) {
    event.preventDefault();
    try {
      const targetUrl = normalizeAddressEntry(address);
      setAddress(targetUrl);
      await runCommand('navigate', { url: targetUrl });
    } catch (err) {
      showError(err);
    }
  }

  async function runInput(input: Omit<Parameters<typeof window.firik.browser.input>[0], 'conversation_id'>) {
    if (statusRef.current?.state !== 'manual_control' || busy || inputPending.current) return;
    inputPending.current = true;
    operationEpoch.current++;
    clearPreview();
    try {
      const next = await window.firik.browser.input({ conversation_id: conversationId, ...input });
      applyStatus(next);
      void capturePreview();
    } catch (err) {
      if (alive.current) showError(err);
    } finally {
      inputPending.current = false;
    }
  }

  async function onPreviewClick(event: MouseEvent<HTMLImageElement>) {
    const rect = event.currentTarget.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return;
    // The image itself keeps the viewport aspect ratio, including in a short
    // panel; normalized coordinates refer to its actual painted area.
    const x = Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width));
    const y = Math.min(1, Math.max(0, (event.clientY - rect.top) / rect.height));
    await runInput({ kind: 'click', x, y });
  }

  async function onPreviewWheel(event: WheelEvent<HTMLImageElement>) {
    await runInput({ kind: 'wheel', delta_y: Math.max(-2000, Math.min(2000, event.deltaY)) });
  }

  async function sendManualText(event: FormEvent) {
    event.preventDefault();
    if (!manualText || statusRef.current?.state !== 'manual_control' || inputPending.current) return;
    const text = manualText;
    setManualText('');
    await runInput({ kind: 'type', text });
  }

  async function sendManualKey(key: string) {
    await runInput({ kind: 'key', key });
  }

  function onViewportKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (status?.state !== 'manual_control') return;
    const allowedKeys = new Set([
      'Enter',
      'Tab',
      'Escape',
      'Backspace',
      'Delete',
      'ArrowUp',
      'ArrowDown',
      'ArrowLeft',
      'ArrowRight',
      'PageUp',
      'PageDown',
      'Home',
      'End',
      ' ',
    ]);
    if (allowedKeys.has(event.key)) {
      event.preventDefault();
      void sendManualKey(event.key);
    }
  }

  const tabs = status?.tabs ?? [];
  const manualMode = status?.state === 'manual_control';

  return (
    <aside className="browser-panel" aria-label="Tarayıcı paneli">
      <div className="browser-header">
        <div className="browser-title">
          <Globe size={15} />
          <strong>Tarayıcı</strong>
          <span className={`browser-state-badge ${manualMode ? 'manual' : isSessionActive ? 'ready' : ''}`}>
            {manualMode ? 'Elle kontrol' : isSessionActive ? 'Ajan kontrolü' : 'Hazır'}
          </span>
        </div>
        <div className="browser-header-actions">
          {isSessionActive && (
            <Button
              variant="outline"
              onClick={() => void runCommand(manualMode ? 'resume_after_handoff' : 'request_handoff')}
              disabled={busy}
            >
              {manualMode ? <Play size={13} /> : <Hand size={13} />}
              {manualMode ? 'Ajana geri ver' : 'Kontrolü al'}
            </Button>
          )}
          <Button
            variant="ghost"
            aria-label="Tarayıcı izinleri"
            title="Tarayıcı izinleri"
            onClick={toggleSettings}
          >
            <Settings2 size={15} />
          </Button>
          <Button variant="ghost" aria-label="Tarayıcı panelini kapat" onClick={onClose}>
            <X size={15} />
          </Button>
        </div>
      </div>

      <div className="browser-tabs" role="tablist" aria-label="Tarayıcı sekmeleri">
        {tabs.map(tab => (
          <div key={tab.tab_id} className={`browser-tab ${tab.active ? 'active' : ''}`}>
            <button
              type="button"
              role="tab"
              aria-selected={tab.active}
              className="browser-tab-select"
              disabled={busy}
              onClick={() => void runCommand('select_tab', { tab_id: tab.tab_id })}
            >
              <span>{tab.title || tab.url || 'Yeni sekme'}</span>
            </button>
            {tabs.length > 1 && (
              <button
                type="button"
                aria-label="Sekmeyi kapat"
                className="browser-tab-close"
                disabled={busy}
                onClick={() => void runCommand('close_tab', { tab_id: tab.tab_id })}
              >
                <X size={11} />
              </button>
            )}
          </div>
        ))}
        <button
          type="button"
          className="browser-tab-new"
          aria-label="Yeni sekme aç"
          title="Yeni sekme aç"
          disabled={busy || !enabledFlag}
          onClick={() => void runCommand('new_tab')}
        >
          <Plus size={13} />
        </button>
      </div>

      <form className="browser-nav" onSubmit={event => void onNavigateSubmit(event)}>
        <Button
          type="button"
          variant="ghost"
          aria-label="Geri"
          disabled={busy || !isSessionActive}
          onClick={() => void runCommand('back')}
        >
          <ArrowLeft size={14} />
        </Button>
        <Button
          type="button"
          variant="ghost"
          aria-label="İleri"
          disabled={busy || !isSessionActive}
          onClick={() => void runCommand('forward')}
        >
          <ArrowRight size={14} />
        </Button>
        <Button
          type="button"
          variant="ghost"
          aria-label="Yenile"
          disabled={busy || !isSessionActive}
          onClick={() => void runCommand('reload')}
        >
          <RefreshCw size={14} className={busy ? 'spin' : ''} />
        </Button>
        <input
          aria-label="Adres veya arama"
          placeholder="Adres girin veya web'de arayın…"
          value={address}
          onChange={event => setAddress(event.target.value)}
        />
        <Button type="submit" variant="default" disabled={busy || !enabledFlag || !address.trim()}>
          Git
        </Button>
      </form>

      {settingsOpen && (
        <div className="browser-settings-drawer" role="region" aria-label="Tarayıcı izin ayarları">
          <label className="browser-checkbox">
            <input
              type="checkbox"
              checked={draftEnabled}
              onChange={event => setDraftEnabled(event.target.checked)}
            />
            <span>Bu sohbette tarayıcı erişimine izin ver</span>
          </label>
          <label className="browser-checkbox">
            <input
              type="checkbox"
              checked={draftScreenshots}
              onChange={event => setDraftScreenshots(event.target.checked)}
            />
            <span>Canlı sayfa görünümü ve ekran görüntüsüne izin ver</span>
          </label>
          <label className="browser-field">
            <span>İzin verilen kökenler (her satıra bir tam http/https kökeni)</span>
            <textarea
              rows={2}
              placeholder="https://example.com&#10;http://127.0.0.1:3000"
              value={originsText}
              onChange={event => setOriginsText(event.target.value)}
            />
          </label>
          <label className="browser-field">
            <span>Yerel / özel ağ kökenleri (yukarıdaki listede de yer almalıdır)</span>
            <textarea
              rows={2}
              placeholder="http://127.0.0.1:3000"
              value={privateOriginsText}
              onChange={event => setPrivateOriginsText(event.target.value)}
            />
          </label>
          <div className="browser-settings-actions">
            {isSessionActive && (
              <Button type="button" variant="outline" onClick={() => void runCommand('close')}>
                Oturumu kapat
              </Button>
            )}
            <Button
              type="button"
              variant="default"
              disabled={busy}
              onClick={() => void savePermissions(draftEnabled, draftScreenshots)}
            >
              İzinleri kaydet
            </Button>
          </div>
        </div>
      )}

      {connectionError && <p className="browser-error" role="status">{connectionError}</p>}

      <div
        className={`browser-viewport ${manualMode ? 'manual-active' : ''}`}
        tabIndex={manualMode ? 0 : -1}
        onKeyDown={onViewportKeyDown}
      >
        {!enabledFlag ? (
          <div className="browser-empty">
            <ShieldCheck size={28} />
            <h3>Tarayıcı bu sohbet için kapalı</h3>
            <p>
              Ajanın veya sizin bu sohbette web sayfalarını açabilmeniz için tarayıcı iznini etkinleştirin.
            </p>
            <Button
              variant="default"
              disabled={busy}
              onClick={() => void savePermissions(true, true)}
            >
              Tarayıcıyı ve canlı görünümü etkinleştir
            </Button>
          </div>
        ) : !isSessionActive ? (
          <div className="browser-empty">
            <Globe size={28} />
            <h3>Tarayıcı hazır</h3>
            <p>Yukarıdaki adres çubuğundan bir sayfa açın veya oturumu başlatın.</p>
            <Button variant="outline" disabled={busy} onClick={() => void runCommand('start')}>
              Boş sekme başlat
            </Button>
          </div>
        ) : !screenshotsFlag ? (
          <div className="browser-empty">
            <Eye size={28} />
            <h3>Canlı önizleme kapalı</h3>
            <p>
              Sayfa açık ({status?.url || 'about:blank'}), ancak canlı ekran görüntüsü izni kapalı.
            </p>
            <Button
              variant="outline"
              disabled={busy}
              onClick={() => void savePermissions(true, true)}
            >
              Canlı görünümü aç
            </Button>
          </div>
        ) : previewUrl ? (
          <img
            src={previewUrl}
            alt={status?.title || status?.url || 'Tarayıcı önizlemesi'}
            className="browser-preview-image"
            onClick={event => void onPreviewClick(event)}
            onWheel={event => void onPreviewWheel(event)}
          />
        ) : (
          <div className="browser-empty">
            <RefreshCw size={22} className="spin" />
            <p>Sayfa görünümü alınıyor…</p>
          </div>
        )}
      </div>

      {manualMode && (
        <form className="browser-manual-bar" onSubmit={event => void sendManualText(event)}>
          <input
            aria-label="Elle kontrol metin girişi"
            placeholder="Seçili alana yazılacak metin…"
            value={manualText}
            onChange={event => setManualText(event.target.value)}
          />
          <Button type="submit" variant="outline" disabled={!manualText}>
            Yaz
          </Button>
          <Button type="button" variant="ghost" onClick={() => void sendManualKey('Enter')}>
            Enter
          </Button>
          <Button type="button" variant="ghost" onClick={() => void sendManualKey('Tab')}>
            Tab
          </Button>
        </form>
      )}
    </aside>
  );
}
