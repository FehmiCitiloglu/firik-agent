import { DownloadIcon, DriveIcon, SparkIcon, ToolIcon } from "./Icons";
import { formatBytes, formatCount, formatParameters, shortName } from "../format";
import type { ModelDetail, ModelSummary } from "../types";

interface Props {
  model: ModelDetail | ModelSummary | null;
  loading: boolean;
  isLocal: boolean;
  isDownloading: boolean;
  onDownload: () => void;
}

export function ModelInspector({ model, loading, isLocal, isDownloading, onDownload }: Props) {
  if (!model) return <aside className="inspector empty-inspector"><SparkIcon/><p>Select a model to inspect compatibility, license, and download size.</p></aside>;
  const detail = "description" in model ? model : null;
  return <aside className="inspector">
    <div className="inspector-scroll">
      <h2>{shortName(model.model_id)}</h2>
      <dl className="facts">
        <div><dt>License</dt><dd>{model.license ?? "Not declared"}</dd></div>
        <div><dt>Parameters</dt><dd>{formatParameters(model.parameters)} parameters</dd></div>
        <div><dt>Download size</dt><dd>{loading ? "Checking…" : formatBytes(model.size_bytes)}</dd></div>
        <div><dt>Downloads</dt><dd>{formatCount(model.downloads)}</dd></div>
        <div><dt>Capabilities</dt><dd>{model.tool_calling ? "Tool calling" : "Text generation"}</dd></div>
      </dl>
      <section className="about-model"><h3>About this model</h3><p>{detail?.description ?? "Loading verified model metadata…"}</p></section>
      <div className="compatibility"><ToolIcon/><div><strong>{detail?.safe_serialization ? "Safetensors weights" : "Checking weight format"}</strong><span>Remote model code stays disabled</span></div></div>
    </div>
    <div className="inspector-actions">
      <button className="primary-action" disabled={loading || isLocal || isDownloading || !detail?.safe_serialization} onClick={onDownload}>
        {isLocal ? <DriveIcon/> : <DownloadIcon/>}
        {isLocal ? "Available locally" : isDownloading ? "Downloading…" : "Download model"}
      </button>
      <a className="secondary-action" href={detail?.url ?? `https://huggingface.co/${model.model_id}`} target="_blank" rel="noreferrer">View model card</a>
    </div>
  </aside>;
}
