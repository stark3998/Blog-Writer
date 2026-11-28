/**
 * The worker client with a fake worker (jsdom has no Worker). The fake answers
 * with the real worker-side handler and codec, or misbehaves on purpose.
 */
import { afterAll, afterEach, describe, expect, test, vi } from 'vitest';

import { ArticleCodec } from './dialect/codec';
import { DialectError, FrontmatterError } from './dialect/errors';
import { EDITOR_MAX_BYTES } from './dialect/limits';
import {
  DialectWorkerClient,
  disposeDialectWorker,
  normalizeInWorker,
  parseInWorker,
} from './normalize-client';
import { type DialectWorkerRequest, handleDialectWorkerRequest } from './normalize-protocol';
import { FakeWorker } from './testing/fake-worker';

const codec = new ArticleCodec();
afterAll(() => {
  codec.destroy();
});
afterEach(() => {
  disposeDialectWorker();
});

/** Answers like the real worker. */
function answer(request: DialectWorkerRequest): unknown {
  return handleDialectWorkerRequest(() => codec, request).response;
}

/** A client whose workers are fakes; `workers` lists every worker it started. */
function clientWith(respond: (request: DialectWorkerRequest, worker: number) => unknown = answer) {
  const workers: FakeWorker[] = [];
  const createWorker = vi.fn(() => {
    const index = workers.length;
    const worker = new FakeWorker((request) => respond(request, index));
    workers.push(worker);
    return worker;
  });
  return { client: new DialectWorkerClient({ createWorker }), createWorker, workers };
}

async function rejection(promise: Promise<unknown>): Promise<DialectError> {
  try {
    await promise;
  } catch (error) {
    if (error instanceof DialectError) {
      return error;
    }
    throw error;
  }
  throw new Error('expected the promise to reject');
}

describe('normalize and parse in the worker', () => {
  test('normalize returns the canonical Markdown', async () => {
    const { client, workers } = clientWith();
    await expect(client.normalize('Title\n=====\n\n* a\n* b\n')).resolves.toBe('# Title\n\n- a\n- b\n');
    expect(workers[0]?.received.map((request) => request.op)).toEqual(['normalize']);
  });

  test('parse returns the article: frontmatter and TipTap JSON', async () => {
    const { client } = clientWith();
    const article = await client.parse('## Heading\n\nText.\n');
    expect(article.frontmatter).toBeNull();
    expect(article.doc.content?.map((node) => node.type)).toEqual(['heading', 'paragraph']);
  });

  test('one worker serves every request, one request at a time', async () => {
    const { client, createWorker, workers } = clientWith();
    const results = await Promise.all([client.normalize('a\n'), client.parse('b\n'), client.normalize('*c*\n')]);
    expect(results[0]).toBe('a\n');
    expect(results[2]).toBe('*c*\n');
    expect(createWorker).toHaveBeenCalledTimes(1);
    expect(workers[0]?.received.map((request) => request.id)).toEqual([1, 2, 3]);
  });

  test('codec errors arrive as typed errors', async () => {
    const { client } = clientWith();
    const frontmatter = await rejection(client.normalize('---\ntitle: "x"\n---\n\nBody\n'));
    expect(frontmatter).toBeInstanceOf(FrontmatterError);
    expect(frontmatter instanceof FrontmatterError ? frontmatter.issues : []).toContainEqual({ path: 'slug', message: 'required' });

    const deep = await rejection(client.normalize(`${'>'.repeat(5000)} deep\n`));
    expect(deep.code).toBe('too-deep');
  });
});

describe('size limit', () => {
  test('a source over 128 KB is refused before a worker is started', async () => {
    const { client, createWorker } = clientWith();
    const error = await rejection(client.normalize('a'.repeat(EDITOR_MAX_BYTES + 1)));
    expect(error.code).toBe('too-large');
    expect(createWorker).not.toHaveBeenCalled();
  });

  test('UTF-8 bytes count, not characters', async () => {
    const { client, createWorker } = clientWith();
    // 65,537 characters, 131,074 bytes.
    expect((await rejection(client.normalize('é'.repeat(EDITOR_MAX_BYTES / 2 + 1)))).code).toBe('too-large');
    expect(createWorker).not.toHaveBeenCalled();
  });

  test('exactly 128 KB is sent', async () => {
    const { client, workers } = clientWith();
    const source = `${'a'.repeat(EDITOR_MAX_BYTES - 1)}\n`;
    await expect(client.normalize(source)).resolves.toBe(source);
    expect(workers[0]?.received).toHaveLength(1);
  });
});

describe('timeouts and failures', () => {
  test('a worker that does not answer in time is stopped, and the next request gets a fresh one', async () => {
    const { client, createWorker, workers } = clientWith((request, worker) => (worker === 0 ? undefined : answer(request)));
    const error = await rejection(client.normalize('slow\n', { timeoutMs: 20 }));
    expect(error.code).toBe('timeout');
    expect(error.message).toBe(
      'Normalizing the article took longer than 20 ms, so it was stopped. The article may be too complex for the editor.',
    );
    expect(workers[0]?.terminated).toBe(true);

    await expect(client.normalize('fast\n')).resolves.toBe('fast\n');
    expect(createWorker).toHaveBeenCalledTimes(2);
  });

  test('each timeout measures its own request: queued requests wait without their timer running', async () => {
    const { client } = clientWith((request, worker) => (worker === 0 ? undefined : answer(request)));
    const first = client.normalize('hangs\n', { timeoutMs: 40 });
    const second = client.normalize('waits\n', { timeoutMs: 25 });
    expect((await rejection(first)).code).toBe('timeout');
    await expect(second).resolves.toBe('waits\n');
  });

  test('a crashed worker fails the request and is replaced', async () => {
    const { client, workers } = clientWith((request, worker) => (worker === 0 ? undefined : answer(request)));
    const pending = client.normalize('x\n');
    await vi.waitFor(() => {
      expect(workers[0]?.received).toHaveLength(1);
    });
    const event = new Event('error', { cancelable: true });
    workers[0]?.emit('error', event);
    expect((await rejection(pending)).code).toBe('worker-failed');
    expect(event.defaultPrevented).toBe(true);
    expect(workers[0]?.terminated).toBe(true);
    await expect(client.normalize('y\n')).resolves.toBe('y\n');
  });

  test('a message the browser cannot deserialize fails the request', async () => {
    const { client, workers } = clientWith(() => undefined);
    const pending = client.normalize('x\n');
    await vi.waitFor(() => {
      expect(workers[0]?.received).toHaveLength(1);
    });
    workers[0]?.emit('messageerror', new MessageEvent('messageerror'));
    expect((await rejection(pending)).code).toBe('worker-failed');
  });

  test.each([
    ['not an object', 'hello'],
    ['no id', { ok: true, op: 'normalize', markdown: 'x' }],
    ['a success without its result', { id: 1, ok: true, op: 'normalize' }],
    ['an unknown error code', { id: 1, ok: false, error: { code: 'nope', message: 'x' } }],
    ['the wrong kind of result', { id: 1, ok: true, op: 'parse', article: { frontmatter: null, doc: { type: 'doc' } } }],
  ])('an answer outside the protocol (%s) fails the request', async (_name, reply) => {
    const { client } = clientWith(() => reply);
    expect((await rejection(client.normalize('x\n'))).code).toBe('worker-failed');
  });

  test('a stale answer with another id is ignored', async () => {
    const { client } = clientWith((request) => ({ id: request.id + 100, ok: true, op: 'normalize', markdown: 'stale' }));
    expect((await rejection(client.normalize('x\n', { timeoutMs: 30 }))).code).toBe('timeout');
  });

  test('a browser without workers (or a CSP that blocks them) gives worker-unavailable', async () => {
    const client = new DialectWorkerClient({
      createWorker: () => {
        throw new DOMException('blocked', 'SecurityError');
      },
    });
    const error = await rejection(client.normalize('x\n'));
    expect(error.code).toBe('worker-unavailable');
    expect(error.cause).toBeInstanceOf(DOMException);
  });

  test.each([0, -5, Number.NaN, Number.POSITIVE_INFINITY])('timeoutMs %d is refused', async (timeoutMs) => {
    const { client } = clientWith();
    await expect(client.normalize('x\n', { timeoutMs })).rejects.toBeInstanceOf(RangeError);
  });
});

describe('dispose', () => {
  test('fails the active and queued requests, stops the worker, and refuses new ones', async () => {
    const { client, workers } = clientWith(() => undefined);
    const active = client.normalize('a\n');
    const queued = client.parse('b\n');
    await vi.waitFor(() => {
      expect(workers[0]?.received).toHaveLength(1);
    });
    client.dispose();
    expect((await rejection(active)).code).toBe('cancelled');
    expect((await rejection(queued)).code).toBe('cancelled');
    expect(workers[0]?.terminated).toBe(true);
    expect((await rejection(client.normalize('c\n'))).code).toBe('cancelled');
  });
});

describe('the shared worker', () => {
  test('without Worker support (jsdom) the call fails with worker-unavailable', async () => {
    expect('Worker' in globalThis).toBe(false);
    expect((await rejection(normalizeInWorker('x\n'))).code).toBe('worker-unavailable');
  });

  test('starts the bundled module worker and reuses it', async () => {
    const started: { url: string; options: unknown }[] = [];
    class StubWorker extends FakeWorker {
      constructor(url: URL | string, options?: unknown) {
        super(answer);
        started.push({ url: String(url), options });
      }
    }
    vi.stubGlobal('Worker', StubWorker);

    await expect(normalizeInWorker('* a\n')).resolves.toBe('- a\n');
    await expect(parseInWorker('b\n')).resolves.toMatchObject({ frontmatter: null });
    expect(started).toHaveLength(1);
    expect(started[0]?.url).toMatch(/normalize-worker\.ts/);
    expect(started[0]?.options).toEqual({ type: 'module', name: 'article-dialect' });

    disposeDialectWorker();
    await expect(normalizeInWorker('c\n')).resolves.toBe('c\n');
    expect(started).toHaveLength(2);
  });
});
