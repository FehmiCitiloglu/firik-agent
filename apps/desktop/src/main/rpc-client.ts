import { EventEmitter } from 'node:events';
import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process';
import { parseParams, parseResult, parseEvent, rpcResponse, rpcNotification, MAX_FRAME_BYTES,
  type DesktopMethods, type Method, type DesktopEvent } from '@firik/contracts';

type Pending = { method: Method; resolve: (value: never) => void; reject: (error: Error) => void; timer: NodeJS.Timeout };

export class BackendClient extends EventEmitter {
  private child: ChildProcessWithoutNullStreams;
  private pending = new Map<number, Pending>();
  private buffer = Buffer.alloc(0);
  private nextId = 0;
  private closed = false;
  private stopping = false;
  private startupError: string | null = null;

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

  request<M extends Method>(method: M, input: DesktopMethods[M]['params']): Promise<DesktopMethods[M]['result']> {
    try { parseParams(method, input); } catch (error) { return Promise.reject(error); }
    if (this.closed) return Promise.reject(new Error('Backend bağlantısı kapalı. Uygulamayı yeniden başlatın.'));
    if (this.pending.size >= 32) return Promise.reject(new Error('İstek kuyruğu dolu.'));
    const id = ++this.nextId;
    const data = Buffer.from(`${JSON.stringify({ jsonrpc: '2.0', id, method, params: input })}\n`);
    if (data.length > MAX_FRAME_BYTES || this.child.stdin.writableLength + data.length > MAX_FRAME_BYTES * 2)
      return Promise.reject(new Error('İstek boyut sınırını aştı.'));
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error('Backend isteği zaman aşımına uğradı; işlem otomatik tekrarlanmadı.'));
      }, method === 'models.list' ? 30000 : 15000);
      this.pending.set(id, { method, resolve: resolve as (value: never) => void, reject, timer });
      this.child.stdin.write(data);
    });
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
          const notification = rpcNotification.parse(message);
          const event: DesktopEvent = parseEvent(notification.params);
          this.emit('event', event);
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
