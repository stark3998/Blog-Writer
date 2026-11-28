import { http, HttpResponse, type JsonBodyType } from 'msw';

import { apiUrl } from '@/test/msw';

import { foldIndex } from '../model/text';
import type { Finding, Post } from '../model/types';

import { ARTICLE_ID, BRAND, CADENCE, makeArticle, MACHINES, PILLARS, SHA256, summaryOf, T0, versioned } from './fixtures';
import { machinesResponse } from '@/test/machines';

/**
 * A small in-memory server for the Posts tab: one store of posts with etags that move on every
 * write, If-Match checked like the server (412 with `current`), and every request recorded.
 * Install with `server.use(...posts.handlers)`.
 */

export interface RecordedRequest {
  readonly method: string;
  readonly path: string;
  readonly ifMatch: string | null;
  readonly body: unknown;
}

/** A problem document with extension members, as the server sends them. */
export function problemWith(status: number, code: string, extensions: Record<string, unknown> = {}, detail?: string): Response {
  return HttpResponse.json(
    { type: 'about:blank', title: 'Error', status, code, request_id: 'req-test-9', ...(detail ? { detail } : {}), ...extensions },
    { status, headers: { 'Content-Type': 'application/problem+json', 'X-Request-Id': 'req-test-9' } },
  );
}

export interface PostsServerOptions {
  /** The article piece (`GET /api/articles/{id}`). */
  article?: Parameters<typeof makeArticle>[0];
  /** Findings the live lint returns for a text. */
  lint?: (text: string) => Finding[];
}

/** The live lint's default: a link blocks (the zero-links rule), as `linkedin.lint` does. */
export function defaultLint(text: string): Finding[] {
  const match = /https?:\/\/\S+/u.exec(text);
  if (!match) return [];
  const start = Array.from(text.slice(0, match.index)).length;
  return [
    {
      code: 'link',
      severity: 'block',
      message: 'Remove the link: posts carry no links.',
      start,
      end: start + Array.from(match[0]).length,
      overridable: false,
    },
  ];
}

export function postsServer(initial: readonly Post[], options: PostsServerOptions = {}) {
  const posts = new Map<string, { post: Post; etag: number }>(initial.map((post) => [post.id, { post, etag: 1 }]));
  const requests: RecordedRequest[] = [];
  const lint = options.lint ?? defaultLint;
  let jobs = 0;

  const etagOf = (id: string) => `"${String(posts.get(id)?.etag ?? 0)}"`;
  const current = (id: string) => {
    const entry = posts.get(id);
    return entry ? { item: entry.post, etag: etagOf(id) } : undefined;
  };
  const write = (id: string, change: Partial<Post>) => {
    const entry = posts.get(id);
    if (!entry) return undefined;
    entry.post = { ...entry.post, ...change, updatedAt: T0 };
    entry.etag += 1;
    return current(id);
  };
  const record = async (request: Request, path: string): Promise<unknown> => {
    const text = await request.text();
    const body: unknown = text === '' ? undefined : JSON.parse(text);
    requests.push({ method: request.method, path, ifMatch: request.headers.get('If-Match'), body });
    return body;
  };
  /** 412 with the current version when If-Match is stale; 428 without one. */
  const precondition = (request: Request, id: string): Response | undefined => {
    const ifMatch = request.headers.get('If-Match');
    if (ifMatch === null) return problemWith(428, 'if_match_required');
    if (ifMatch !== etagOf(id)) {
      return problemWith(412, 'etag_mismatch', {
        current: { etag: etagOf(id), updatedAt: T0, updatedBy: 'pipeline', status: posts.get(id)?.post.status },
      });
    }
    return undefined;
  };
  const accepted = () => {
    jobs += 1;
    return HttpResponse.json({ jobId: `job-${String(jobs)}`, status: 'queued' }, { status: 202 });
  };
  const json = (value: unknown) => HttpResponse.json(value as JsonBodyType);
  const missing = () => problemWith(404, 'not_found');

  const handlers = [
    http.get(apiUrl('/api/meta/machines'), () => json(machinesResponse(MACHINES))),
    http.get(apiUrl('/api/kb/pillars'), () => json(PILLARS)),
    http.get(apiUrl('/api/kb/brand'), () => json(BRAND)),
    http.get(apiUrl('/api/kb/cadence'), () => json(CADENCE)),
    http.get(apiUrl('/api/articles/:articleId'), ({ params }) =>
      params.articleId === ARTICLE_ID ? json(versioned(makeArticle(options.article))) : missing(),
    ),
    http.get(apiUrl('/api/posts'), ({ request }) => {
      const articleId = new URL(request.url).searchParams.get('articleId');
      const items = [...posts.values()]
        .map((entry) => entry.post)
        .filter((post) => articleId === null || post.articleId === articleId)
        .map((post) => ({ item: summaryOf(post), etag: etagOf(post.id) }));
      return json({ items, nextCursor: null });
    }),
    http.get(apiUrl('/api/posts/:postId'), ({ params }) => {
      const found = current(String(params.postId));
      return found ? json(found) : missing();
    }),
    http.put(apiUrl('/api/posts/:postId'), async ({ request, params }) => {
      const id = String(params.postId);
      const body = (await record(request, `/api/posts/${id}`)) as Record<string, unknown>;
      const refused = precondition(request, id);
      if (refused) return refused;
      const before = posts.get(id)?.post;
      const displayText = String(body.displayText);
      return json(
        write(id, {
          displayText,
          payload: displayText,
          hashtags: (body.hashtags as string[] | undefined) ?? before?.hashtags ?? [],
          ...(typeof body.selectedHookId === 'string' ? { selectedHookId: body.selectedHookId } : {}),
          ...(typeof body.pillar === 'string' ? { pillar: body.pillar } : {}),
          ...(typeof body.format === 'string' ? { format: body.format as Post['format'], media: [] } : {}),
          ...(before?.status === 'approved' || before?.status === 'staged' ? { status: 'draft', approval: null } : {}),
        }),
      );
    }),
    http.post(apiUrl('/api/posts/:postId/lint'), async ({ request }) => {
      const body = (await request.json()) as { displayText: string };
      return json({
        findings: lint(body.displayText),
        payloadUnits: body.displayText.length,
        foldIndex: foldIndex(body.displayText),
      });
    }),
    http.post(apiUrl('/api/posts/:postId/check'), async ({ request, params }) => {
      await record(request, `/api/posts/${String(params.postId)}/check`);
      return accepted();
    }),
    http.post(apiUrl('/api/posts/:postId/render'), async ({ request, params }) => {
      await record(request, `/api/posts/${String(params.postId)}/render`);
      return accepted();
    }),
    http.put(apiUrl('/api/posts/:postId/carousel'), async ({ request, params }) => {
      const id = String(params.postId);
      const body = (await record(request, `/api/posts/${id}/carousel`)) as { spec: Record<string, unknown> };
      const refused = precondition(request, id);
      if (refused) return refused;
      return json(
        write(id, {
          carouselSpec: body.spec,
          documentTitle: typeof body.spec.title === 'string' ? body.spec.title : null,
          media: [],
        }),
      );
    }),
    http.post(apiUrl('/api/posts/:postId/regenerate'), async ({ request, params }) => {
      const id = String(params.postId);
      await record(request, `/api/posts/${id}/regenerate`);
      const refused = precondition(request, id);
      if (refused) return refused;
      write(id, { status: 'generating', approval: null });
      return accepted();
    }),
    http.post(apiUrl('/api/posts/:postId/approve'), async ({ request, params }) => {
      const id = String(params.postId);
      const body = (await record(request, `/api/posts/${id}/approve`)) as { slot?: { localDate: string; localTime: string } } | undefined;
      const refused = precondition(request, id);
      if (refused) return refused;
      return json(
        write(id, {
          status: 'approved',
          approval: { sha256: SHA256, approvedAt: '2026-11-27T13:05:00.000000Z' },
          ...(body?.slot
            ? {
                slot: {
                  localDate: body.slot.localDate,
                  localTime: body.slot.localTime,
                  timezone: 'America/New_York',
                  utc: `${body.slot.localDate}T14:00:00.000000Z`,
                  kind: 'custom' as const,
                },
              }
            : {}),
        }),
      );
    }),
    http.post(apiUrl('/api/posts/:postId/unapprove'), async ({ request, params }) => {
      const id = String(params.postId);
      await record(request, `/api/posts/${id}/unapprove`);
      const refused = precondition(request, id);
      if (refused) return refused;
      return json(write(id, { status: 'draft', approval: null }));
    }),
    http.post(apiUrl('/api/posts/:postId/archive'), async ({ request, params }) => {
      const id = String(params.postId);
      await record(request, `/api/posts/${id}/archive`);
      const refused = precondition(request, id);
      if (refused) return refused;
      return json(write(id, { status: 'archived' }));
    }),
    http.post(apiUrl('/api/articles/:articleId/posts/derive'), async ({ request, params }) => {
      await record(request, `/api/articles/${String(params.articleId)}/posts/derive`);
      return accepted();
    }),
  ];

  return {
    handlers,
    requests,
    /** Requests to one path and method. */
    calls: (method: string, path: string) => requests.filter((entry) => entry.method === method && entry.path === path),
    post: (id: string) => posts.get(id)?.post,
    etag: etagOf,
    current,
    /** Changes a post as another writer would (the etag moves). */
    change: (id: string, change: Partial<Post>) => write(id, change),
    /** For handlers built on this store (go-live): record a request, check If-Match, answer 202. */
    record,
    precondition,
    accepted,
  };
}
