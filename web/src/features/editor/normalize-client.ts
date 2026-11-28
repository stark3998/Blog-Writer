/**
 * Typed client for the normalize worker (`normalize-worker.ts`).
 *
 * - Refuses sources over `EDITOR_MAX_BYTES` (UTF-8, measured with
 *   TextEncoder) before anything is sent to the worker.
 * - Runs one request at a time, so each timeout measures that request alone.
 * - On timeout the worker is terminated (it may be stuck in a super-linear
 *   parse) and the request fails with a `timeout` DialectError; the next
 *   request starts a fresh worker. Nothing falls back to the main thread.
 * - Every failed request rejects with a DialectError: `too-large`,
 *   `too-deep`, `invalid-frontmatter`, `invalid-content`, `timeout`,
 *   `worker-unavailable`, `worker-failed`, `cancelled` or `internal`. Only a
 *   programming error (a `timeoutMs` that is not a positive number) rejects
 *   with a RangeError.
 */
import type { Article } from './dialect/codec';
import { DialectError } from './dialect/errors';
import { assertEditableSize } from './dialect/limits';
import {
  deserializeDialectError,
  type DialectWorkerOperation,
  type DialectWorkerRequest,
  type DialectWorkerResponse,
  isDialectWorkerResponse,
} from './normalize-protocol';

/** Long enough for a cold worker on a slow phone; a 10 KB article normalizes in about 2 ms. */
export const DEFAULT_WORKER_TIMEOUT_MS = 5000;

/** What the client needs from a worker. A real `Worker` fits; tests pass a fake. */
export interface DialectWorkerLike {
  postMessage(message: DialectWorkerRequest): void;
  terminate(): void;
  addEventListener(type: 'message' | 'messageerror' | 'error', listener: (event: Event) => void): void;
}

export interface DialectWorkerClientOptions {
  /** Starts a worker. Defaults to the bundled normalize worker (a same-origin module worker). */
  createWorker?: () => DialectWorkerLike;
}

export interface WorkerCallOptions {
  /** How long the worker may take for this request (default `DEFAULT_WORKER_TIMEOUT_MS`). */
  timeoutMs?: number;
}

type SuccessResponse = Extract<DialectWorkerResponse, { ok: true }>;

interface QueuedRequest {
  op: DialectWorkerOperation;
  markdown: string;
  timeoutMs: number;
  resolve: (response: SuccessResponse) => void;
  reject: (error: DialectError) => void;
}

interface ActiveRequest extends QueuedRequest {
  id: number;
  timer: ReturnType<typeof setTimeout>;
}

const ACTIONS: Record<DialectWorkerOperation, string> = {
  normalize: 'Normalizing the article',
  parse: 'Reading the article',
};

/** The bundled worker. Vite turns this `new URL(..., import.meta.url)` into a same-origin worker file (CSP `worker-src 'self'`). */
export function createNormalizeWorker(): DialectWorkerLike {
  return new Worker(new URL('./normalize-worker.ts', import.meta.url), { type: 'module', name: 'article-dialect' });
}

function timeoutOf(options: WorkerCallOptions | undefined): number {
  const timeoutMs = options?.timeoutMs ?? DEFAULT_WORKER_TIMEOUT_MS;
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) {
    throw new RangeError('timeoutMs must be a positive number of milliseconds');
  }
  return timeoutMs;
}

export class DialectWorkerClient {
  private readonly createWorker: () => DialectWorkerLike;
  private worker: DialectWorkerLike | null = null;
  private readonly queue: QueuedRequest[] = [];
  private active: ActiveRequest | null = null;
  private nextId = 1;
  private disposed = false;

  constructor(options: DialectWorkerClientOptions = {}) {
    this.createWorker = options.createWorker ?? createNormalizeWorker;
  }

  /** The canonical Markdown for `markdown` (frontmatter included), computed in the worker. */
  async normalize(markdown: string, options?: WorkerCallOptions): Promise<string> {
    const response = await this.request('normalize', markdown, options);
    if (response.op !== 'normalize') {
      throw new DialectError('worker-failed', 'The editor worker answered with the wrong kind of result.');
    }
    return response.markdown;
  }

  /** The parsed article (frontmatter and TipTap JSON), computed in the worker. Load it with `setContent(doc)`. */
  async parse(markdown: string, options?: WorkerCallOptions): Promise<Article> {
    const response = await this.request('parse', markdown, options);
    if (response.op !== 'parse') {
      throw new DialectError('worker-failed', 'The editor worker answered with the wrong kind of result.');
    }
    return response.article;
  }

  /** Stop the worker and fail every pending request with `cancelled`. The client cannot be used afterwards. */
  dispose(): void {
    if (this.disposed) {
      return;
    }
    this.disposed = true;
    const cancelled = new DialectError('cancelled', 'The editor worker was stopped before the article was processed.');
    const pending = [...(this.active ? [this.active] : []), ...this.queue.splice(0)];
    if (this.active) {
      clearTimeout(this.active.timer);
      this.active = null;
    }
    this.stopWorker();
    for (const request of pending) {
      request.reject(cancelled);
    }
  }

  private request(op: DialectWorkerOperation, markdown: string, options: WorkerCallOptions | undefined): Promise<SuccessResponse> {
    if (this.disposed) {
      return Promise.reject(new DialectError('cancelled', 'The editor worker has been stopped.'));
    }

    let timeoutMs: number;
    try {
      timeoutMs = timeoutOf(options);
      assertEditableSize(markdown);
    } catch (error) {
      return Promise.reject(error instanceof Error ? error : new Error(String(error)));
    }

    return new Promise<SuccessResponse>((resolve, reject) => {
      this.queue.push({ op, markdown, timeoutMs, resolve, reject });
      this.pump();
    });
  }

  /** Send the next queued request when none is in flight. */
  private pump(): void {
    while (!this.active && !this.disposed) {
      const next = this.queue.shift();
      if (!next) {
        return;
      }

      let worker: DialectWorkerLike;
      try {
        worker = this.ensureWorker();
      } catch (error) {
        next.reject(
          new DialectError('worker-unavailable', 'The editor worker could not be started in this browser.', {
            cause: error,
          }),
        );
        continue;
      }

      const id = this.nextId;
      this.nextId += 1;
      const timer = setTimeout(() => {
        this.fail(
          id,
          new DialectError(
            'timeout',
            `${ACTIONS[next.op]} took longer than ${next.timeoutMs} ms, so it was stopped. The article may be too complex for the editor.`,
          ),
        );
      }, next.timeoutMs);
      this.active = { ...next, id, timer };

      try {
        worker.postMessage({ id, op: next.op, markdown: next.markdown });
      } catch (error) {
        this.fail(id, new DialectError('worker-failed', 'The article could not be sent to the editor worker.', { cause: error }));
      }
    }
  }

  private ensureWorker(): DialectWorkerLike {
    if (this.worker) {
      return this.worker;
    }

    const worker = this.createWorker();
    worker.addEventListener('message', (event) => {
      if (this.worker === worker) {
        this.onMessage('data' in event ? event.data : undefined);
      }
    });
    worker.addEventListener('messageerror', () => {
      if (this.worker === worker && this.active) {
        this.fail(this.active.id, new DialectError('worker-failed', 'The editor worker sent a message that could not be read.'));
      }
    });
    worker.addEventListener('error', (event) => {
      // Handled here; keep the browser from also reporting it as uncaught.
      event.preventDefault();
      if (this.worker !== worker) {
        return;
      }
      if (this.active) {
        this.fail(this.active.id, new DialectError('worker-failed', 'The editor worker stopped with an error.'));
      } else {
        this.stopWorker();
      }
    });
    this.worker = worker;
    return worker;
  }

  private onMessage(data: unknown): void {
    const active = this.active;
    if (!active) {
      return;
    }

    if (!isDialectWorkerResponse(data)) {
      this.fail(active.id, new DialectError('worker-failed', 'The editor worker sent a message that does not follow the protocol.'));
      return;
    }
    if (data.id !== active.id) {
      // An answer to a request that already failed; the worker is restarted after a failure, so this is stale.
      return;
    }

    clearTimeout(active.timer);
    this.active = null;
    if (data.ok) {
      active.resolve(data);
    } else {
      active.reject(deserializeDialectError(data.error));
    }
    this.pump();
  }

  /** Fail the active request `id` and restart the worker: after a timeout or a crash its state is unknown. */
  private fail(id: number, error: DialectError): void {
    const active = this.active;
    if (active?.id !== id) {
      return;
    }

    clearTimeout(active.timer);
    this.active = null;
    this.stopWorker();
    active.reject(error);
    this.pump();
  }

  private stopWorker(): void {
    const worker = this.worker;
    this.worker = null;
    worker?.terminate();
  }
}

let sharedClient: DialectWorkerClient | null = null;

function defaultClient(): DialectWorkerClient {
  sharedClient ??= new DialectWorkerClient();
  return sharedClient;
}

/**
 * Normalize an article source in the shared worker: the canonical Markdown,
 * or a DialectError (`too-large` before anything is sent, `timeout` when the
 * worker takes longer than `timeoutMs`, and the codec's own errors).
 */
export function normalizeInWorker(markdown: string, options?: WorkerCallOptions): Promise<string> {
  return defaultClient().normalize(markdown, options);
}

/** Parse an article source in the shared worker, for loading it into the editor without parsing on the main thread. */
export function parseInWorker(markdown: string, options?: WorkerCallOptions): Promise<Article> {
  return defaultClient().parse(markdown, options);
}

/** Stop the shared worker (for example when leaving the editor). The next call starts a new one. */
export function disposeDialectWorker(): void {
  sharedClient?.dispose();
  sharedClient = null;
}
