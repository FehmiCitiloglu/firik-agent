import { parentPort as nodeParentPort } from 'node:worker_threads';
import { PlaywrightBrowserExecutor, type BrowserExecutor } from './executor.js';
import { BrowserError, defaultBrowserConfig, type BrowserConfig, type BrowserGrant, type BrowserIdentity } from './models.js';

export * from './models.js';
export * from './security.js';
export * from './executor.js';

export type WorkerRequestPayload =
  | { type: 'configure'; config: Partial<BrowserConfig> }
  | { type: 'bind_grant'; identity: BrowserIdentity; grant: BrowserGrant }
  | {
      type: 'prepare';
      identity: BrowserIdentity;
      grant: BrowserGrant;
      operation: string;
      arguments?: Record<string, unknown>;
    }
  | {
      type: 'dispatch';
      identity: BrowserIdentity;
      grant: BrowserGrant;
      operation: string;
      arguments?: Record<string, unknown>;
      approved_fingerprint?: string | null;
      host_control?: boolean;
    }
  | {
      type: 'host_command';
      identity: BrowserIdentity;
      grant: BrowserGrant;
      operation: string;
      arguments?: Record<string, unknown>;
    }
  | {
      type: 'host_input';
      identity: BrowserIdentity;
      grant: BrowserGrant;
      kind: string;
      arguments?: Record<string, unknown>;
    }
  | { type: 'resume'; identity: BrowserIdentity; grant: BrowserGrant }
  | { type: 'preview'; identity: BrowserIdentity; grant: BrowserGrant; full_page?: boolean }
  | { type: 'host_preview'; identity: BrowserIdentity; grant: BrowserGrant }
  | { type: 'sessions'; identity: BrowserIdentity }
  | { type: 'close_owner'; identity: BrowserIdentity }
  | { type: 'close_all' };

export interface WorkerEnvelope {
  id: number;
  payload: WorkerRequestPayload;
}

export interface WorkerResponseEnvelope {
  id: number;
  ok: boolean;
  result?: unknown;
  binary?: Uint8Array;
  error?: {
    code: string;
    message: string;
    dispatched: boolean;
    requires_reconciliation: boolean;
  };
}

export class WorkerRequestDispatcher {
  readonly executor: BrowserExecutor;

  constructor(executor?: BrowserExecutor) {
    this.executor = executor ?? new PlaywrightBrowserExecutor({ enabled: true });
  }

  async handle(envelope: WorkerEnvelope): Promise<WorkerResponseEnvelope> {
    const { id, payload } = envelope;
    try {
      switch (payload.type) {
        case 'configure': {
          Object.assign(this.executor.config, defaultBrowserConfig(payload.config));
          return { id, ok: true, result: { ok: true } };
        }
        case 'bind_grant': {
          this.executor.bindGrant(payload.identity, payload.grant);
          return { id, ok: true, result: { ok: true } };
        }
        case 'prepare': {
          this.executor.bindGrant(payload.identity, payload.grant);
          const result = await this.executor.prepare(
            payload.identity,
            payload.grant,
            payload.operation,
            payload.arguments,
          );
          return { id, ok: true, result };
        }
        case 'dispatch': {
          this.executor.bindGrant(payload.identity, payload.grant);
          const result = await this.executor.dispatch(
            payload.identity,
            payload.grant,
            payload.operation,
            payload.arguments,
            {
              approvedFingerprint: payload.approved_fingerprint,
              hostControl: payload.host_control,
            },
          );
          return { id, ok: true, result };
        }
        case 'host_command': {
          this.executor.bindGrant(payload.identity, payload.grant);
          const result = await this.executor.hostCommand(
            payload.identity,
            payload.grant,
            payload.operation,
            payload.arguments,
          );
          return { id, ok: true, result };
        }
        case 'host_input': {
          this.executor.bindGrant(payload.identity, payload.grant);
          const result = await this.executor.hostInput(
            payload.identity,
            payload.grant,
            payload.kind,
            payload.arguments,
          );
          return { id, ok: true, result };
        }
        case 'resume': {
          this.executor.bindGrant(payload.identity, payload.grant);
          const result = await this.executor.resume(payload.identity, payload.grant);
          return { id, ok: true, result };
        }
        case 'preview': {
          this.executor.bindGrant(payload.identity, payload.grant);
          const binary = await this.executor.preview(
            payload.identity,
            payload.grant,
            payload.full_page,
          );
          return { id, ok: true, binary };
        }
        case 'host_preview': {
          this.executor.bindGrant(payload.identity, payload.grant);
          const binary = await this.executor.hostPreview(payload.identity, payload.grant);
          return { id, ok: true, binary };
        }
        case 'sessions': {
          const result = this.executor.sessions(payload.identity);
          return { id, ok: true, result };
        }
        case 'close_owner': {
          await this.executor.closeOwner(payload.identity);
          return { id, ok: true, result: { state: 'closed' } };
        }
        case 'close_all': {
          await this.executor.closeAll();
          return { id, ok: true, result: { state: 'closed' } };
        }
        default:
          throw new BrowserError('unsupported_capability', 'Unknown browser worker request.');
      }
    } catch (err: unknown) {
      if (err instanceof BrowserError) {
        return {
          id,
          ok: false,
          error: {
            code: err.code,
            message: err.message,
            dispatched: err.dispatched,
            requires_reconciliation: err.requires_reconciliation,
          },
        };
      }
      return {
        id,
        ok: false,
        error: {
          code: 'browser_error',
          message: err instanceof Error ? err.message.slice(0, 1500) : 'Browser worker error.',
          dispatched: false,
          requires_reconciliation: false,
        },
      };
    }
  }
}

interface PortLike {
  postMessage(message: unknown): void;
  on(event: 'message', listener: (eventOrData: unknown) => void): void;
  start?: () => void;
}

function wirePointerSink(dispatcher: WorkerRequestDispatcher, target: Pick<PortLike, 'postMessage'>): void {
  dispatcher.executor.setPointerSink?.(pointer => {
    try {
      target.postMessage({ type: 'pointer', pointer });
    } catch {
      // Ignore closed port during teardown
    }
  });
}

function attachPort(port: PortLike, dispatcher: WorkerRequestDispatcher): void {
  wirePointerSink(dispatcher, port);
  port.on('message', (raw: unknown) => {
    const data =
      raw && typeof raw === 'object' && 'data' in raw && !('id' in raw && 'payload' in raw)
        ? (raw as { data: unknown }).data
        : raw;
    if (!data || typeof data !== 'object' || !('id' in data) || !('payload' in data)) {
      return;
    }
    void dispatcher.handle(data as WorkerEnvelope).then(response => {
      port.postMessage(response);
    });
  });
  port.start?.();
}

// Automatically bind when launched as an Electron utilityProcess or Node Worker thread
const electronParentPort = (process as unknown as { parentPort?: PortLike & { once: (e: string, cb: (ev: { ports?: PortLike[] }) => void) => void } }).parentPort;
if (electronParentPort && typeof electronParentPort.once === 'function') {
  const dispatcher = new WorkerRequestDispatcher();
  electronParentPort.once('message', event => {
    const port = event?.ports?.[0] ?? electronParentPort;
    attachPort(port, dispatcher);
  });
} else if (nodeParentPort) {
  const dispatcher = new WorkerRequestDispatcher();
  wirePointerSink(dispatcher, nodeParentPort);
  nodeParentPort.on('message', (msg: unknown) => {
    if (msg && typeof msg === 'object' && 'type' in msg && (msg as { type: string }).type === 'init_port' && 'port' in msg) {
      attachPort((msg as { port: PortLike }).port, dispatcher);
    } else if (msg && typeof msg === 'object' && 'id' in msg && 'payload' in msg) {
      void dispatcher.handle(msg as WorkerEnvelope).then(response => {
        nodeParentPort?.postMessage(response);
      });
    }
  });
}
