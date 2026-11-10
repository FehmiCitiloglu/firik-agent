import { mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { randomBytes } from 'node:crypto';
import { MessageChannel, Worker } from 'node:worker_threads';
import {
  BrowserError,
  INTERACTIONS,
  NAVIGATIONS,
  WorkerRequestDispatcher,
  defaultBrowserConfig,
  identityKey,
  grantsEqual,
  originOf,
  validateGrant,
  validateIdentity,
  type BrowserConfig,
  type BrowserGrant,
  type BrowserIdentity,
  type WorkerEnvelope,
  type WorkerRequestPayload,
  type WorkerResponseEnvelope,
} from '../browser-worker/index.js';

interface PendingWorkerCall {
  dispatched: boolean;
  resolve: (res: WorkerResponseEnvelope) => void;
  reject: (err: BrowserError) => void;
  timer: NodeJS.Timeout;
}

export interface BrowserHostOptions {
  workerPath?: string;
  browsersPath?: string;
  inProcess?: boolean;
  dispatcher?: WorkerRequestDispatcher;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  utilityProcess?: any;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  MessageChannelMain?: any;
}

export class BrowserHost {
  private readonly options: BrowserHostOptions;
  private nextId = 0;
  private pending = new Map<number, PendingWorkerCall>();
  private seenExecutionIds = new Set<string>();
  private conversationGrants = new Map<string, BrowserGrant>();
  private activeIdentities = new Map<string, BrowserIdentity>();
  private inProcessDispatcher: WorkerRequestDispatcher | null = null;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  private workerProcess: any = null;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  private port: any = null;
  private tempDir: string | null = null;
  private stopping = false;
  private failed = false;
  private shutdownPromise: Promise<void> | null = null;
  private grantEpochs = new Map<string, number>();
  private config = defaultBrowserConfig({ enabled: true });

  constructor(options: BrowserHostOptions = {}) {
    this.options = options;
    if (options.inProcess || options.dispatcher) {
      this.inProcessDispatcher = options.dispatcher ?? new WorkerRequestDispatcher();
    }
  }

  private ensureWorker(): void {
    if (this.failed) throw new BrowserError('browser_worker_unavailable', 'Browser worker disconnected. Restart the desktop app before continuing.');
    if (this.inProcessDispatcher || this.port) {
      return;
    }
    const workerPath = this.options.workerPath;
    if (!workerPath) {
      throw new BrowserError('browser_worker_unavailable', 'Browser worker entrypoint is missing.');
    }
    if (this.options.utilityProcess && this.options.MessageChannelMain) {
      const { port1, port2 } = new this.options.MessageChannelMain();
      const child = this.options.utilityProcess.fork(workerPath, [], {
        stdio: 'pipe',
        serviceName: 'Firik Browser Worker',
        env: { ...process.env, ...(this.options.browsersPath ? { PLAYWRIGHT_BROWSERS_PATH: this.options.browsersPath } : {}) },
      });
      child.stderr?.on('data', () => {});
      child.stdout?.on('data', () => {});
      child.on('exit', () => this.onWorkerExit());
      child.postMessage({ type: 'init' }, [port2]);
      port1.on('message', (event: { data?: unknown }) => {
        this.onWorkerMessage((event?.data ?? event) as WorkerResponseEnvelope);
      });
      port1.start();
      this.workerProcess = child;
      this.port = port1;
      return;
    }
    const { port1, port2 } = new MessageChannel();
    const worker = new Worker(workerPath, { env: { ...process.env,
      ...(this.options.browsersPath ? { PLAYWRIGHT_BROWSERS_PATH: this.options.browsersPath } : {}) } });
    worker.on('exit', () => this.onWorkerExit());
    worker.on('error', () => this.onWorkerExit());
    worker.postMessage({ type: 'init_port', port: port2 }, [port2]);
    port1.on('message', (data: unknown) => {
      this.onWorkerMessage(data as WorkerResponseEnvelope);
    });
    port1.start();
    this.workerProcess = worker;
    this.port = port1;
  }

  private onWorkerMessage(response: WorkerResponseEnvelope): void {
    if (!response || typeof response.id !== 'number') return;
    const entry = this.pending.get(response.id);
    if (!entry) return;
    this.pending.delete(response.id);
    clearTimeout(entry.timer);
    if (response.ok) {
      entry.resolve(response);
    } else {
      const err = response.error;
      entry.reject(
        new BrowserError(
          err?.code ?? 'browser_error',
          err?.message ?? 'Browser operation failed.',
          {
            dispatched: Boolean(err?.dispatched),
            requires_reconciliation: Boolean(err?.requires_reconciliation),
          },
        ),
      );
    }
  }

  private onWorkerExit(): void {
    this.failed = true;
    this.conversationGrants.clear();
    this.activeIdentities.clear();
    this.port = null;
    this.workerProcess = null;
    for (const [id, entry] of [...this.pending.entries()]) {
      this.pending.delete(id);
      clearTimeout(entry.timer);
      if (this.stopping && !entry.dispatched) {
        entry.reject(new BrowserError('cancelled', 'Browser host is shutting down.'));
      } else {
        entry.reject(
          new BrowserError(
            entry.dispatched ? 'tool_outcome_unknown' : 'browser_operation_failed',
            entry.dispatched
              ? 'Browser worker exited during action; inspect the page before repeating.'
              : 'Browser worker disconnected before completing observation.',
            {
              dispatched: entry.dispatched,
              requires_reconciliation: entry.dispatched,
            },
          ),
        );
      }
    }
  }

  async sendToWorker(
    payload: WorkerRequestPayload,
    options?: { dispatched?: boolean; timeoutMs?: number },
  ): Promise<WorkerResponseEnvelope> {
    if (this.stopping && payload.type !== 'close_all') {
      throw new BrowserError('cancelled', 'Browser host is shutting down.');
    }
    if (this.pending.size >= 64) throw new BrowserError('browser_limit', 'Browser request queue is full.');
    // Cleanup may still use a failed worker's existing port. Every new
    // operation fails closed after loss of acknowledgement.
    if (payload.type !== 'close_all' || !this.port) this.ensureWorker();
    const id = ++this.nextId;
    const envelope: WorkerEnvelope = { id, payload };
    if (this.inProcessDispatcher) {
      const response = await this.inProcessDispatcher.handle(envelope);
      if (!response.ok) {
        const err = response.error!;
        throw new BrowserError(err.code, err.message, {
          dispatched: err.dispatched,
          requires_reconciliation: err.requires_reconciliation,
        });
      }
      return response;
    }
    const dispatched = Boolean(options?.dispatched);
    const timeoutMs = options?.timeoutMs ?? Math.max(45000,
      this.config.action_timeout_ms + 10000, this.config.navigation_timeout_ms + 10000,
      this.config.launch_timeout_ms + 10000);
    return new Promise<WorkerResponseEnvelope>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(
          new BrowserError(
            dispatched ? 'tool_outcome_unknown' : 'browser_operation_failed',
            dispatched
              ? 'Browser worker timed out; outcome is unknown and was not retried.'
              : 'Browser worker observation timed out.',
            { dispatched, requires_reconciliation: dispatched },
          ),
        );
        this.failed = true;
        void this.shutdown();
      }, timeoutMs);
      this.pending.set(id, { dispatched, resolve, reject, timer });
      try {
        this.port.postMessage(envelope);
      } catch {
        clearTimeout(timer);
        this.pending.delete(id);
        reject(new BrowserError('browser_operation_failed', 'Browser worker transport is closed.'));
        this.failed = true;
        void this.shutdown();
      }
    });
  }

  private recordIdentityGrant(identity: BrowserIdentity, grant: BrowserGrant): void {
    const previous = this.conversationGrants.get(identity.conversation_id);
    const owner = this.activeIdentities.get(identity.conversation_id);
    if (!previous || !grantsEqual(previous, grant) || (owner && identityKey(owner) !== identityKey(identity))) {
      this.grantEpochs.set(identity.conversation_id, (this.grantEpochs.get(identity.conversation_id) ?? 0) + 1);
    }
    this.conversationGrants.set(identity.conversation_id, grant);
    if (grant.enabled) {
      this.activeIdentities.set(identity.conversation_id, identity);
    } else {
      this.activeIdentities.delete(identity.conversation_id);
    }
  }

  async configure(config: Partial<BrowserConfig>): Promise<{ ok: true }> {
    const checked = defaultBrowserConfig(config);
    await this.sendToWorker({ type: 'configure', config: checked });
    this.config = checked;
    return { ok: true };
  }

  private saveEphemeralBuffer(data: Uint8Array, prefix = 'preview'): string {
    if (!this.tempDir) {
      this.tempDir = join(tmpdir(), `firik-browser-host-${randomBytes(8).toString('hex')}`);
      mkdirSync(this.tempDir, { recursive: true, mode: 0o700 });
    }
    const file = join(this.tempDir, `${prefix}_${randomBytes(12).toString('hex')}.png`);
    writeFileSync(file, data, { flag: 'wx', mode: 0o600 });
    return file;
  }

  async handlePrepare(params: {
    identity: BrowserIdentity;
    grant: BrowserGrant;
    operation: string;
    arguments: Record<string, unknown>;
  }): Promise<Record<string, unknown>> {
    const identity = validateIdentity(params.identity);
    const grant = validateGrant(params.grant, originOf);
    this.recordIdentityGrant(identity, grant);
    const response = await this.sendToWorker({
      type: 'prepare',
      identity,
      grant,
      operation: params.operation,
      arguments: params.arguments,
    });
    return (response.result ?? {}) as Record<string, unknown>;
  }

  async handleExecute(params: {
    execution_id: string;
    identity: BrowserIdentity;
    grant: BrowserGrant;
    operation: string;
    arguments: Record<string, unknown>;
    approved_fingerprint?: string | null;
    host_control?: boolean;
  }): Promise<Record<string, unknown>> {
    if (!params.execution_id || typeof params.execution_id !== 'string') {
      throw new BrowserError('invalid_tool_arguments', 'Missing execution_id.');
    }
    if (this.seenExecutionIds.has(params.execution_id)) {
      throw new BrowserError(
        'tool_outcome_unknown',
        'Duplicate browser execution ID rejected to prevent replay.',
        { dispatched: true, requires_reconciliation: true },
      );
    }
    this.seenExecutionIds.add(params.execution_id);
    if (this.seenExecutionIds.size > 2048) {
      const oldest = this.seenExecutionIds.values().next().value;
      if (oldest) this.seenExecutionIds.delete(oldest);
    }

    const identity = validateIdentity(params.identity);
    const grant = validateGrant(params.grant, originOf);
    this.recordIdentityGrant(identity, grant);
    const cleanOp = params.operation.replace(/^browser_/, '');

    if (cleanOp === 'sessions') {
      const response = await this.sendToWorker({ type: 'sessions', identity });
      return { sessions: (response.result ?? []) as Record<string, unknown>[] };
    }
    if (cleanOp === 'preview' || cleanOp === 'host_preview') {
      const response = await this.sendToWorker({
        type: cleanOp,
        identity,
        grant,
        ...(cleanOp === 'preview' ? { full_page: Boolean(params.arguments?.full_page) } : {}),
      });
      const bytes = response.binary ?? new Uint8Array();
      const artifactPath = this.saveEphemeralBuffer(bytes, cleanOp);
      return {
        artifact_path: artifactPath,
        size_bytes: bytes.byteLength,
        mime_type: 'image/png',
      };
    }
    if (cleanOp === 'host_input') {
      const kind = String(params.arguments?.kind ?? '');
      const inputArgs = (params.arguments?.arguments ?? {}) as Record<string, unknown>;
      const response = await this.sendToWorker(
        {
          type: 'host_input',
          identity,
          grant,
          kind,
          arguments: inputArgs,
        },
        { dispatched: true },
      );
      return (response.result ?? {}) as Record<string, unknown>;
    }
    if (cleanOp === 'resume') {
      const response = await this.sendToWorker({
        type: 'resume',
        identity,
        grant,
      });
      return (response.result ?? {}) as Record<string, unknown>;
    }
    if (params.host_control) {
      const response = await this.sendToWorker(
        {
          type: 'host_command',
          identity,
          grant,
          operation: cleanOp,
          arguments: params.arguments,
        },
        { dispatched: NAVIGATIONS.has(cleanOp) },
      );
      return (response.result ?? {}) as Record<string, unknown>;
    }

    const isSideEffect = INTERACTIONS.has(cleanOp) || NAVIGATIONS.has(cleanOp);
    const response = await this.sendToWorker(
      {
        type: 'dispatch',
        identity,
        grant,
        operation: cleanOp,
        arguments: params.arguments,
        approved_fingerprint: params.approved_fingerprint,
        host_control: false,
      },
      { dispatched: isSideEffect },
    );
    return (response.result ?? {}) as Record<string, unknown>;
  }

  async handleRevoke(params: {
    identity?: BrowserIdentity | null;
    grant?: BrowserGrant | null;
    close_owner?: boolean;
    close_all?: boolean;
  }): Promise<{ ok: true }> {
    if (params.close_all) {
      this.conversationGrants.clear();
      this.activeIdentities.clear();
      if (this.inProcessDispatcher || this.port) {
        await this.sendToWorker({ type: 'close_all' });
      }
      return { ok: true };
    }
    if (params.identity) {
      const identity = validateIdentity(params.identity);
      const grant = validateGrant(params.grant ?? { enabled: false }, originOf);
      this.recordIdentityGrant(identity, grant);
      if (this.inProcessDispatcher || this.port) {
        await this.sendToWorker({ type: 'bind_grant', identity, grant });
        if (params.close_owner || !grant.enabled) {
          await this.sendToWorker({ type: 'close_owner', identity });
        }
      }
    }
    return { ok: true };
  }

  async captureUserPreview(conversationId: string): Promise<Uint8Array | null> {
    if (this.stopping || this.failed) return null;
    const grant = this.conversationGrants.get(conversationId);
    if (!grant || !grant.enabled || !grant.allow_screenshots) {
      return null;
    }
    const identity =
      this.activeIdentities.get(conversationId) ??
      validateIdentity({ conversation_id: conversationId, actor_id: 'parent' });
    const epoch = this.grantEpochs.get(conversationId);
    try {
      const response = await this.sendToWorker({
        type: 'host_preview',
        identity,
        grant,
      });
      const current = this.conversationGrants.get(conversationId);
      if (this.stopping || this.failed || !current?.enabled || !current.allow_screenshots ||
          this.grantEpochs.get(conversationId) !== epoch || !grantsEqual(current, grant)) return null;
      return response.binary ?? null;
    } catch {
      return null;
    }
  }

  shutdown(): Promise<void> {
    return this.shutdownPromise ??= this.closeResources();
  }

  private async closeResources(): Promise<void> {
    this.stopping = true;
    this.conversationGrants.clear();
    this.activeIdentities.clear();
    try {
      if (this.inProcessDispatcher || this.port) {
        await this.sendToWorker({ type: 'close_all' }, { timeoutMs: 8000 });
      }
    } catch {
      // Proceed with worker termination
    }
    if (this.port && typeof this.port.close === 'function') {
      try {
        this.port.close();
      } catch {
        // Ignore
      }
    }
    if (this.workerProcess) {
      try {
        if (typeof this.workerProcess.kill === 'function') {
          this.workerProcess.kill();
        } else if (typeof this.workerProcess.terminate === 'function') {
          await this.workerProcess.terminate();
        }
      } catch {
        // Ignore
      }
    }
    this.onWorkerExit();
    if (this.tempDir) {
      try {
        rmSync(this.tempDir, { recursive: true, force: true });
      } catch {
        // Ignore
      }
      this.tempDir = null;
    }
  }
}

export { identityKey };
