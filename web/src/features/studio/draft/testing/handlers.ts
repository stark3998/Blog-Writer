import { http, HttpResponse, type HttpHandler } from 'msw';

import type { components } from '@/lib/api/schema';
import { apiUrl } from '@/test/msw';

import type { Article, ArticleSaved, Finding, GatesView, VersionSummary } from '../model/types';

import { ARTICLE_ID, briefFixture, FORMATS, gatesViewFixture, MACHINES, PILLARS } from './fixtures';

/**
 * An in-memory article server for the Draft tab's tests: the article with its etag and
 * version, the reads the tab makes around it, and a record of every write. Tests replace one
 * route with `server.use(...)` (msw takes the newest handler first).
 */

type Problem = Record<string, unknown>;

export function problemResponse(status: number, code: string, extensions: Problem = {}, requestId = 'req-draft-1'): Response {
  return HttpResponse.json(
    { type: 'about:blank', title: 'Error', status, code, request_id: requestId, ...extensions },
    { status, headers: { 'Content-Type': 'application/problem+json', 'X-Request-Id': requestId } },
  );
}

export interface RecordedSave {
  readonly ifMatch: string | null;
  readonly body: components['schemas']['ArticleSaveIn'];
}

export interface DraftServer {
  article: Article;
  etag: string;
  gates: GatesView;
  lintFindings: Finding[];
  versions: VersionSummary[];
  readonly saves: RecordedSave[];
  readonly writes: { path: string; ifMatch: string | null; body: unknown }[];
  readonly handlers: HttpHandler[];
  /** Another tab (or the pipeline) saves a newer version. */
  bumpVersion: (markdown: string, by?: Article['updatedBy']) => void;
}

let etagCounter = 100;

function nextEtag(): string {
  etagCounter += 1;
  return `"etag-${String(etagCounter)}"`;
}

export function createDraftServer(initial: Article, id = ARTICLE_ID): DraftServer {
  const base = `/api/articles/${id}`;
  const server: DraftServer = {
    article: initial,
    etag: '"etag-7"',
    gates: gatesViewFixture(),
    lintFindings: [],
    versions: [],
    saves: [],
    writes: [],
    handlers: [],
    bumpVersion: (markdown, by = 'owner') => {
      server.article = { ...server.article, markdown, version: server.article.version + 1, updatedBy: by };
      server.etag = nextEtag();
    },
  };

  const versioned = () => HttpResponse.json({ item: server.article, etag: server.etag }, { headers: { ETag: server.etag } });

  const recordWrite = async (request: Request, path: string) => {
    const text = await request.text();
    server.writes.push({ path, ifMatch: request.headers.get('If-Match'), body: text === '' ? null : (JSON.parse(text) as unknown) });
  };

  server.handlers.push(
    http.get(apiUrl(base), versioned),
    http.put(apiUrl(base), async ({ request }) => {
      const ifMatch = request.headers.get('If-Match');
      const body = (await request.json()) as components['schemas']['ArticleSaveIn'];
      server.saves.push({ ifMatch, body });
      if (ifMatch === null) return problemResponse(428, 'if_match_required');
      if (ifMatch !== server.etag && body.baseVersion !== server.article.version) {
        return problemResponse(412, 'etag_mismatch', {
          current: {
            etag: server.etag,
            updatedAt: server.article.updatedAt,
            updatedBy: server.article.updatedBy,
            version: server.article.version,
            status: server.article.status,
          },
        });
      }
      server.article = {
        ...server.article,
        markdown: body.markdown,
        meta: { ...body.meta, tags: body.meta.tags ?? [] },
        version: server.article.version + 1,
        updatedAt: '2026-11-26T15:05:00.000000Z',
        updatedBy: 'owner',
      };
      server.etag = nextEtag();
      const saved: ArticleSaved = { item: server.article, etag: server.etag, findings: [] };
      return HttpResponse.json(saved, { headers: { ETag: server.etag } });
    }),
    http.get(apiUrl(`${base}/gates`), () => HttpResponse.json(server.gates)),
    http.get(apiUrl(`${base}/versions`), () => HttpResponse.json({ items: server.versions.map((item) => ({ item, etag: null })), nextCursor: null })),
    http.get(apiUrl(`${base}/publish-status`), () =>
      HttpResponse.json({ status: server.article.status, publish: server.article.publish ?? { live: false, auto: false }, job: null }),
    ),
    http.get(apiUrl('/api/meta/machines'), () => HttpResponse.json(MACHINES)),
    http.get(apiUrl('/api/kb/pillars'), () => HttpResponse.json(PILLARS)),
    http.get(apiUrl('/api/kb/formats'), () => HttpResponse.json(FORMATS)),
    http.get(apiUrl('/api/research/briefs/:briefId'), () => HttpResponse.json({ item: briefFixture(), etag: '"brief-1"' })),
    http.post(apiUrl('/api/gates/lint'), () => HttpResponse.json({ findings: server.lintFindings })),
    http.post(apiUrl(`${base}/transition`), async ({ request }) => {
      await recordWrite(request.clone(), 'transition');
      const body = (await request.json()) as { to: Article['status'] };
      server.article = { ...server.article, status: body.to };
      server.etag = nextEtag();
      return versioned();
    }),
  );
  return server;
}
