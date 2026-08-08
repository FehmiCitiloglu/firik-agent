import type { DownloadJob, LocalModel, ModelDetail, ModelSummary, StorageInfo } from "./types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init);
  const payload = (await response.json()) as T & { error?: string };
  if (!response.ok) throw new Error(payload.error ?? `Request failed (${response.status})`);
  return payload;
}

export async function searchModels(query: string): Promise<ModelSummary[]> {
  const payload = await request<{ models: ModelSummary[] }>(
    `/api/models/search?q=${encodeURIComponent(query)}&limit=16`,
  );
  return payload.models;
}

export function getModel(modelId: string): Promise<ModelDetail> {
  return request(`/api/models/${encodeURIComponent(modelId)}`);
}

export async function getLocalModels(): Promise<LocalModel[]> {
  return (await request<{ models: LocalModel[] }>("/api/models/local")).models;
}

export async function getDownloads(): Promise<DownloadJob[]> {
  return (await request<{ downloads: DownloadJob[] }>("/api/downloads")).downloads;
}

export function getStorage(): Promise<StorageInfo> {
  return request("/api/system");
}

export function startDownload(modelId: string): Promise<DownloadJob> {
  return request("/api/downloads", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model_id: modelId }),
  });
}

export function downloadAction(jobId: string, action: "pause" | "resume" | "cancel") {
  return request<DownloadJob>(`/api/downloads/${jobId}/${action}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{}",
  });
}
