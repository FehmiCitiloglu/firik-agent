import type { ReactNode } from "react";
import { DownloadIcon, DriveIcon, MarkIcon, SearchIcon } from "./Icons";
import { formatBytes } from "../format";
import type { StorageInfo, View } from "../types";

interface Props {
  view: View;
  storage: StorageInfo | null;
  activeDownloads: number;
  onView: (view: View) => void;
  children: ReactNode;
}

export function AppShell({ view, storage, activeDownloads, onView, children }: Props) {
  const usage = storage ? Math.round((storage.used_bytes / storage.capacity_bytes) * 100) : 0;
  return <div className="window-shell">
    <header className="titlebar" aria-label="Application title bar">
      <div className="traffic-lights" aria-hidden="true"><i/><i/><i/></div>
      <strong>Firik Agent</strong>
    </header>
    <aside className="sidebar">
      <div className="brand"><MarkIcon/><span>Firik Agent</span></div>
      <nav aria-label="Model library">
        <button className={view === "discover" ? "active" : ""} onClick={() => onView("discover")}><SearchIcon/><span>Discover</span></button>
        <button className={view === "local" ? "active" : ""} onClick={() => onView("local")}><DriveIcon/><span>Local models</span></button>
        <button className={view === "downloads" ? "active" : ""} onClick={() => onView("downloads")}><DownloadIcon/><span>Downloads</span>{activeDownloads > 0 && <b>{activeDownloads}</b>}</button>
      </nav>
      <div className="storage-block">
        <div><DriveIcon/><strong>{formatBytes(storage?.free_bytes, 0)} available</strong></div>
        <span className="storage-track"><i style={{ width: `${usage}%` }}/></span>
        <small>{storage ? `${formatBytes(storage.model_cache_bytes)} in model cache` : "Reading storage…"}</small>
      </div>
    </aside>
    {children}
  </div>;
}
