import { DownloadIcon } from "./Icons";
import { formatBytes, shortName } from "../format";
import type { ModelDetail, StorageInfo } from "../types";

export function ConfirmDownload({ model, storage, busy, onCancel, onConfirm }: { model: ModelDetail; storage: StorageInfo | null; busy: boolean; onCancel: () => void; onConfirm: () => void }) {
  return <div className="modal-backdrop" role="presentation" onMouseDown={event => event.currentTarget === event.target && onCancel()}>
    <section className="confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="download-title">
      <DownloadIcon/><h2 id="download-title">Download {shortName(model.model_id)}?</h2>
      <p>Firik Agent will store safe model files in the Hugging Face cache. Model code is never executed during download.</p>
      <dl><div><dt>Download</dt><dd>{formatBytes(model.size_bytes)}</dd></div><div><dt>Free space</dt><dd>{formatBytes(storage?.free_bytes)}</dd></div><div><dt>License</dt><dd>{model.license ?? "Not declared"}</dd></div></dl>
      <div><button className="secondary-action" onClick={onCancel}>Cancel</button><button className="primary-action" disabled={busy} onClick={onConfirm}>{busy ? "Preparing…" : "Confirm download"}</button></div>
    </section>
  </div>;
}
