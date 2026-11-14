import { createHash, randomBytes } from 'node:crypto';
import { existsSync, mkdirSync, rmSync, unlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import {
  BrowserError,
  grantsEqual,
  identityKey,
  validateGrant,
  validateIdentity,
  type BrowserConfig,
  type BrowserGrant,
  type BrowserIdentity,
} from './models.js';
import { BrowserSecurityPolicy, originOf, redact } from './security.js';

export const INTERACTIONS = new Set([
  'click',
  'fill',
  'type',
  'press',
  'select',
  'check',
  'hover',
  'scroll',
]);

export const NAVIGATIONS = new Set(['navigate', 'back', 'forward', 'reload']);

export const READS = new Set([
  'status',
  'list_tabs',
  'snapshot',
  'screenshot',
  'extract_text',
  'get_links',
  'get_forms',
  'get_console',
  'get_page_errors',
  'get_network_errors',
  'wait_for',
]);

export const ALLOWED_HOST_OPS = new Set([
  ...NAVIGATIONS,
  'start',
  'status',
  'list_tabs',
  'new_tab',
  'select_tab',
  'close_tab',
  'close',
  'resize',
  'request_handoff',
]);

export const FIELDS: Record<string, Set<string>> = {
  start: new Set(['headless', 'width', 'height']),
  status: new Set(),
  close: new Set(),
  list_tabs: new Set(),
  new_tab: new Set(['url']),
  select_tab: new Set(['tab_id']),
  close_tab: new Set(['tab_id']),
  resize: new Set(['width', 'height']),
  navigate: new Set(['url', 'tab_id']),
  back: new Set(['tab_id']),
  forward: new Set(['tab_id']),
  reload: new Set(['tab_id']),
  wait_for: new Set(['tab_id', 'target', 'state', 'timeout_ms']),
  snapshot: new Set(['tab_id']),
  screenshot: new Set(['tab_id', 'full_page']),
  extract_text: new Set(['tab_id']),
  get_links: new Set(['tab_id']),
  get_forms: new Set(['tab_id']),
  get_console: new Set(['tab_id']),
  get_page_errors: new Set(['tab_id']),
  get_network_errors: new Set(['tab_id']),
  click: new Set(['tab_id', 'target', 'revision']),
  fill: new Set(['tab_id', 'target', 'revision', 'value']),
  type: new Set(['tab_id', 'target', 'revision', 'value']),
  press: new Set(['tab_id', 'target', 'revision', 'key']),
  select: new Set(['tab_id', 'target', 'revision', 'values']),
  check: new Set(['tab_id', 'target', 'revision', 'checked']),
  hover: new Set(['tab_id', 'target', 'revision']),
  scroll: new Set(['tab_id', 'revision', 'x', 'y']),
  request_handoff: new Set(),
  resume_after_handoff: new Set(),
  upload: new Set(),
  download: new Set(),
};

export const TARGET_KEYS = new Set(['ref', 'role', 'name', 'label', 'test_id', 'text', 'frame']);

export const SENSITIVE_RE =
  /password|passwd|one.?time|otp|mfa|verification.?code|security.?code|credit.?card|cc-/i;

export const TARGET_STATE_SCRIPT = `el => {
 const sensitive = e => /password|passwd|one.?time|otp|mfa|verification.?code|security.?code|credit.?card|cc-/i.test(
  [e.type,e.name,e.id,e.autocomplete,e.getAttribute('aria-label'),...Array.from(e.labels||[]).map(l=>l.textContent)].join(' '));
 const allFields = Array.from(el.closest('form')?.elements || []);
 const fields = allFields.slice(0, 100).map(e => ({
  name: String(e.name || e.id || e.getAttribute('aria-label') || '').slice(0,1000), type: e.type || e.tagName,
  value: sensitive(e) ? '[REDACTED]' : String(e.value||'').slice(0,10000), sensitive: sensitive(e),
  valueLength: String(e.value||'').length,
  selected: Array.from(e.selectedOptions||[]).slice(0,100).map(o=>String(o.value).slice(0,1000)),
  selectedCount: e.selectedOptions?.length || 0, checked: !!e.checked }));
 return {tag: el.tagName, html: el.outerHTML.slice(0,16000), type: el.type || '',
  name: String(el.name || '').slice(0,1000), autocomplete: el.autocomplete || '', label: String(el.getAttribute('aria-label') || '').slice(0,1000),
  sensitive: sensitive(el), value: sensitive(el) ? '[REDACTED]' : String(el.value||'').slice(0,10000),
  valueLength: String(el.value||'').length, formFieldCount: allFields.length,
  selected: Array.from(el.selectedOptions||[]).slice(0,100).map(o=>String(o.value).slice(0,1000)),
  selectedCount: el.selectedOptions?.length || 0,
  form: fields, formAction: el.closest('form')?.action || '', href: el.href || ''};
}`;

export const FORM_OBSERVATION_SCRIPT = `forms => forms.slice(0,50).map(f => ({
 method: f.method, action: f.action.slice(0,8192), fieldCount: f.elements.length,
 fields: Array.from(f.elements).slice(0,100).map(e=>({tag:e.tagName,type:e.type||'',
 name:(e.name||'').slice(0,256),label:(e.getAttribute('aria-label')||'').slice(0,512),required:!!e.required,disabled:!!e.disabled}))
}))`;

/**
 * Node Playwright evaluates string arguments as expressions and does not call
 * the resulting function (Python Playwright does). Wrap fixed host scripts in
 * a real function so the page receives the element/argument as intended.
 */
export function pageFunction(source: string): (arg?: unknown) => unknown {
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  return new Function('arg', `return (${source})(arg);`) as (arg?: unknown) => unknown;
}

export function canonicalJson(value: unknown): string {
  if (value === null || typeof value !== 'object') {
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) {
    return `[${value.map(item => canonicalJson(item)).join(',')}]`;
  }
  const obj = value as Record<string, unknown>;
  const keys = Object.keys(obj).sort();
  return `{${keys.map(k => `${JSON.stringify(k)}:${canonicalJson(obj[k])}`).join(',')}}`;
}

export function hashValue(value: unknown): string {
  return createHash('sha256').update(canonicalJson(value)).digest('hex');
}

export interface BrowserArtifact {
  artifact_id: string;
  owner_key: string;
  owner: BrowserIdentity;
  path: string;
  size: number;
}

/** Manages ephemeral screenshot PNG artifacts isolated per execution identity. */
export class BrowserArtifacts {
  private tempDir: string | null = null;
  readonly _artifacts = new Map<string, BrowserArtifact>();

  save(identity: BrowserIdentity, data: Uint8Array): BrowserArtifact {
    const owner = validateIdentity(identity);
    const ownerKey = identityKey(owner);
    if (!this.tempDir || !existsSync(this.tempDir)) {
      this.tempDir = join(tmpdir(), `firik-browser-${randomBytes(8).toString('hex')}`);
      mkdirSync(this.tempDir, { recursive: true, mode: 0o700 });
    }
    const artifact_id = `screenshot_${randomBytes(16).toString('hex')}`;
    const path = join(this.tempDir, `${artifact_id}.png`);
    writeFileSync(path, data, { flag: 'wx', mode: 0o600 });
    const artifact: BrowserArtifact = {
      artifact_id,
      owner_key: ownerKey,
      owner,
      path,
      size: data.byteLength,
    };
    this._artifacts.set(artifact_id, artifact);
    return artifact;
  }

  path(identity: BrowserIdentity, artifactId: string): string {
    const ownerKey = identityKey(validateIdentity(identity));
    const artifact = this._artifacts.get(artifactId);
    if (!artifact || artifact.owner_key !== ownerKey) {
      throw new BrowserError('ownership_denied', 'Screenshot does not belong to this execution identity.');
    }
    return artifact.path;
  }

  removeOwner(identity: BrowserIdentity): void {
    const ownerKey = identityKey(validateIdentity(identity));
    for (const [id, artifact] of [...this._artifacts.entries()]) {
      if (artifact.owner_key === ownerKey) {
        try {
          unlinkSync(artifact.path);
        } catch {
          // Ignore already removed files
        }
        this._artifacts.delete(id);
      }
    }
  }

  close(): void {
    this._artifacts.clear();
    if (this.tempDir) {
      try {
        rmSync(this.tempDir, { recursive: true, force: true });
      } catch {
        // Ignore cleanup errors
      }
      this.tempDir = null;
    }
  }
}

export interface PreparedAction {
  identity_key: string;
  grant_digest: string;
  identity: BrowserIdentity;
  operation: string;
  args_digest: string;
  state_digest: string;
  tab_id: string;
  expires: number;
}

/** Validates and normalizes browser operation arguments across all executors. */
export function normalizeBrowserArguments(
  operation: string,
  rawArgs: Record<string, unknown> | null | undefined,
  actionTimeoutMs: number,
): [string, Record<string, unknown>] {
  const op = operation.replace(/^browser_/, '');
  if (!Object.hasOwn(FIELDS, op)) {
    throw new BrowserError('unsupported_capability', 'Unknown browser operation.');
  }
  const args = rawArgs ?? {};
  if (typeof args !== 'object' || Array.isArray(args)) {
    throw new BrowserError('invalid_tool_arguments', 'Browser arguments contain unsupported fields.');
  }
  const allowedFields = FIELDS[op];
  for (const key of Object.keys(args)) {
    if (!allowedFields.has(key)) {
      throw new BrowserError('invalid_tool_arguments', 'Browser arguments contain unsupported fields.');
    }
  }
  if ('tab_id' in args && (typeof args.tab_id !== 'string' || args.tab_id.length > 100)) {
    throw new BrowserError('invalid_tool_arguments', 'Invalid tab identifier.');
  }
  if (op === 'navigate' && typeof args.url !== 'string') {
    throw new BrowserError('invalid_tool_arguments', 'A URL is required.');
  }
  if (op === 'new_tab' && 'url' in args && typeof args.url !== 'string') {
    throw new BrowserError('invalid_tool_arguments', 'Invalid URL.');
  }
  if (INTERACTIONS.has(op)) {
    if (typeof args.revision !== 'number' || !Number.isInteger(args.revision) || args.revision < 1) {
      throw new BrowserError('invalid_tool_arguments', 'Interactions require a current snapshot revision.');
    }
    if (op !== 'scroll' && (!args.target || typeof args.target !== 'object' || Array.isArray(args.target))) {
      throw new BrowserError('invalid_tool_arguments', 'An explicit semantic target is required.');
    }
  }
  if ('target' in args) {
    const target = args.target as Record<string, unknown>;
    if (!target || typeof target !== 'object' || Array.isArray(target) || Object.keys(target).length === 0) {
      throw new BrowserError('invalid_tool_arguments', 'Invalid semantic target.');
    }
    for (const [k, v] of Object.entries(target)) {
      if (!TARGET_KEYS.has(k)) {
        throw new BrowserError('invalid_tool_arguments', 'Invalid semantic target.');
      }
      if (typeof v !== 'string' || v.length === 0 || v.length > 1000) {
        throw new BrowserError('invalid_tool_arguments', 'Target fields must be bounded text.');
      }
    }
    const selectors = Object.keys(target).filter(k =>
      ['ref', 'role', 'label', 'test_id', 'text'].includes(k),
    );
    if (selectors.length !== 1 || ('name' in target && !('role' in target))) {
      throw new BrowserError('invalid_tool_arguments', 'Specify exactly one semantic locator strategy.');
    }
  }
  if ((op === 'fill' || op === 'type') && (typeof args.value !== 'string' || args.value.length > 10000)) {
    throw new BrowserError('invalid_tool_arguments', 'Input value must be text of at most 10000 characters.');
  }
  if (op === 'press' && (typeof args.key !== 'string' || args.key.length === 0 || args.key.length > 80)) {
    throw new BrowserError('invalid_tool_arguments', 'Invalid key.');
  }
  if (
    op === 'select' &&
    (!Array.isArray(args.values) ||
      args.values.length < 1 ||
      args.values.length > 50 ||
      args.values.some(v => typeof v !== 'string' || v.length > 1000))
  ) {
    throw new BrowserError('invalid_tool_arguments', 'Select requires a bounded list of values.');
  }
  for (const boolField of ['headless', 'full_page', 'checked']) {
    if (boolField in args && typeof args[boolField] !== 'boolean') {
      throw new BrowserError('invalid_tool_arguments', `${boolField} must be boolean.`);
    }
  }
  const bounds: [string, number, number][] = [
    ['width', 320, 2560],
    ['height', 240, 1440],
    ['x', -2000, 2000],
    ['y', -2000, 2000],
    ['timeout_ms', 100, actionTimeoutMs],
  ];
  for (const [field, min, max] of bounds) {
    if (field in args) {
      const val = args[field];
      if (typeof val !== 'number' || !Number.isInteger(val) || val < min || val > max) {
        throw new BrowserError('invalid_tool_arguments', `${field} is outside the supported bounds.`);
      }
    }
  }
  if (op === 'resize' && (typeof args.width !== 'number' || typeof args.height !== 'number')) {
    throw new BrowserError('invalid_tool_arguments', 'Resize requires integer width and height.');
  }
  if ('state' in args && !['visible', 'hidden', 'attached', 'detached'].includes(String(args.state))) {
    throw new BrowserError('invalid_tool_arguments', 'Unsupported wait state.');
  }
  return [op, { ...args }];
}

/** Validates grant + host config eligibility before dispatching a browser command. */
export function assertBrowserEnabled(config: BrowserConfig, grant: BrowserGrant): void {
  if (!config.enabled || !grant.enabled) {
    throw new BrowserError('permission_denied', 'Browser is disabled for this host or task.');
  }
  if (config.strict_network_isolation) {
    throw new BrowserError(
      'network_isolation_unavailable',
      'Strict network isolation requires an enforceable egress boundary; host Chromium is unavailable in this mode.',
    );
  }
}

/** Truncates list observation outputs symmetrically against `max_snapshot_chars`. */
export function boundedDiagnosticItems(
  items: unknown[],
  key: string,
  maxSnapshotChars: number,
): Record<string, unknown> {
  const selected: unknown[] = [];
  let used = 0;
  for (const item of items) {
    const size = JSON.stringify(item).length + 2;
    if (used + size > maxSnapshotChars) {
      break;
    }
    selected.push(item);
    used += size;
  }
  const omitted = items.length - selected.length;
  return {
    [key]: selected,
    truncated: omitted > 0,
    omitted_entries: omitted,
    untrusted: true,
  };
}

/**
 * Validates target and surrounding form metadata against sensitivity and size
 * guardrails, checking outbound link/form targets against security policy.
 */
export async function validateTargetState(
  state: Record<string, unknown>,
  target: unknown,
  policy: BrowserSecurityPolicy,
  grant: BrowserGrant,
  maxSnapshotChars: number,
): Promise<Record<string, unknown>> {
  const formFields = Array.isArray(state.form) ? (state.form as Record<string, unknown>[]) : [];
  const totalFormChars = formFields.reduce((acc, field) => {
    const nameLen = String(field.name ?? '').length;
    const valLen = String(field.value ?? '').length;
    const selLen = Array.isArray(field.selected)
      ? field.selected.reduce((s: number, v: unknown) => s + String(v).length, 0)
      : 0;
    return acc + nameLen + valLen + selLen;
  }, 0);
  if (
    Number(state.formFieldCount ?? 0) > 100 ||
    Number(state.valueLength ?? 0) > 10000 ||
    Number(state.selectedCount ?? 0) > 100 ||
    formFields.some(f => Number(f.valueLength ?? 0) > 10000 || Number(f.selectedCount ?? 0) > 100) ||
    totalFormChars > maxSnapshotChars
  ) {
    throw new BrowserError('form_limit', 'The complete form exceeds review limits; use manual browser control.');
  }
  const combinedLabels = ['type', 'name', 'autocomplete', 'label']
    .map(k => String(state[k] ?? ''))
    .join(' ');
  if (
    Boolean(state.sensitive) ||
    SENSITIVE_RE.test(combinedLabels) ||
    formFields.some(f => Boolean(f.sensitive))
  ) {
    throw new BrowserError(
      'authentication_handoff_required',
      'Credentials, MFA and payment details require manual browser control.',
    );
  }
  if (state.href && typeof state.href === 'string') {
    await policy.validate(state.href, grant);
  }
  if (state.formAction && typeof state.formAction === 'string') {
    await policy.validate(state.formAction, grant);
  }
  return {
    target,
    form: formFields.map(f => ({
      name: redact(f.name),
      type: f.type,
      value: redact(f.value, 10000),
      checked: Boolean(f.checked),
      selected: Array.isArray(f.selected) ? f.selected.map(v => redact(v)) : [],
    })),
  };
}

/** Shared single-use bound approval & grant provider registry for browser executors. */
export class BrowserGrantGovernance {
  readonly prepared = new Map<string, PreparedAction>();
  readonly grantProviders = new Map<string, () => BrowserGrant>();

  bindGrant(identity: BrowserIdentity, grant: BrowserGrant): void {
    const key = identityKey(validateIdentity(identity));
    const checked = validateGrant(grant, originOf);
    const previous = this.grantProviders.get(key)?.();
    if (previous && !grantsEqual(previous, checked)) {
      this.clearOwnerApprovals(key);
    }
    this.grantProviders.set(key, () => checked);
  }

  bindGrantProvider(identity: BrowserIdentity, provider: () => BrowserGrant): void {
    if (typeof provider !== 'function') {
      throw new TypeError('Browser grant provider must be callable.');
    }
    const key = identityKey(validateIdentity(identity));
    this.grantProviders.set(key, provider);
  }

  liveGrant(identity: BrowserIdentity, fallback: BrowserGrant): BrowserGrant {
    const key = identityKey(validateIdentity(identity));
    const provider = this.grantProviders.get(key);
    if (!provider) {
      return validateGrant(fallback, originOf);
    }
    try {
      return validateGrant(provider(), originOf);
    } catch {
      return validateGrant({ enabled: false }, originOf);
    }
  }

  issueApproval(params: {
    identityKey: string;
    grant: BrowserGrant;
    identity: BrowserIdentity;
    operation: string;
    args: Record<string, unknown>;
    stateDigest: string;
    tabId: string;
    ttlSeconds: number;
  }): string {
    const now = performance.now() / 1000;
    for (const [k, v] of [...this.prepared.entries()]) {
      if (v.expires <= now) {
        this.prepared.delete(k);
      }
    }
    if (this.prepared.size >= 128) {
      throw new BrowserError('browser_limit', 'Too many pending browser approvals.');
    }
    const token = randomBytes(24).toString('base64url');
    this.prepared.set(token, {
      identity_key: params.identityKey,
      grant_digest: hashValue(params.grant),
      identity: params.identity,
      operation: params.operation,
      args_digest: hashValue(params.args),
      state_digest: params.stateDigest,
      tab_id: params.tabId,
      expires: now + params.ttlSeconds,
    });
    return token;
  }

  consumeApproval(params: {
    approvedFingerprint: string | null | undefined;
    identityKey: string;
    grant: BrowserGrant;
    operation: string;
    args: Record<string, unknown>;
    tabId: string;
  }): PreparedAction {
    const prepared = params.approvedFingerprint ? this.prepared.get(params.approvedFingerprint) : undefined;
    if (params.approvedFingerprint) {
      this.prepared.delete(params.approvedFingerprint);
    }
    if (
      !prepared ||
      prepared.identity_key !== params.identityKey ||
      prepared.grant_digest !== hashValue(params.grant) ||
      prepared.operation !== params.operation ||
      prepared.args_digest !== hashValue(params.args) ||
      prepared.tab_id !== params.tabId
    ) {
      throw new BrowserError(
        'approval_required',
        'This exact browser action requires fresh host approval.',
      );
    }
    if (prepared.expires <= performance.now() / 1000) {
      throw new BrowserError('approval_expired', 'Browser approval expired before execution.');
    }
    return prepared;
  }

  clearOwnerApprovals(ownerKey: string): void {
    for (const [token, prep] of [...this.prepared.entries()]) {
      if (prep.identity_key === ownerKey) {
        this.prepared.delete(token);
      }
    }
  }

  removeOwner(ownerKey: string): void {
    this.clearOwnerApprovals(ownerKey);
    this.grantProviders.delete(ownerKey);
  }

  clearAll(): void {
    this.prepared.clear();
    this.grantProviders.clear();
  }
}

const HOST_INPUT_FIELDS: Record<string, Set<string>> = {
  click: new Set(['x', 'y', 'x_ratio', 'y_ratio']),
  scroll: new Set(['delta_x', 'delta_y']),
  key: new Set(['key']),
  text: new Set(['text']),
};

export function assertValidHostInputShape(kind: string, rawArgs?: Record<string, unknown> | null): Record<string, unknown> {
  const args = rawArgs ?? {};
  if (
    !Object.hasOwn(HOST_INPUT_FIELDS, kind) ||
    typeof args !== 'object' ||
    Array.isArray(args) ||
    Object.keys(args).some(k => !HOST_INPUT_FIELDS[kind].has(k))
  ) {
    throw new BrowserError('invalid_tool_arguments', 'Unsupported browser user input.');
  }
  return args;
}

/** Validates host_input parameters consistently across executors. */
export function validateHostInputArgs(
  kind: string,
  args: Record<string, unknown>,
  viewport: { width: number; height: number },
): { kind: 'click'; x: number; y: number } | { kind: 'scroll'; deltaX: number; deltaY: number } | { kind: 'key'; key: string } | { kind: 'text'; text: string } {
  assertValidHostInputShape(kind, args);
  if (kind === 'click') {
    const ratioMode = 'x_ratio' in args || 'y_ratio' in args;
    if (ratioMode && ('x' in args || 'y' in args)) {
      throw new BrowserError('invalid_tool_arguments', 'Use either pixel or ratio click coordinates.');
    }
    let x = args.x;
    let y = args.y;
    if (ratioMode) {
      const xr = args.x_ratio;
      const yr = args.y_ratio;
      if (
        typeof xr !== 'number' ||
        typeof yr !== 'number' ||
        !Number.isFinite(xr) ||
        !Number.isFinite(yr) ||
        xr < 0 ||
        xr > 1 ||
        yr < 0 ||
        yr > 1
      ) {
        throw new BrowserError('invalid_tool_arguments', 'Click is outside the browser view.');
      }
      x = Math.min(viewport.width - 1, Math.floor(xr * viewport.width));
      y = Math.min(viewport.height - 1, Math.floor(yr * viewport.height));
    }
    if (
      typeof x !== 'number' ||
      !Number.isInteger(x) ||
      x < 0 ||
      x >= viewport.width ||
      typeof y !== 'number' ||
      !Number.isInteger(y) ||
      y < 0 ||
      y >= viewport.height
    ) {
      throw new BrowserError('invalid_tool_arguments', 'Click is outside the browser view.');
    }
    return { kind: 'click', x, y };
  }
  if (kind === 'scroll') {
    if (
      !('delta_y' in args) ||
      Object.values(args).some(
        v => typeof v !== 'number' || !Number.isInteger(v) || v < -2000 || v > 2000,
      )
    ) {
      throw new BrowserError('invalid_tool_arguments', 'Scroll exceeds the supported bounds.');
    }
    return {
      kind: 'scroll',
      deltaX: (args.delta_x as number | undefined) ?? 0,
      deltaY: args.delta_y as number,
    };
  }
  if (kind === 'key') {
    if (typeof args.key !== 'string' || args.key.length < 1 || args.key.length > 80) {
      throw new BrowserError('invalid_tool_arguments', 'Invalid browser key.');
    }
    return { kind: 'key', key: args.key };
  }
  if (typeof args.text !== 'string' || args.text.length > 10000) {
    throw new BrowserError('invalid_tool_arguments', 'Browser input must be bounded text.');
  }
  return { kind: 'text', text: args.text };
}

export interface BrowserPointerPoint {
  x: number;
  y: number;
}

export const POINTER_ACTION_LABELS: Record<string, string> = {
  move: 'Gidiyor',
  hover: 'Üzerinde',
  click: 'Tıklıyor',
  fill: 'Dolduruyor',
  type: 'Yazıyor',
  select: 'Seçiyor',
  check: 'İşaretliyor',
  scroll: 'Kaydırıyor',
};

/**
 * Generates an ease-in-out quadratic Bezier trajectory between two viewport
 * coordinates so pointer motion is naturally visible to the user.
 */
export function computePointerTrajectory(
  from: BrowserPointerPoint,
  to: BrowserPointerPoint,
  options?: { minSteps?: number; maxSteps?: number },
): BrowserPointerPoint[] {
  const targetX = Math.round(to.x);
  const targetY = Math.round(to.y);
  const startX = Math.round(from.x);
  const startY = Math.round(from.y);
  const dx = targetX - startX;
  const dy = targetY - startY;
  const dist = Math.hypot(dx, dy);
  if (dist < 2) {
    return [{ x: targetX, y: targetY }];
  }

  const minSteps = Math.max(2, options?.minSteps ?? 8);
  const maxSteps = Math.max(minSteps, options?.maxSteps ?? 18);
  const steps = Math.min(maxSteps, Math.max(minSteps, Math.round(dist / 36) + minSteps - 2));

  // Subtle perpendicular curve offset proportional to distance, capped so paths stay inside the target zone
  const curveMag = Math.min(26, dist * 0.12) * (dx >= 0 ? 1 : -1);
  const nx = -dy / dist;
  const ny = dx / dist;
  const ctrlX = (startX + targetX) / 2 + nx * curveMag;
  const ctrlY = (startY + targetY) / 2 + ny * curveMag;

  const points: BrowserPointerPoint[] = [];
  for (let i = 1; i <= steps; i++) {
    if (i === steps) {
      points.push({ x: targetX, y: targetY });
      break;
    }
    const rawT = i / steps;
    const t = rawT < 0.5 ? 4 * rawT * rawT * rawT : 1 - Math.pow(-2 * rawT + 2, 3) / 2;
    const oneMinusT = 1 - t;
    const px = Math.round(oneMinusT * oneMinusT * startX + 2 * oneMinusT * t * ctrlX + t * t * targetX);
    const py = Math.round(oneMinusT * oneMinusT * startY + 2 * oneMinusT * t * ctrlY + t * t * targetY);
    points.push({ x: px, y: py });
  }
  return points;
}
