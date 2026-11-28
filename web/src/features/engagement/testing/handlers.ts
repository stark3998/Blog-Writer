import { http, HttpResponse, type HttpHandler } from 'msw';

import { postEntry } from '@/features/today/testing/fixtures';
import { apiUrl } from '@/test/msw';

import {
  ANGLES,
  type Angle,
  type EngagementRecord,
  type Finding,
  type ItemEntry,
  type WatchEntryVersioned,
} from './fixtures';

/** A problem with extension members (`current` on a 412, `errors`, `findings`), like the server's. */
export function problemResponse(
  status: number,
  code: string,
  extra: Record<string, unknown> = {},
  requestId = 'req-eng-1',
): Response {
  return HttpResponse.json(
    { type: 'about:blank', title: 'Error', status, code, request_id: requestId, ...extra },
    { status, headers: { 'Content-Type': 'application/problem+json', 'X-Request-Id': requestId } },
  );
}

export interface Recorded {
  readonly method: string;
  readonly path: string;
  readonly ifMatch: string | null;
  readonly contentType: string | null;
  readonly body: unknown;
}

/** The default reply check: a link blocks, a generic opener is worth a look. */
export function defaultLint(text: string): Finding[] {
  const findings: Finding[] = [];
  const link = text.search(/https?:\/\//u);
  if (link >= 0) {
    const end = text.slice(link).search(/\s/u);
    findings.push({
      code: 'external-link',
      severity: 'block',
      message: 'A link: LinkedIn shows comments with links to fewer people. Name the source instead.',
      start: link,
      end: end < 0 ? text.length : link + end,
      overridable: false,
    });
  }
  if (/^great post/iu.test(text)) {
    findings.push({
      code: 'generic-opener',
      severity: 'warn',
      message: 'Opens with praise. Start with what you add.',
      start: 0,
      end: 10,
      overridable: false,
    });
  }
  return findings;
}

export interface EngagementBackend {
  items: ItemEntry[];
  watch: WatchEntryVersioned[];
  records: EngagementRecord[];
  readonly calls: Recorded[];
  /** What `angles` answers for an item (a `Response` for an error). */
  angles: (entry: ItemEntry) => Angle[] | Response;
  lint: (text: string) => Finding[] | Response;
  extract: () => { text: string; findings: Finding[] } | Response;
  /** Runs before finalize's own checks; return a `Response` to answer with it. */
  beforeFinalize: (entry: ItemEntry) => Response | undefined;
  readonly handlers: HttpHandler[];
  /** The recorded requests to one route (`'POST'`, `'/api/engagement/items'`). */
  callsTo: (method: string, path: string) => Recorded[];
}

const stamp = () => new Date().toISOString();

/**
 * An in-memory engagement API (items, angles, lint, finalize, dismiss, extract, watchlist, records)
 * that checks If-Match like the server and records every request.
 */
export function createEngagementBackend(seed: {
  items?: ItemEntry[];
  watch?: WatchEntryVersioned[];
  records?: EngagementRecord[];
} = {}): EngagementBackend {
  let version = 100;
  const nextEtag = (prefix: string) => {
    version += 1;
    return `"${prefix}-${String(version)}"`;
  };

  const backend: EngagementBackend = {
    items: [...(seed.items ?? [])],
    watch: [...(seed.watch ?? [])],
    records: [...(seed.records ?? [])],
    calls: [],
    angles: () => ANGLES,
    lint: defaultLint,
    extract: () => ({ text: 'Read from the screenshot: which identity do your agents use?', findings: [] }),
    beforeFinalize: () => undefined,
    handlers: [],
    callsTo: (method, path) => backend.calls.filter((call) => call.method === method && call.path === path),
  };

  async function record(request: Request): Promise<Recorded> {
    const url = new URL(request.url);
    const contentType = request.headers.get('Content-Type');
    let body: unknown = null;
    if (request.method !== 'GET' && request.method !== 'DELETE') {
      if (contentType?.includes('json')) body = await request.clone().json();
      else if (request.body) body = new Uint8Array(await request.clone().arrayBuffer());
    }
    const entry: Recorded = {
      method: request.method,
      path: url.pathname,
      ifMatch: request.headers.get('If-Match'),
      contentType,
      body,
    };
    backend.calls.push(entry);
    return entry;
  }

  const findItem = (id: string) => backend.items.find((entry) => entry.item.id === id);
  const stale = (current: { etag: string | null; item: { updatedAt: string } }) =>
    problemResponse(412, 'etag_mismatch', {
      current: { etag: current.etag, updatedAt: current.item.updatedAt, updatedBy: 'owner' },
    });
  const gone = () => problemResponse(404, 'not_found');
  const page = (items: readonly object[]) => HttpResponse.json({ items, nextCursor: null });

  backend.handlers.push(
    http.get(apiUrl('/api/engagement/items'), async ({ request }) => {
      await record(request);
      return page(backend.items);
    }),
    http.post(apiUrl('/api/engagement/items'), async ({ request }) => {
      const { body } = await record(request);
      const input = body as { itemKind: 'reply' | 'comment'; pastedText: string; postId?: string | null; targetUrl?: string | null };
      const now = stamp();
      const entry: ItemEntry = {
        item: {
          id: `eng-${String(backend.items.length + 2).padStart(4, '0')}`,
          schemaVersion: 1,
          createdAt: now,
          updatedAt: now,
          expiresAt: new Date(Date.now() + 48 * 3_600_000).toISOString(),
          itemKind: input.itemKind,
          status: 'open',
          statusChangedAt: now,
          postId: input.postId ?? null,
          targetUrl: input.targetUrl ?? null,
          pastedText: input.pastedText,
          angles: [],
          servedBy: [],
        },
        etag: nextEtag('eng'),
      };
      backend.items = [entry, ...backend.items];
      return HttpResponse.json(entry, { status: 201 });
    }),
    http.post(apiUrl('/api/engagement/items/:id/angles'), async ({ request, params }) => {
      await record(request);
      const current = findItem(String(params.id));
      if (!current) return gone();
      const answer = backend.angles(current);
      if (answer instanceof Response) return answer;
      const updated: ItemEntry = {
        item: { ...current.item, angles: answer, updatedAt: stamp(), servedBy: [{ step: 'angles' } as never] },
        etag: nextEtag('eng'),
      };
      backend.items = backend.items.map((entry) => (entry.item.id === current.item.id ? updated : entry));
      return HttpResponse.json(updated);
    }),
    http.post(apiUrl('/api/engagement/items/:id/lint'), async ({ request, params }) => {
      const { body } = await record(request);
      if (!findItem(String(params.id))) return gone();
      const answer = backend.lint((body as { draftText: string }).draftText);
      return answer instanceof Response ? answer : HttpResponse.json({ findings: answer });
    }),
    http.post(apiUrl('/api/engagement/items/:id/finalize'), async ({ request, params }) => {
      const call = await record(request);
      const current = findItem(String(params.id));
      if (!current) return gone();
      const early = backend.beforeFinalize(current);
      if (early) return early;
      if (call.ifMatch !== current.etag) return stale(current);
      const input = call.body as { finalText: string; url?: string | null; watchEntryId?: string | null };
      const saved: EngagementRecord = {
        id: `rec-${current.item.id}`,
        schemaVersion: 1,
        createdAt: stamp(),
        updatedAt: stamp(),
        itemKind: current.item.itemKind,
        finalText: input.finalText,
        url: input.url ?? null,
        postId: current.item.postId ?? null,
        postedAt: stamp(),
        substantive: true,
        watchEntryId: input.watchEntryId ?? null,
      };
      backend.records = [saved, ...backend.records];
      backend.items = backend.items.filter((entry) => entry.item.id !== current.item.id);
      return HttpResponse.json({ item: saved, etag: nextEtag('rec') }, { status: 201 });
    }),
    http.post(apiUrl('/api/engagement/items/:id/dismiss'), async ({ request, params }) => {
      const call = await record(request);
      const current = findItem(String(params.id));
      if (!current) return gone();
      if (call.ifMatch !== current.etag) return stale(current);
      backend.items = backend.items.filter((entry) => entry.item.id !== current.item.id);
      return new HttpResponse(null, { status: 204 });
    }),
    http.post(apiUrl('/api/engagement/extract'), async ({ request }) => {
      await record(request);
      const answer = backend.extract();
      return answer instanceof Response ? answer : HttpResponse.json(answer);
    }),
    http.get(apiUrl('/api/engagement/watchlist'), async ({ request }) => {
      await record(request);
      return page(backend.watch);
    }),
    http.post(apiUrl('/api/engagement/watchlist'), async ({ request }) => {
      const { body } = await record(request);
      const input = body as { name: string; profileUrl?: string | null; pillar?: string | null; note?: string | null; active: boolean };
      const entry: WatchEntryVersioned = {
        item: {
          id: `watch-${String(backend.watch.length + 2).padStart(4, '0')}`,
          schemaVersion: 1,
          createdAt: stamp(),
          updatedAt: stamp(),
          name: input.name,
          profileUrl: input.profileUrl ?? null,
          pillar: input.pillar ?? null,
          note: input.note ?? null,
          lastEngagedAt: null,
          active: input.active,
        },
        etag: nextEtag('watch'),
      };
      backend.watch = [...backend.watch, entry];
      return HttpResponse.json(entry, { status: 201 });
    }),
    http.put(apiUrl('/api/engagement/watchlist/:id'), async ({ request, params }) => {
      const call = await record(request);
      const current = backend.watch.find((entry) => entry.item.id === String(params.id));
      if (!current) return gone();
      if (call.ifMatch !== current.etag) return stale(current);
      const updated: WatchEntryVersioned = {
        item: { ...current.item, ...(call.body as object), updatedAt: stamp() },
        etag: nextEtag('watch'),
      };
      backend.watch = backend.watch.map((entry) => (entry.item.id === current.item.id ? updated : entry));
      return HttpResponse.json(updated);
    }),
    http.delete(apiUrl('/api/engagement/watchlist/:id'), async ({ request, params }) => {
      const call = await record(request);
      const current = backend.watch.find((entry) => entry.item.id === String(params.id));
      if (!current) return gone();
      if (call.ifMatch !== current.etag) return stale(current);
      backend.watch = backend.watch.filter((entry) => entry.item.id !== current.item.id);
      return new HttpResponse(null, { status: 204 });
    }),
    http.post(apiUrl('/api/engagement/watchlist/:id/engaged'), async ({ request, params }) => {
      await record(request);
      const current = backend.watch.find((entry) => entry.item.id === String(params.id));
      if (!current) return gone();
      const updated: WatchEntryVersioned = {
        item: { ...current.item, lastEngagedAt: stamp(), updatedAt: stamp() },
        etag: nextEtag('watch'),
      };
      backend.watch = backend.watch.map((entry) => (entry.item.id === current.item.id ? updated : entry));
      return HttpResponse.json(updated);
    }),
    http.get(apiUrl('/api/engagement/records'), async ({ request }) => {
      await record(request);
      return page(backend.records.map((item) => ({ item, etag: `"${item.id}"` })));
    }),
    http.get(apiUrl('/api/kb/pillars'), () =>
      HttpResponse.json({
        item: {
          id: 'pillars',
          schemaVersion: 1,
          createdAt: stamp(),
          updatedAt: stamp(),
          pillars: [
            { id: 'agents', name: 'AI agents & security', topics: '', description: '', audience: '', targetShare: 0.34 },
            { id: 'identity', name: 'Identity modernization', topics: '', description: '', audience: '', targetShare: 0.33 },
            { id: 'governance', name: 'AI governance', topics: '', description: '', audience: '', targetShare: 0.33 },
          ],
        },
        etag: '"pillars-1"',
      }),
    ),
    http.get(apiUrl('/api/posts/:id'), ({ params }) => HttpResponse.json(postEntry({ id: String(params.id) }))),
  );
  return backend;
}
