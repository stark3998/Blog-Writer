import { http, HttpResponse, type HttpHandler } from 'msw';

import type { components } from '@/lib/api/schema';
import { apiUrl } from '@/test/msw';

import { heroOf, markdownHeadings, mediaInBody, type ModelsView } from '../model/illustrations';
import type { Article, ArticleSaved, Finding, GatesView, VersionSummary } from '../model/types';

import { ARTICLE_ID, briefFixture, FORMATS, gatesViewFixture, MACHINES, modelsViewFixture, PILLARS } from './fixtures';

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

type JobView = components['schemas']['JobView'];

export interface DraftServer {
  article: Article;
  etag: string;
  gates: GatesView;
  lintFindings: Finding[];
  versions: VersionSummary[];
  /** `GET /api/kb/models`: the image route, the owner's switch and the prices. */
  models: ModelsView;
  /** `GET /api/jobs/{id}` (404 for others). */
  readonly jobs: Map<string, JobView>;
  readonly saves: RecordedSave[];
  readonly writes: { path: string; ifMatch: string | null; body: unknown }[];
  readonly handlers: HttpHandler[];
  /** Another tab (or the pipeline) saves a newer version. */
  bumpVersion: (markdown: string, by?: Article['updatedBy']) => void;
  /** Something else changed the article without a new version (a gates run, the illustrations job). */
  touch: (changes?: Partial<Article>) => void;
}

/** A job record as `GET /api/jobs/{id}` answers it. */
export function jobView(jobId: string, overrides: Partial<JobView> = {}): JobView {
  return {
    jobId,
    type: 'illustrations.generate',
    status: 'queued',
    attempt: 0,
    maxAttempts: 3,
    seq: 1,
    cancelRequested: false,
    createdAt: '2026-11-26T16:00:00.000000Z',
    updatedAt: '2026-11-26T16:00:00.000000Z',
    steps: ['plan', 'generate', 'check', 'store', 'attach'].map((name) => ({ name, status: 'pending', runs: 0 })),
    events: [],
    ...overrides,
  };
}

let etagCounter = 100;

function nextEtag(): string {
  etagCounter += 1;
  return `"etag-${String(etagCounter)}"`;
}

export function createDraftServer(initial: Article, id = ARTICLE_ID): DraftServer {
  const base = `/api/articles/${id}`;
  let clock = 0;
  /** Every write moves `updatedAt` on, like the server's clock. */
  const stamp = () => {
    clock += 1;
    return `2026-11-26T16:${String(clock).padStart(2, '0')}:00.000000Z`;
  };
  const server: DraftServer = {
    article: initial,
    etag: '"etag-7"',
    gates: gatesViewFixture(),
    lintFindings: [],
    versions: [],
    models: modelsViewFixture(),
    jobs: new Map(),
    saves: [],
    writes: [],
    handlers: [],
    bumpVersion: (markdown, by = 'owner') => {
      server.article = { ...server.article, markdown, version: server.article.version + 1, updatedBy: by, updatedAt: stamp() };
      server.etag = nextEtag();
    },
    touch: (changes = {}) => {
      server.article = { ...server.article, ...changes, updatedAt: stamp() };
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
    ...illustrationHandlers(server, base, { recordWrite, stamp, versioned }),
  );
  return server;
}

interface Helpers {
  readonly recordWrite: (request: Request, path: string) => Promise<void>;
  readonly stamp: () => string;
  readonly versioned: () => Response;
}

const OWNER_STATES: readonly Article['status'][] = ['planned', 'in_review', 'needs_attention'];

/** The body with the alt text of the image at `src` replaced. */
function replaceAlt(markdown: string, src: string, altText: string): string {
  const end = markdown.indexOf(`](${src})`);
  const start = end === -1 ? -1 : markdown.lastIndexOf('![', end);
  return start === -1 ? markdown : `${markdown.slice(0, start)}![${altText}${markdown.slice(end)}`;
}
const REVIEW_STATES: readonly Article['status'][] = ['in_review', 'needs_attention'];

/**
 * The illustration routes (`blogwriter.illustrations.routes`) over the draft server's article:
 * a strict If-Match (412 with `current`), the owner-state rule, and the refusals the panel shows.
 */
function illustrationHandlers(server: DraftServer, base: string, { recordWrite, stamp, versioned }: Helpers): HttpHandler[] {
  const stale = () =>
    problemResponse(412, 'etag_mismatch', {
      current: {
        etag: server.etag,
        updatedAt: server.article.updatedAt,
        updatedBy: server.article.updatedBy,
        version: server.article.version,
        status: server.article.status,
      },
    });
  const readOnly = () =>
    problemResponse(409, 'read_only_state', { detail: 'It’s read-only in this state.', state: server.article.status, writer: 'none' });
  const candidate = (id: string) => (server.article.illustrations ?? []).find((item) => item.id === id);
  const write = (changes: Partial<Article>, newVersion = false) => {
    server.article = {
      ...server.article,
      ...changes,
      ...(newVersion ? { version: server.article.version + 1 } : {}),
      updatedAt: stamp(),
      updatedBy: 'owner',
    };
    server.etag = nextEtag();
  };
  const saved = () => {
    const body: ArticleSaved = { item: server.article, etag: server.etag, findings: [] };
    return HttpResponse.json(body, { headers: { ETag: server.etag } });
  };
  /** The common checks of an owner action on a candidate; a response when refused. */
  const guard = (request: Request, id: string, { anyState = false } = {}): Response | undefined => {
    if (request.headers.get('If-Match') !== server.etag) return stale();
    if (anyState ? server.article.status === 'archived' : !OWNER_STATES.includes(server.article.status)) return readOnly();
    if (!candidate(id)) return problemResponse(404, 'not_found');
    return undefined;
  };

  return [
    http.get(apiUrl('/api/kb/models'), () => HttpResponse.json(server.models)),
    http.get(apiUrl('/api/jobs/:jobId'), ({ params }) => {
      const job = server.jobs.get(String(params.jobId));
      return job ? HttpResponse.json(job) : problemResponse(404, 'not_found');
    }),
    http.post(apiUrl(`${base}/images`), async ({ request }) => {
      await recordWrite(request.clone(), 'images');
      if (request.headers.get('If-Match') !== server.etag) return stale();
      const status = server.article.status;
      if (status === 'planned') return problemResponse(409, 'draft_missing', { detail: 'Draft the article first.' });
      if (!REVIEW_STATES.includes(status)) return readOnly();
      if (!server.models.preferences.item.imagesEnabled) {
        return problemResponse(409, 'images_disabled', { detail: 'Switch illustrations on in Settings > Models first.' });
      }
      if (!server.models.routes.some((route) => route.task === 'image')) {
        return problemResponse(503, 'images_unavailable', { detail: 'Illustrations need an image model, and none is configured.' });
      }
      const running = server.article.illustrationJobId ? server.jobs.get(server.article.illustrationJobId) : undefined;
      if (running && ['queued', 'running', 'waiting_input'].includes(running.status)) {
        return problemResponse(409, 'images_in_progress', {
          detail: 'Illustrations are being made for this article. Wait for them.',
          jobId: running.jobId,
        });
      }
      const jobId = `illustrations-${server.article.id}-run${String(server.jobs.size + 1)}`;
      server.jobs.set(jobId, jobView(jobId));
      server.touch({ illustrationJobId: jobId });
      return HttpResponse.json({ article: { item: server.article, etag: server.etag }, jobId }, { status: 202 });
    }),
    http.post(apiUrl(`${base}/images/:illustrationId/hero`), async ({ request, params }) => {
      await recordWrite(request.clone(), 'hero');
      const id = String(params.illustrationId);
      const refused = guard(request, id);
      if (refused) return refused;
      const chosen = candidate(id);
      if (!chosen) return problemResponse(404, 'not_found');
      const cards = new Set((server.article.media ?? []).filter((item) => item.role === 'og_image').map((item) => item.sha256));
      const media = (server.article.media ?? []).filter((item) => item.role !== 'hero_image' || cards.has(item.sha256));
      write({
        media: [...media.filter((item) => item.role !== 'hero_image'), { ...chosen.media, role: 'hero_image', altText: chosen.altText }],
      });
      return versioned();
    }),
    http.delete(apiUrl(`${base}/hero`), async ({ request }) => {
      await recordWrite(request.clone(), 'clear-hero');
      if (request.headers.get('If-Match') !== server.etag) return stale();
      if (!OWNER_STATES.includes(server.article.status)) return readOnly();
      const cards = new Set((server.article.media ?? []).filter((item) => item.role === 'og_image').map((item) => item.sha256));
      write({ media: (server.article.media ?? []).filter((item) => item.role !== 'hero_image' || cards.has(item.sha256)) });
      return versioned();
    }),
    http.post(apiUrl(`${base}/images/:illustrationId/insert`), async ({ request, params }) => {
      await recordWrite(request.clone(), 'insert');
      const id = String(params.illustrationId);
      const refused = guard(request, id);
      if (refused) return refused;
      const chosen = candidate(id);
      if (!chosen) return problemResponse(404, 'not_found');
      const { heading } = (await request.json()) as { heading: string };
      if (mediaInBody(server.article.markdown, chosen.media.name)) {
        return problemResponse(409, 'illustration_inserted', { detail: 'The draft already shows this image.' });
      }
      const lines = server.article.markdown.split('\n');
      const target = markdownHeadings(server.article.markdown).find((item) => item.source === heading);
      const at = target ? lines.findIndex((line) => line.replace(/^#+\s+/, '').trim() === target.source) : -1;
      if (at === -1) {
        return problemResponse(422, 'heading_not_found', { detail: 'No section heading has that text. Pick one from the draft.' });
      }
      lines.splice(at + 1, 0, '', `![${chosen.altText}](/api/media/${chosen.media.name})`);
      write({ markdown: lines.join('\n') }, true);
      return saved();
    }),
    http.put(apiUrl(`${base}/images/:illustrationId/alt`), async ({ request, params }) => {
      await recordWrite(request.clone(), 'alt');
      const id = String(params.illustrationId);
      const refused = guard(request, id);
      if (refused) return refused;
      const chosen = candidate(id);
      if (!chosen) return problemResponse(404, 'not_found');
      const { altText } = (await request.json()) as { altText: string };
      if (altText.trim() === '') return problemResponse(422, 'invalid_alt_text', { detail: 'Describe the image in a few words.' });
      const inBody = mediaInBody(server.article.markdown, chosen.media.name);
      const src = `/api/media/${chosen.media.name}`;
      write(
        {
          illustrations: (server.article.illustrations ?? []).map((item) => (item.id === id ? { ...item, altText } : item)),
          media: (server.article.media ?? []).map((item) =>
            item.role === 'hero_image' && item.name === chosen.media.name ? { ...item, altText } : item,
          ),
          markdown: inBody ? replaceAlt(server.article.markdown, src, altText) : server.article.markdown,
        },
        inBody,
      );
      return saved();
    }),
    http.delete(apiUrl(`${base}/images/:illustrationId`), async ({ request, params }) => {
      await recordWrite(request.clone(), 'discard');
      const id = String(params.illustrationId);
      const refused = guard(request, id, { anyState: true });
      if (refused) return refused;
      const chosen = candidate(id);
      if (!chosen) return problemResponse(404, 'not_found');
      if (heroOf(server.article).image?.name === chosen.media.name || mediaInBody(server.article.markdown, chosen.media.name)) {
        return problemResponse(409, 'illustration_in_use', {
          detail: 'It is the hero image or in the draft. Clear the hero or remove it first.',
        });
      }
      write({ illustrations: (server.article.illustrations ?? []).filter((item) => item.id !== id) });
      return versioned();
    }),
  ];
}
