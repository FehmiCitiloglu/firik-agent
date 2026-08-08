import { DownloadIcon } from "./Icons";
import { formatBytes, formatCount, formatParameters, shortName } from "../format";
import type { ModelSummary } from "../types";

interface Props {
  models: ModelSummary[];
  selectedId: string | null;
  localIds: Set<string>;
  loading: boolean;
  onSelect: (model: ModelSummary) => void;
}

export function ModelTable({ models, selectedId, localIds, loading, onSelect }: Props) {
  if (loading) return <div className="table-state"><span className="spinner"/>Searching the open model catalog…</div>;
  if (!models.length) return <div className="table-state">No matching Transformers models found.</div>;
  return <div className="model-table" role="table" aria-label="Model search results">
    <div className="model-row table-head" role="row">
      <span>Model</span><span>Publisher</span><span>Params</span><span>License</span><span>Size</span><span>Downloads</span><span>Status</span>
    </div>
    {models.map(model => <button
      type="button"
      role="row"
      className={`model-row ${selectedId === model.model_id ? "selected" : ""}`}
      key={model.model_id}
      onClick={() => onSelect(model)}
    >
      <span className="model-name"><i>☆</i><strong>{shortName(model.model_id)}</strong></span>
      <span>{model.author}</span>
      <span className="mono">{formatParameters(model.parameters)}</span>
      <span className="mono">{model.license ?? "—"}</span>
      <span className="mono">{formatBytes(model.size_bytes)}</span>
      <span className="mono">{formatCount(model.downloads)}</span>
      <span className={localIds.has(model.model_id) ? "ready" : "row-download"}>{localIds.has(model.model_id) ? "Ready" : <DownloadIcon/>}</span>
    </button>)}
  </div>;
}
