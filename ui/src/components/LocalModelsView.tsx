import { CheckIcon, DriveIcon } from "./Icons";
import { formatBytes, shortName } from "../format";
import type { LocalModel } from "../types";

export function LocalModelsView({ models, loading }: { models: LocalModel[]; loading: boolean }) {
  return <main className="content single-content">
    <div className="view-heading"><h1>Local models</h1><p>Models and partial snapshots stored in your Hugging Face cache.</p></div>
    <section className="local-list" aria-live="polite">
      {loading && <div className="table-state"><span className="spinner"/>Reading the local model cache…</div>}
      {!loading && !models.length && <div className="empty-state"><DriveIcon/><h2>No local models yet</h2><p>Discover a compatible model and confirm its download to make it available offline.</p></div>}
      {models.map(model => <article key={model.model_id} className="local-row">
        <CheckIcon/><div><strong>{shortName(model.model_id)}</strong><span>{model.model_id.split("/")[0]}</span></div>
        <span className="mono">{model.files} files</span><span className="mono">{formatBytes(model.size_bytes)}</span><b>Ready</b>
      </article>)}
    </section>
  </main>;
}
