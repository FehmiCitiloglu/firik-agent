import { CheckIcon, CloseIcon, DownloadIcon, PauseIcon, PlayIcon } from "./Icons";
import { formatBytes, formatDuration, shortName } from "../format";
import type { DownloadJob } from "../types";

interface Props {
  downloads: DownloadJob[];
  onAction: (job: DownloadJob, action: "pause" | "resume" | "cancel") => void;
}

export function DownloadsView({ downloads, onAction }: Props) {
  const active = downloads.filter(job => ["preparing", "downloading", "paused", "cancelling"].includes(job.status));
  const finished = downloads.filter(job => !active.includes(job));
  const selected = active[0] ?? finished[0] ?? null;
  return <>
    <main className="content downloads-content">
      <div className="view-heading"><h1>Downloads</h1></div>
      <h3 className="section-label">Active downloads</h3>
      {!active.length && <div className="empty-downloads"><DownloadIcon/>No active downloads</div>}
      {active.map(job => <article className="download-row" key={job.job_id}>
        <DownloadIcon/><div className="download-main"><strong>{shortName(job.model_id)}</strong><div><span>{Math.round(job.progress * 100)}%</span><i><b style={{ width: `${job.progress * 100}%` }}/></i></div></div>
        <span className="mono">{formatBytes(job.downloaded_bytes)} of {formatBytes(job.total_bytes)}</span>
        <span className="mono">{formatBytes(job.bytes_per_second)}/s</span>
        <span>{formatDuration(job.eta_seconds)}</span>
        <button aria-label={job.status === "paused" ? "Resume" : "Pause"} onClick={() => onAction(job, job.status === "paused" ? "resume" : "pause")}>{job.status === "paused" ? <PlayIcon/> : <PauseIcon/>}</button>
        <button aria-label="Cancel" onClick={() => onAction(job, "cancel")}><CloseIcon/></button>
      </article>)}
      <h3 className="section-label completed-label">Completed</h3>
      {!finished.length && <p className="muted-copy">Completed downloads will appear here.</p>}
      {finished.map(job => <article className={`completed-row ${job.status}`} key={job.job_id}><CheckIcon/><strong>{shortName(job.model_id)}</strong><span>{formatBytes(job.total_bytes)}</span><b>{job.status === "complete" ? "Ready" : job.status}</b>{job.error && <small>{job.error}</small>}</article>)}
    </main>
    <aside className="inspector download-inspector">
      {selected ? <><div className="inspector-scroll"><h2>{shortName(selected.model_id)}</h2><div className="overall-progress"><span>Download progress <b>{Math.round(selected.progress * 100)}%</b></span><i><b style={{ width: `${selected.progress * 100}%` }}/></i><span><b>{formatBytes(selected.downloaded_bytes)} of {formatBytes(selected.total_bytes)}</b><b>{formatBytes(selected.bytes_per_second)}/s</b></span></div><h3>Files ({selected.files.length})</h3><div className="file-list">{selected.files.map(file => <div key={file.filename}><span>{file.status === "complete" ? <CheckIcon/> : <i className="file-dot"/>}<strong>{file.filename}</strong><em>{file.status}</em></span><small>{formatBytes(file.downloaded_bytes)} / {formatBytes(file.size_bytes)}</small><i><b style={{ width: `${file.size_bytes ? (file.downloaded_bytes / file.size_bytes) * 100 : 0}%` }}/></i></div>)}</div></div>{active.includes(selected) && <div className="inspector-actions"><button className="danger-action" onClick={() => onAction(selected, "cancel")}><CloseIcon/>Cancel download</button><small>Partial data is kept so the download can resume later.</small></div>}</> : <div className="empty-inspector"><DownloadIcon/><p>Downloads and file progress will appear here.</p></div>}
    </aside>
  </>;
}
