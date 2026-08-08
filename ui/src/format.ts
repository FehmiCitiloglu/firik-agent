export function formatBytes(value: number | null | undefined, digits = 1): string {
  if (value == null) return "—";
  if (value === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
  return `${(value / 1024 ** index).toFixed(index === 0 ? 0 : digits)} ${units[index]}`;
}

export function formatCount(value: number): string {
  return new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 }).format(value);
}

export function formatParameters(value: number | null | undefined): string {
  if (!value) return "—";
  return `${(value / 1_000_000_000).toFixed(value >= 10_000_000_000 ? 0 : 2)}B`;
}

export function formatDuration(value: number | null): string {
  if (value == null || !Number.isFinite(value)) return "Calculating";
  if (value < 60) return `${Math.max(1, Math.round(value))}s remaining`;
  return `${Math.ceil(value / 60)}m remaining`;
}

export function shortName(modelId: string): string {
  return modelId.split("/", 2)[1] ?? modelId;
}
