import { EventEmitter } from 'node:events';
import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process';
import {
  MAX_FRAME_BYTES,
  parseEvent,
  parseHostParams,
  parseHostResult,
  parseParams,
  parseResult,
  rpcHostRequest,
  rpcNotification,
  rpcResponse,
  type DesktopEvent,
  type DesktopHostMethods,
  type DesktopMethods,
  type HostMethod,
  type Method,
} from '@firik/contracts';

type Pending = { method: Method; resolve: (value: never) => void; reject: (error: Error) => void; timer: NodeJS.Timeout };
export type HostHandler = <M extends HostMethod>(
  method: M,
  params: DesktopHostMethods[M]['params'],
) => Promise<DesktopHostMethods[M]['result']>;

export class BackendClient extends EventEmitter {
  private child: ChildProcessWithoutNullStreams;
  private pending = new Map<number, Pending>();
  private buffer = Buffer.alloc(0);
  private nextId = 0;
  private closed = false;
  private stopping = false;
  private startupError: string | null = null;
  private hostHandler: HostHandler | null = null;

  constructor(command: string, args: string[], cwd: string, env = process.env) {
    super();
    this.child = spawn(command, args, { cwd, env, stdio: 'pipe', windowsHide: true, detached: process.platform !== 'win32' });
    this.child.stdout.on('data', (chunk: Buffer) => this.read(chunk));
    // Drain backend diagnostics, but never echo arbitrary provider/key payloads.
    this.child.stderr.on('data', (chunk: Buffer) => {
      if (chunk.toString('utf8').includes('FIRIK_BACKEND_DATA_IN_USE'))
        this.startupError = 'Bu veri dizini başka bir Firik uygulamasında açık. Diğer uygulamayı kapatıp yeniden deneyin.';
    });
    this.child.on('error', () => this.fail(new Error('Python backend başlatılamadı.')));
    this.child.on('exit', () => this.fail(new Error(this.startupError ?? 'Python backend bağlantısı kapandı.')));
    this.child.stdin.on('error', () => this.fail(new Error('Python backend iletişimi kesildi.')));
  }

  setHostHandler(handler: HostHandler): void {
    this.hostHandler = handler;
  }

  request<M extends Method>(method: M, input: DesktopMethods[M]['params']): Promise<DesktopMethods[M]['result']> {
    try { parseParams(method, input); } catch (error) { return Promise.reject(error); }
    if (this.closed) return Promise.reject(new Error('Backend bağlantısı kapalı. Uygulamayı yeniden başlatın.'));
    if (this.pending.size >= 32) return Promise.reject(new Error('İstek kuyruğu dolu.'));
    const id = ++this.nextId;
    const data = Buffer.from(`${JSON.stringify({ jsonrpc: '2.0', id, method, params: input })}\n`);
    if (data.length > MAX_FRAME_BYTES || this.child.stdin.writableLength + data.length > MAX_FRAME_BYTES * 2)
      return Promise.reject(new Error('İstek boyut sınırını aştı.'));
    const timeoutMs =
      method === 'system.shutdown'
        ? 45000
        : method === 'models.list'
        ? 30000
        : method.startsWith('browser.')
          ? 270000 // A user navigation may need both bounded startup and navigation.
          : 15000;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error('Backend isteği zaman aşımına uğradı; işlem otomatik tekrarlanmadı.'));
      }, timeoutMs);
      this.pending.set(id, { method, resolve: resolve as (value: never) => void, reject, timer });
      this.child.stdin.write(data);
    });
  }

  private writeFrame(payload: Record<string, unknown>): void {
    if (this.closed) return;
    const data = Buffer.from(`${JSON.stringify(payload)}\n`);
    if (data.length <= MAX_FRAME_BYTES) {
      this.child.stdin.write(data);
    }
  }

  private async handleHostRequest(req: ReturnType<typeof rpcHostRequest.parse>): Promise<void> {
    try {
      if (!this.hostHandler) {
        throw new Error('Tarayıcı host yöneticisi yapılandırılmadı.');
      }
      const params = parseHostParams(req.method, req.params);
      const rawResult = await this.hostHandler(req.method, params);
      const checked = parseHostResult(req.method, rawResult);
      this.writeFrame({ jsonrpc: '2.0', id: req.id, result: checked });
    } catch (err: unknown) {
      const browserErr = err as {
        code?: string;
        message?: string;
        dispatched?: boolean;
        requires_reconciliation?: boolean;
      };
      this.writeFrame({
        jsonrpc: '2.0',
        id: req.id,
        error: {
          code: -32001,
          message:
            typeof browserErr?.message === 'string' && browserErr.message
              ? browserErr.message.slice(0, 4096)
              : 'Tarayıcı host işlemi başarısız oldu.',
          data: {
            code: typeof browserErr?.code === 'string' ? browserErr.code : 'browser_operation_failed',
            dispatched: Boolean(browserErr?.dispatched),
            requires_reconciliation: Boolean(browserErr?.requires_reconciliation),
          },
        },
      });
    }
  }

  private read(chunk: Buffer) {
    try {
      this.buffer = Buffer.concat([this.buffer, chunk]);
      for (;;) {
        const end = this.buffer.indexOf(10);
        if (end < 0) break;
        if (end + 1 > MAX_FRAME_BYTES) throw new Error('Büyük RPC çerçevesi.');
        const frame = this.buffer.subarray(0, end);
        this.buffer = this.buffer.subarray(end + 1);
        const message: unknown = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(frame));
        if (typeof message === 'object' && message && 'method' in message) {
          if ('id' in message) {
            // Validate synchronously so malformed host frames follow the same
            // fail-closed path as every other protocol error.
            void this.handleHostRequest(rpcHostRequest.parse(message));
          } else {
            const notification = rpcNotification.parse(message);
            const event: DesktopEvent = parseEvent(notification.params);
            this.emit('event', event);
          }
        } else {
          const response = rpcResponse.parse(message);
          const id = typeof response.id === 'number' ? response.id : -1;
          const pending = this.pending.get(id);
          if (!pending) continue;
          this.pending.delete(id);
          clearTimeout(pending.timer);
          if (response.error) pending.reject(new Error(response.error.message));
          else {
            try { pending.resolve(parseResult(pending.method, response.result) as never); }
            catch { pending.reject(new Error('Backend yanıtı sözleşmeyle uyumlu değil.')); }
          }
        }
      }
      if (this.buffer.length > MAX_FRAME_BYTES) throw new Error('Büyük RPC çerçevesi.');
    } catch {
      this.fail(new Error('Backend protokolü doğrulanamadı.'));
      this.terminate();
    }
  }

  private fail(error: Error) {
    if (this.closed) return;
    this.closed = true;
    for (const pending of this.pending.values()) { clearTimeout(pending.timer); pending.reject(error); }
    this.pending.clear();
    if (!this.stopping) this.emit('disconnected', error.message);
  }

  private terminate() {
    const pid = this.child.pid;
    if (!pid || this.child.exitCode !== null || this.child.signalCode !== null) return;
    if (process.platform === 'win32') spawn('taskkill', ['/PID', String(pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' });
    else { try { process.kill(-pid, 'SIGKILL'); } catch { this.child.kill('SIGKILL'); } }
  }

  async shutdown() {
    this.stopping = true;
    if (!this.closed) {
      try { await this.request('system.shutdown', {}); } catch { /* Closing still owns the process tree. */ }
      this.child.stdin.end();
    }
    if (this.child.exitCode === null && this.child.signalCode === null) {
      await new Promise<void>(resolve => {
        const timer = setTimeout(() => { this.terminate(); resolve(); }, 10000);
        this.child.once('exit', () => { clearTimeout(timer); resolve(); });
      });
    }
    this.fail(new Error('Backend kapandı.'));
  }
}
