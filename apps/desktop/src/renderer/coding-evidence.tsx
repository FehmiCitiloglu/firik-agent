export type EvidenceKind = 'plan' | 'changes' | 'tests';
export type EvidenceRow = Record<string, unknown>;
type Tone = 'success' | 'warning' | 'danger' | 'neutral';

const statusNames: Record<string, string> = {
  idle: 'Hazır', preparing: 'Bağlam hazırlanıyor', queued: 'Sırada', running: 'Çalışıyor',
  understanding: 'Projeyi inceliyor', planning: 'Planlıyor', applying: 'Uyguluyor', implementing: 'Uyguluyor', verifying: 'Doğruluyor',
  paused: 'Duraklatıldı', awaiting_approval: 'Onay bekliyor', waiting_approval: 'Onay bekliyor',
  awaiting_user: 'Yanıt bekliyor', completed: 'Tamamlandı', completed_verified: 'Doğrulandı',
  completed_unverified: 'Doğrulama eksik', cancelled: 'Durduruldu', failed: 'Başarısız',
  blocked: 'İlerleyemiyor', interrupted: 'Kesildi', pending: 'Bekliyor', in_progress: 'Sürüyor',
};
export const codingStatusName = (status?: string) => status ? statusNames[status] ?? status : 'Yükleniyor…';
const str = (value: unknown) => typeof value === 'string' ? value : '';

/** Preserve recorded execution and freshness; missing historical fields prove no result. */
export function testEvidenceStatus(row: EvidenceRow): { label: string; tone: Tone } {
  const status = str(row.status).toLowerCase();
  if (status === 'blocked') return { label: 'Engellendi', tone: 'warning' };
  if (row.executed === false || ['unexecuted', 'not_run', 'skipped'].includes(status))
    return { label: 'Çalıştırılmadı', tone: 'neutral' };
  if (['running', 'in_progress', 'started'].includes(status)) return { label: 'Çalışıyor', tone: 'neutral' };
  if (['pending', 'queued', 'waiting'].includes(status)) return { label: 'Bekliyor', tone: 'neutral' };
  if (['cancelled', 'interrupted'].includes(status)) return { label: 'Durduruldu', tone: 'warning' };
  if (row.timed_out === true) return { label: 'Süre aşıldı', tone: 'danger' };
  if (status === 'stale' || row.stale === true)
    return { label: 'Yeniden doğrulanmalı', tone: 'warning' };
  if (row.passed === false || ['failed', 'error'].includes(status)
      || (typeof row.exit_code === 'number' && row.exit_code !== 0))
    return { label: 'Başarısız', tone: 'danger' };
  if (row.fresh === false) return { label: 'Yeniden doğrulanmalı', tone: 'warning' };
  if (['unverified', 'unknown'].includes(status)) return { label: 'Doğrulama eksik', tone: 'warning' };
  if (row.passed === true || ['passed', 'success'].includes(status))
    return row.fresh === true ? { label: 'Başarılı', tone: 'success' }
      : { label: 'Başarılı · Güncellik bilinmiyor', tone: 'warning' };
  return { label: 'Sonuç bilinmiyor', tone: 'neutral' };
}

function TestEvidence({ row }: { row: EvidenceRow }) {
  const status = testEvidenceStatus(row);
  const stdout = str(row.stdout);
  const stderr = str(row.stderr);
  const output = str(row.output);
  const notes = [...new Set([row.stale_reason, row.reason, row.detail].map(str).filter(Boolean))];
  return <>
    <span className="evidence-status" data-tone={status.tone}>{status.label}</span>
    <pre className="evidence-command">{str(row.command) || str(row.criterion) || 'Komut bilgisi kaydedilmemiş.'}</pre>
    <dl className="evidence-metadata">
      {typeof row.exit_code === 'number' && <div><dt>Çıkış kodu</dt><dd>{row.exit_code}</dd></div>}
      {typeof row.duration_sec === 'number' && Number.isFinite(row.duration_sec) && <div><dt>Süre</dt><dd>{row.duration_sec} sn</dd></div>}
      {str(row.evidence_id) && <div><dt>Kanıt</dt><dd>{str(row.evidence_id)}</dd></div>}
    </dl>
    {notes.map(note => <p key={note} className="evidence-note">{note}</p>)}
    {stdout && <section aria-label="Standart çıktı"><h4>Standart çıktı</h4><pre>{stdout}</pre></section>}
    {stderr && <section aria-label="Hata çıktısı"><h4>Hata çıktısı</h4><pre>{stderr}</pre></section>}
    {output && output !== stdout && output !== stderr && <section aria-label="Kaydedilen çıktı"><h4>Kaydedilen çıktı</h4><pre>{output}</pre></section>}
  </>;
}

export function CodingEvidence({ kind, text, readOnly = false }: { kind: EvidenceKind; text: string; readOnly?: boolean }) {
  const empty = kind === 'plan' ? 'Bu oturumda henüz bir plan oluşturulmadı.'
    : kind === 'tests' ? 'Henüz test çalıştırılmadı. Bu oturum için doğrulama kanıtı yok.'
    : readOnly ? 'Bu modda dosya değişikliği yapılmaz.' : 'Bu oturumda henüz dosya değişikliği yok.';
  let rows: EvidenceRow[];
  try {
    const value: unknown = JSON.parse(text || '[]');
    if (!Array.isArray(value)) return <div className="evidence-legacy"><p className="muted">Önceki kayıt biçimi</p><pre>{text}</pre></div>;
    rows = value.filter((row): row is EvidenceRow => !!row && typeof row === 'object' && !Array.isArray(row));
  } catch { return <div className="evidence-legacy"><p className="muted">Önceki kayıt biçimi</p><pre>{text}</pre></div>; }
  if (!rows.length) return <p className="evidence-empty">{empty}</p>;
  return <div className="coding-evidence">{rows.map((row, i) => <article key={str(row.id) || str(row.change_id) || str(row.evidence_id) || i}>
    {kind === 'plan' ? <><strong>{str(row.title)}</strong><p>{codingStatusName(str(row.status))}</p>
      {Array.isArray(row.acceptance_criteria) && <ul>{row.acceptance_criteria.map((item, j) => <li key={j}>{str(item)}</li>)}</ul>}
      {str(row.verification) && <p>Doğrulama: {str(row.verification)}</p>}</>
      : kind === 'changes' ? <><strong>{str(row.path)}</strong>{row.undone === true && <p>Geri alındı</p>}<pre className="evidence-diff">{str(row.diff)}</pre></>
      : <TestEvidence row={row} />}
  </article>)}</div>;
}
