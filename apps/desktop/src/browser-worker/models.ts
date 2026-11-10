import { randomBytes } from 'node:crypto';

export class BrowserError extends Error {
  readonly code: string;
  readonly dispatched: boolean;
  readonly requires_reconciliation: boolean;

  constructor(
    code: string,
    message: string,
    options?: { dispatched?: boolean; requires_reconciliation?: boolean },
  ) {
    super(message);
    this.name = 'BrowserError';
    this.code = code;
    this.dispatched = Boolean(options?.dispatched);
    this.requires_reconciliation = Boolean(options?.requires_reconciliation);
  }
}

export interface BrowserIdentity {
  readonly conversation_id: string;
  readonly project_id: string;
  readonly run_id: string;
  readonly actor_id: string;
  readonly parent_agent_id: string;
}

export interface BrowserGrant {
  readonly enabled: boolean;
  readonly origins: readonly string[];
  readonly allow_private_origins: readonly string[];
  readonly allow_screenshots: boolean;
  readonly allow_uploads: boolean;
  readonly allow_downloads: boolean;
  readonly allow_public_https: boolean;
}

export interface BrowserConfig {
  enabled: boolean;
  headless: boolean;
  strict_network_isolation: boolean;
  max_sessions: number;
  max_tabs_per_session: number;
  max_actions_per_task: number;
  action_timeout_ms: number;
  navigation_timeout_ms: number;
  launch_timeout_ms: number;
  max_snapshot_chars: number;
  max_diagnostics: number;
  max_screenshot_bytes: number;
  approval_ttl_seconds: number;
  viewport_width: number;
  viewport_height: number;
  locale: string;
  timezone_id: string;
}

export function defaultBrowserConfig(overrides?: Partial<BrowserConfig>): BrowserConfig {
  const cfg: BrowserConfig = {
    enabled: false,
    headless: true,
    strict_network_isolation: false,
    max_sessions: 3,
    max_tabs_per_session: 8,
    max_actions_per_task: 100,
    action_timeout_ms: 15000,
    navigation_timeout_ms: 30000,
    launch_timeout_ms: 30000,
    max_snapshot_chars: 16000,
    max_diagnostics: 100,
    max_screenshot_bytes: 8_000_000,
    approval_ttl_seconds: 60,
    viewport_width: 1280,
    viewport_height: 800,
    locale: 'en-US',
    timezone_id: 'UTC',
  };
  if (overrides) {
    for (const key of Object.keys(overrides) as (keyof BrowserConfig)[]) {
      if (!Object.hasOwn(cfg, key)) {
        throw new Error(`Unknown BrowserConfig field: ${String(key)}`);
      }
      const value = overrides[key];
      if (value !== undefined) {
        if (typeof value !== typeof cfg[key]) {
          throw new Error(`Invalid BrowserConfig type for ${String(key)}`);
        }
        (cfg as unknown as Record<string, unknown>)[key] = value;
      }
    }
  }
  return cfg;
}

export function validateIdentity(input: Partial<BrowserIdentity> & { conversation_id: unknown }): BrowserIdentity {
  const raw = {
    conversation_id: input?.conversation_id,
    project_id: input?.project_id ?? '',
    run_id: input?.run_id ?? '',
    actor_id: input?.actor_id ?? 'parent',
    parent_agent_id: input?.parent_agent_id ?? '',
  };
  for (const key of ['conversation_id', 'project_id', 'run_id', 'actor_id', 'parent_agent_id'] as const) {
    const value = raw[key];
    if (typeof value !== 'string' || value.length > 256 || [...value].some(ch => ch.charCodeAt(0) < 32)) {
      throw new Error('Browser identity fields must be bounded text without control characters.');
    }
  }
  if (!raw.conversation_id || !raw.actor_id) {
    throw new Error('Browser identity requires a conversation and actor.');
  }
  return Object.freeze(raw as BrowserIdentity);
}

export function identityKey(identity: BrowserIdentity): string {
  const checked = validateIdentity(identity);
  return JSON.stringify([
    checked.conversation_id,
    checked.project_id,
    checked.run_id,
    checked.actor_id,
    checked.parent_agent_id,
  ]);
}

export function validateGrant(
  input: Partial<BrowserGrant> | undefined,
  canonicalizeOrigin: (url: string) => string,
): BrowserGrant {
  const raw = {
    enabled: input?.enabled ?? false,
    origins: input?.origins ?? [],
    allow_private_origins: input?.allow_private_origins ?? [],
    allow_screenshots: input?.allow_screenshots ?? false,
    allow_uploads: input?.allow_uploads ?? false,
    allow_downloads: input?.allow_downloads ?? false,
    allow_public_https: input?.allow_public_https ?? false,
  };
  for (const flag of ['enabled', 'allow_screenshots', 'allow_uploads', 'allow_downloads', 'allow_public_https'] as const) {
    if (typeof raw[flag] !== 'boolean') {
      throw new Error('Browser grant flags must be booleans.');
    }
  }
  const normalizeList = (values: unknown): readonly string[] => {
    if (!Array.isArray(values) || values.length > 32) {
      throw new Error('Browser origins require a tuple with at most 32 entries.');
    }
    const normalized: string[] = [];
    for (const value of values) {
      if (typeof value !== 'string' || value.length > 2048) {
        throw new Error('Browser origins must be bounded URL strings.');
      }
      let parsed: URL;
      try {
        parsed = new URL(value);
      } catch {
        throw new Error('Invalid browser origin grant.');
      }
      if ((parsed.pathname !== '' && parsed.pathname !== '/') || parsed.search || parsed.hash) {
        throw new Error('Browser grants require origins, without paths, queries or fragments.');
      }
      let canonical: string;
      try {
        canonical = canonicalizeOrigin(value);
      } catch {
        throw new Error('Invalid browser origin grant.');
      }
      if (!normalized.includes(canonical)) {
        normalized.push(canonical);
      }
    }
    return Object.freeze(normalized);
  };
  const origins = normalizeList(raw.origins);
  const allow_private_origins = normalizeList(raw.allow_private_origins);
  const originSet = new Set(origins);
  if (allow_private_origins.some(origin => !originSet.has(origin))) {
    throw new Error('Private browser origins must be a subset of authorized origins.');
  }
  return Object.freeze({
    enabled: raw.enabled,
    origins,
    allow_private_origins,
    allow_screenshots: raw.allow_screenshots,
    allow_uploads: raw.allow_uploads,
    allow_downloads: raw.allow_downloads,
    allow_public_https: raw.allow_public_https,
  });
}

export function grantsEqual(a: BrowserGrant, b: BrowserGrant): boolean {
  return (
    a.enabled === b.enabled &&
    a.allow_screenshots === b.allow_screenshots &&
    a.allow_uploads === b.allow_uploads &&
    a.allow_downloads === b.allow_downloads &&
    a.allow_public_https === b.allow_public_https &&
    a.origins.length === b.origins.length &&
    a.origins.every((v, i) => v === b.origins[i]) &&
    a.allow_private_origins.length === b.allow_private_origins.length &&
    a.allow_private_origins.every((v, i) => v === b.allow_private_origins[i])
  );
}

export class DiagnosticBuffer<T = Record<string, unknown>> {
  readonly maxlen: number;
  dropped = 0;
  private items: T[] = [];

  constructor(maxlen = 100) {
    this.maxlen = Math.max(1, maxlen);
  }

  append(value: T): void {
    if (this.items.length >= this.maxlen) {
      this.items.shift();
      this.dropped += 1;
    }
    this.items.push(value);
  }

  clear(): void {
    this.items = [];
    this.dropped = 0;
  }

  toArray(): T[] {
    return [...this.items];
  }

  get length(): number {
    return this.items.length;
  }
}

export class AsyncMutex {
  private queue: (() => void)[] = [];
  private locked = false;

  async acquire(): Promise<void> {
    if (!this.locked) {
      this.locked = true;
      return;
    }
    await new Promise<void>(resolve => this.queue.push(resolve));
  }

  release(): void {
    const next = this.queue.shift();
    if (next) {
      next();
    } else {
      this.locked = false;
    }
  }

  async run<T>(fn: () => Promise<T>): Promise<T> {
    await this.acquire();
    try {
      return await fn();
    } finally {
      this.release();
    }
  }
}

export function randomId(prefix: string): string {
  return `${prefix}_${randomBytes(8).toString('hex')}`;
}
