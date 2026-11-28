// @vitest-environment node
/**
 * The worker entry, run the way a browser runs it: no DOM, a global `self`
 * that receives messages and posts answers.
 */
import { beforeAll, describe, expect, test, vi } from 'vitest';

import type { DialectWorkerResponse } from './normalize-protocol';

type Listener = (event: MessageEvent<unknown>) => void;

const listeners: Listener[] = [];
const posted: DialectWorkerResponse[] = [];

beforeAll(async () => {
  vi.stubGlobal('self', {
    addEventListener: (type: string, listener: Listener) => {
      if (type === 'message') {
        listeners.push(listener);
      }
    },
    postMessage: (message: DialectWorkerResponse) => {
      posted.push(message);
    },
  });
  await import('./normalize-worker');
});

/** Deliver one message to the worker and return what it posted in reply (if anything). */
function send(data: unknown): DialectWorkerResponse | undefined {
  const before = posted.length;
  for (const listener of listeners) {
    listener(new MessageEvent('message', { data }));
  }
  expect(posted.length - before).toBeLessThanOrEqual(1);
  return posted.length > before ? posted.at(-1) : undefined;
}

describe('normalize worker', () => {
  test('listens for messages without a DOM', () => {
    expect(typeof document).toBe('undefined');
    expect(listeners).toHaveLength(1);
  });

  test('normalizes', () => {
    expect(send({ id: 1, op: 'normalize', markdown: 'Title\n=====\n' })).toEqual({
      id: 1,
      ok: true,
      op: 'normalize',
      markdown: '# Title\n',
    });
  });

  test('parses', () => {
    const response = send({ id: 2, op: 'parse', markdown: '---\ntitle: "T"\nslug: t\nexcerpt: "E"\npillar: agents\nformat: case-study\ntags: []\n---\n\nText.\n' });
    expect(response).toMatchObject({ id: 2, ok: true, op: 'parse' });
    expect(response?.ok && response.op === 'parse' ? response.article.frontmatter?.slug : undefined).toBe('t');
  });

  test('answers errors with their code (and frontmatter issues)', () => {
    expect(send({ id: 3, op: 'normalize', markdown: '---\ntitle: "x"\n---\n' })).toMatchObject({
      id: 3,
      ok: false,
      error: { code: 'invalid-frontmatter', issues: expect.arrayContaining([{ path: 'slug', message: 'required' }]) as unknown },
    });
    expect(send({ id: 4, op: 'normalize', markdown: `${'>'.repeat(9000)}\n` })).toMatchObject({
      id: 4,
      ok: false,
      error: { code: 'too-deep' },
    });
    expect(send({ id: 5, op: 'parse', markdown: 'x'.repeat(200 * 1024) })).toMatchObject({
      id: 5,
      ok: false,
      error: { code: 'too-large' },
    });
  });

  test('keeps working after an error (a fresh codec after too-deep)', () => {
    expect(send({ id: 6, op: 'normalize', markdown: '* still fine\n' })).toEqual({
      id: 6,
      ok: true,
      op: 'normalize',
      markdown: '- still fine\n',
    });
  });

  test.each([
    ['a string', 'normalize this'],
    ['no id', { op: 'normalize', markdown: 'x' }],
    ['an unknown operation', { id: 7, op: 'render', markdown: 'x' }],
    ['a non-string source', { id: 8, op: 'normalize', markdown: 42 }],
    ['a fractional id', { id: 1.5, op: 'normalize', markdown: 'x' }],
  ])('ignores a message outside the protocol: %s', (_name, data) => {
    expect(send(data)).toBeUndefined();
  });
});
