import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { downloadAction, getDownloads, getLocalModels, getModel, getStorage, searchModels, startDownload } from "./api";
import { AppShell } from "./components/AppShell";
import { ConfirmDownload } from "./components/ConfirmDownload";
import { DownloadsView } from "./components/DownloadsView";
import { DownloadIcon, DriveIcon, SearchIcon, ToolIcon } from "./components/Icons";
import { LocalModelsView } from "./components/LocalModelsView";
import { ModelInspector } from "./components/ModelInspector";
import { ModelTable } from "./components/ModelTable";
import type { DownloadJob, LocalModel, ModelDetail, ModelSummary, StorageInfo, View } from "./types";

export default function App() {
  const [view, setView] = useState<View>("discover");
  const [query, setQuery] = useState("coding");
  const [models, setModels] = useState<ModelSummary[]>([]);
  const [selected, setSelected] = useState<ModelDetail | ModelSummary | null>(null);
  const [modelLoading, setModelLoading] = useState(false);
  const [searching, setSearching] = useState(true);
  const [toolOnly, setToolOnly] = useState(false);
  const [under16, setUnder16] = useState(false);
  const [local, setLocal] = useState<LocalModel[]>([]);
  const [localLoading, setLocalLoading] = useState(true);
  const [downloads, setDownloads] = useState<DownloadJob[]>([]);
  const [storage, setStorage] = useState<StorageInfo | null>(null);
  const [confirming, setConfirming] = useState<ModelDetail | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refreshLocal = useCallback(async () => {
    setLocalLoading(true);
    try { setLocal(await getLocalModels()); } catch (reason) { setError(String(reason)); } finally { setLocalLoading(false); }
  }, []);
  const refreshDownloads = useCallback(async () => {
    try { setDownloads(await getDownloads()); } catch (reason) { setError(String(reason)); }
  }, []);
  const refreshStorage = useCallback(async () => {
    try { setStorage(await getStorage()); } catch (reason) { setError(String(reason)); }
  }, []);

  const performSearch = useCallback(async (term: string) => {
    setSearching(true); setError(null);
    try {
      const result = await searchModels(term);
      setModels(result);
      if (result[0]) {
        setSelected(result[0]); setModelLoading(true);
        try { setSelected(await getModel(result[0].model_id)); } finally { setModelLoading(false); }
      } else setSelected(null);
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setSearching(false); }
  }, []);

  useEffect(() => { void performSearch("coding"); void refreshLocal(); void refreshDownloads(); void refreshStorage(); }, [performSearch, refreshDownloads, refreshLocal, refreshStorage]);
  useEffect(() => {
    const active = downloads.some(job => ["preparing", "downloading", "paused", "cancelling"].includes(job.status));
    if (!active) return;
    const timer = window.setInterval(() => { void refreshDownloads(); void refreshLocal(); void refreshStorage(); }, 1000);
    return () => window.clearInterval(timer);
  }, [downloads, refreshDownloads, refreshLocal, refreshStorage]);

  const localIds = useMemo(() => new Set(local.map(item => item.model_id)), [local]);
  const activeDownloads = downloads.filter(job => ["preparing", "downloading", "paused", "cancelling"].includes(job.status));
  const visibleModels = models.filter(model => (!toolOnly || model.tool_calling) && (!under16 || model.size_bytes == null || model.size_bytes <= 16 * 1024 ** 3));
  const selectedId = selected?.model_id ?? null;
  const isDownloading = activeDownloads.some(job => job.model_id === selectedId);

  async function selectModel(model: ModelSummary) {
    setSelected(model); setModelLoading(true); setError(null);
    try { setSelected(await getModel(model.model_id)); } catch (reason) { setError(String(reason)); } finally { setModelLoading(false); }
  }
  async function confirmDownload() {
    if (!confirming) return;
    setStarting(true); setError(null);
    try { await startDownload(confirming.model_id); setConfirming(null); await refreshDownloads(); setView("downloads"); }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setStarting(false); }
  }
  async function actOnDownload(job: DownloadJob, action: "pause" | "resume" | "cancel") {
    if (action === "cancel" && !window.confirm("Cancel this download? Partial data will be kept for resume.")) return;
    try { await downloadAction(job.job_id, action); await refreshDownloads(); } catch (reason) { setError(String(reason)); }
  }
  function submit(event: FormEvent) { event.preventDefault(); void performSearch(query); }

  return <AppShell view={view} storage={storage} activeDownloads={activeDownloads.length} onView={setView}>
    {view === "discover" && <>
      <main className="content discover-content">
        <div className="view-heading discover-heading"><h1>Find a model for your machine</h1></div>
        <form className="search-form" onSubmit={submit}><SearchIcon/><input aria-label="Search open models" value={query} onChange={event => setQuery(event.target.value)} placeholder="Search coding, tool use, model name…"/><button type="submit">Search</button></form>
        <div className="filters"><button className="fixed-filter"><span>▱</span>Text generation</button><button className={toolOnly ? "active" : ""} onClick={() => setToolOnly(value => !value)}><ToolIcon/>Tool use</button><button className={under16 ? "active" : ""} onClick={() => setUnder16(value => !value)}><DriveIcon/>Under 16 GB</button>{(toolOnly || under16) && <button className="clear-filters" onClick={() => { setToolOnly(false); setUnder16(false); }}>Clear filters</button>}</div>
        {error && <div className="error-banner" role="alert">{error}<button onClick={() => setError(null)}>Dismiss</button></div>}
        <ModelTable models={visibleModels} selectedId={selectedId} localIds={localIds} loading={searching} onSelect={model => void selectModel(model)}/>
        <footer className="statusbar"><span><DownloadIcon/>{activeDownloads.length ? `${activeDownloads.length} active download${activeDownloads.length > 1 ? "s" : ""}` : "No active downloads"}</span><button onClick={() => setView("downloads")}>View downloads <span>›</span></button></footer>
      </main>
      <ModelInspector model={selected} loading={modelLoading} isLocal={selectedId ? localIds.has(selectedId) : false} isDownloading={isDownloading} onDownload={() => selected && "safe_serialization" in selected && setConfirming(selected)}/>
    </>}
    {view === "local" && <LocalModelsView models={local} loading={localLoading}/>} 
    {view === "downloads" && <DownloadsView downloads={downloads} onAction={(job, action) => void actOnDownload(job, action)}/>} 
    {confirming && <ConfirmDownload model={confirming} storage={storage} busy={starting} onCancel={() => setConfirming(null)} onConfirm={() => void confirmDownload()}/>} 
  </AppShell>;
}
