export type View = "discover" | "local" | "downloads";

export interface ModelSummary {
  model_id: string;
  author: string;
  downloads: number;
  likes: number;
  pipeline_tag: string | null;
  library_name: string | null;
  license: string | null;
  parameters: number | null;
  size_bytes: number | null;
  tool_calling: boolean;
  tags: string[];
  last_modified: string | null;
}

export interface ModelDetail extends ModelSummary {
  url: string;
  description: string;
  safe_serialization: boolean;
}

export interface LocalModel {
  model_id: string;
  size_bytes: number;
  files: number;
  last_accessed: number;
  last_modified: number;
  revisions: number;
}

export interface DownloadFile {
  filename: string;
  size_bytes: number;
  downloaded_bytes: number;
  status: string;
}

export interface DownloadJob {
  job_id: string;
  model_id: string;
  status: string;
  total_bytes: number;
  downloaded_bytes: number;
  progress: number;
  bytes_per_second: number;
  eta_seconds: number | null;
  started_at: number;
  completed_at: number | null;
  local_path: string | null;
  error: string | null;
  files: DownloadFile[];
}

export interface StorageInfo {
  capacity_bytes: number;
  free_bytes: number;
  used_bytes: number;
  model_cache_bytes: number;
}
