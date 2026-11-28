import { http, HttpResponse } from 'msw';

import type { components } from '@/lib/api/schema';

import {
  articleSummary,
  at,
  cadenceSettings,
  formatPacks,
  laneSettings,
  MACHINES,
  NY,
  pillarSettings,
  STAMP,
  versioned,
} from './fixtures';
import { machinesResponse } from '@/test/machines';

/**
 * A small in-memory planner, ideas inbox and intake for the calendar's tests: msw handlers over
 * documents the test seeds, which change as the screens write (so refetches show the result),
 * and a log of what was sent (bodies and preconditions) for the assertions.
 */

type Schemas = components['schemas'];
type WeekPlan = Schemas['WeekPlan'];
type PostSummary = Schemas['PostSummary'];
type Idea = Schemas['Idea'];

/** jsdom's origin in Vitest (same as `@/test/msw`). */
const ORIGIN = 'http://localhost:3000';
const url = (path: string) => new URL(path, ORIGIN).href;

/** A problem+json answer with extension members (`findings`, `current`, `errors`). */
export function problemResponse(status: number, code: string, extensions: Record<string, unknown> = {}, detail?: string) {
  return HttpResponse.json(
    { type: 'about:blank', title: 'Error', status, code, request_id: 'req-cal-1', ...(detail ? { detail } : {}), ...extensions },
    { status, headers: { 'Content-Type': 'application/problem+json', 'X-Request-Id': 'req-cal-1' } },
  );
}

export interface SentRequest {
  readonly method: string;
  readonly path: string;
  readonly body: unknown;
  readonly ifMatch: string | null;
  readonly ifNoneMatch: string | null;
  readonly query: URLSearchParams;
}

export interface CalendarServerOptions {
  /** Plans by week start; a week left out is a 404. */
  plans?: readonly WeekPlan[];
  posts?: readonly PostSummary[];
  articles?: readonly Schemas['ArticleSummary'][];
  ideas?: readonly Idea[];
  cadence?: Schemas['CadenceSettings'];
  /** Answers a route in a special way (a problem, a delay); return undefined for the default. */
  override?: (request: SentRequest) => Response | undefined | Promise<Response | undefined>;
}

export interface CalendarServer {
  readonly handlers: ReturnType<typeof http.get>[];
  readonly sent: SentRequest[];
  /** The documents as they are now. */
  readonly plans: Map<string, { plan: WeekPlan; etag: string }>;
  readonly ideas: Map<string, { idea: Idea; etag: string }>;
  readonly posts: PostSummary[];
  /** The requests to one method and path pattern (`'PUT /api/plan/weeks/2026-11-30'`). */
  readonly requests: (prefix: string) => SentRequest[];
}

let etagCounter = 0;
function nextEtag(): string {
  etagCounter += 1;
  return `"etag-${String(etagCounter)}-${String(Date.now())}"`;
}

function page(items: readonly { item: unknown; etag: string | null }[]) {
  return { items, nextCursor: null };
}

export function calendarServer(options: CalendarServerOptions = {}): CalendarServer {
  const plans = new Map<string, { plan: WeekPlan; etag: string }>();
  for (const plan of options.plans ?? []) plans.set(plan.weekStart, { plan, etag: '"plan-etag-1"' });
  const ideas = new Map<string, { idea: Idea; etag: string }>();
  for (const idea of options.ideas ?? []) ideas.set(idea.id, { idea, etag: `"${idea.id}-etag-1"` });
  const posts = [...(options.posts ?? [])];
  const articles = [...(options.articles ?? [])];
  const sent: SentRequest[] = [];

  async function record(request: Request, path: string): Promise<SentRequest> {
    let body: unknown;
    try {
      body = request.method === 'GET' ? undefined : await request.clone().json();
    } catch {
      body = undefined;
    }
    const entry: SentRequest = {
      method: request.method,
      path,
      body,
      ifMatch: request.headers.get('If-Match'),
      ifNoneMatch: request.headers.get('If-None-Match'),
      query: new URL(request.url).searchParams,
    };
    sent.push(entry);
    return entry;
  }

  /** Records the request, then the test's override answers it, or the default does. */
  function route(
    method: 'get' | 'post' | 'put',
    path: string,
    answer: (sentRequest: SentRequest, params: Record<string, string>) => Response | Promise<Response>,
  ) {
    return http[method](url(path), async ({ request, params }) => {
      const actual = new URL(request.url).pathname;
      const entry = await record(request, actual);
      const custom = await options.override?.(entry);
      if (custom) return custom;
      return answer(entry, params as Record<string, string>);
    });
  }

  const planAnswer = (weekStart: string) => {
    const stored = plans.get(weekStart);
    if (!stored) return problemResponse(404, 'not_found');
    return HttpResponse.json(versioned(stored.plan, stored.etag), { headers: { ETag: stored.etag } });
  };

  const handlers = [
    route('get', '/api/kb/cadence', () => HttpResponse.json(versioned(options.cadence ?? cadenceSettings(), null))),
    route('get', '/api/kb/pillars', () => HttpResponse.json(versioned(pillarSettings(), null))),
    route('get', '/api/kb/lanes', () => HttpResponse.json(versioned(laneSettings(), null))),
    route('get', '/api/kb/formats', () => HttpResponse.json(versioned(formatPacks(), null))),
    route('get', '/api/meta/machines', () => HttpResponse.json(machinesResponse(MACHINES))),

    route('get', '/api/plan/weeks', (sentRequest) => {
      const from = sentRequest.query.get('from') ?? '';
      const items = [...plans.values()]
        .filter(({ plan }) => plan.weekStart >= from)
        .sort((a, b) => a.plan.weekStart.localeCompare(b.plan.weekStart))
        .map(({ plan, etag }) => ({ item: plan, etag }));
      return HttpResponse.json(page(items));
    }),
    route('get', '/api/plan/weeks/:week', (_, params) => planAnswer(params.week ?? '')),
    route('put', '/api/plan/weeks/:week', (sentRequest, params) => {
      const week = params.week ?? '';
      const stored = plans.get(week);
      if (!stored) return problemResponse(404, 'not_found');
      if (sentRequest.ifMatch !== stored.etag) {
        return problemResponse(412, 'etag_mismatch', { current: { etag: stored.etag, updatedAt: STAMP, updatedBy: 'owner' } });
      }
      const body = sentRequest.body as Schemas['PlanEditIn'];
      const previous = new Map((stored.plan.slots ?? []).map((slot) => [slot.id, slot]));
      const slots = body.slots.map((input) => {
        const old = previous.get(input.id);
        return {
          ...old,
          id: input.id,
          slot: { localDate: input.localDate, localTime: input.localTime, timezone: NY, utc: at(input.localDate, input.localTime), kind: 'custom' as const },
          postId: input.postId ?? old?.postId ?? null,
          lane: input.lane !== undefined ? input.lane : (old?.lane ?? null),
          pillar: input.pillar !== undefined ? input.pillar : (old?.pillar ?? null),
          format: input.format !== undefined ? input.format : (old?.format ?? null),
          title: input.title !== undefined ? input.title : (old?.title ?? null),
        };
      });
      slots.sort((a, b) => a.slot.utc.localeCompare(b.slot.utc));
      const flagship =
        'flagship' in body
          ? body.flagship
            ? { ...body.flagship, articleId: stored.plan.flagship?.articleId ?? null, publishAt: stored.plan.flagship?.publishAt ?? null }
            : null
          : stored.plan.flagship;
      const plan: WeekPlan = { ...stored.plan, slots, flagship: flagship ?? null, mode: body.mode ?? stored.plan.mode };
      const etag = nextEtag();
      plans.set(week, { plan, etag });
      return HttpResponse.json(versioned(plan, etag), { headers: { ETag: etag } });
    }),
    route('post', '/api/plan/weeks/:week/approve', (sentRequest, params) => {
      const week = params.week ?? '';
      const stored = plans.get(week);
      if (!stored) return problemResponse(404, 'not_found');
      if (sentRequest.ifMatch !== stored.etag) {
        return problemResponse(412, 'etag_mismatch', { current: { etag: stored.etag, updatedAt: STAMP, updatedBy: 'pipeline' } });
      }
      const plan: WeekPlan = { ...stored.plan, status: 'approved', approvedAt: '2026-11-29T23:12:00.000000Z' };
      const etag = nextEtag();
      plans.set(week, { plan, etag });
      return HttpResponse.json({
        plan: versioned(plan, etag),
        articleId: plan.flagship ? 'art-flagship' : null,
        // The real job ids say nothing about what they are: the posts started come as `postIds`.
        jobIds: ['pipeline-art-flagship-1', 'job-post-a', 'job-post-b'],
        postIds: ['post-a', 'post-b'],
      });
    }),
    route('post', '/api/plan/weeks/:week/generate', (_, params) =>
      HttpResponse.json({ jobId: `planweek-${params.week ?? ''}`, status: 'queued' }, { status: 202 }),
    ),
    route('put', '/api/plan/weeks/:week/mode', (sentRequest, params) => {
      const week = params.week ?? '';
      const stored = plans.get(week);
      const body = sentRequest.body as Schemas['blogwriter__planner__models__ModeIn'];
      if (!stored) {
        if (sentRequest.ifNoneMatch !== '*') return problemResponse(428, 'if_match_required');
        const plan: WeekPlan = {
          id: `plan-${week}`,
          schemaVersion: 1,
          createdAt: STAMP,
          updatedAt: STAMP,
          weekStart: week,
          timezone: NY,
          mode: body.mode,
          status: 'generating',
          statusChangedAt: STAMP,
          slots: [],
          warnings: [],
          jobId: null,
        };
        const etag = nextEtag();
        plans.set(week, { plan, etag });
        return HttpResponse.json(versioned(plan, etag), { headers: { ETag: etag } });
      }
      if (sentRequest.ifMatch !== stored.etag) {
        return problemResponse(412, 'etag_mismatch', { current: { etag: stored.etag, updatedAt: STAMP, updatedBy: 'system' } });
      }
      const plan: WeekPlan = { ...stored.plan, mode: body.mode };
      const etag = nextEtag();
      plans.set(week, { plan, etag });
      return HttpResponse.json(versioned(plan, etag), { headers: { ETag: etag } });
    }),

    route('get', '/api/posts', () => HttpResponse.json(page(posts.map((post) => ({ item: post, etag: `"${post.id}-etag"` }))))),
    route('post', '/api/posts/:id/reslot', (_, params) => {
      const index = posts.findIndex((post) => post.id === params.id);
      const post = posts[index];
      if (!post) return problemResponse(404, 'not_found');
      const date = '2026-12-02';
      const moved: PostSummary = {
        ...post,
        status: 'approved',
        slot: { localDate: date, localTime: '10:00', timezone: NY, utc: at(date, '10:00'), kind: 'primary' },
      };
      posts[index] = moved;
      return HttpResponse.json(versioned({ ...moved, schemaVersion: 1, createdAt: STAMP, updatedAt: STAMP }, '"moved"'));
    }),
    route('post', '/api/posts', (sentRequest) => {
      const body = sentRequest.body as Schemas['PostCreateIn'];
      // Like the server: a post for a planned slot takes the slot's link, and the plan's slot names
      // the post without the plan changing status.
      const linked = body.planSlot ? plans.get(body.planSlot.weekStart) : undefined;
      if (body.planSlot && linked) {
        const slotId = body.planSlot.slotId;
        const slots = (linked.plan.slots ?? []).map((slot) => (slot.id === slotId ? { ...slot, postId: 'post-quick-1' } : slot));
        plans.set(body.planSlot.weekStart, { plan: { ...linked.plan, slots }, etag: `${linked.etag}-linked` });
      }
      const post = {
        id: 'post-quick-1',
        schemaVersion: 1,
        createdAt: STAMP,
        updatedAt: STAMP,
        status: 'generating' as const,
        statusChangedAt: STAMP,
        lane: body.lane,
        pillar: body.pillar,
        format: body.format,
        displayText: '',
        payload: '',
        goLiveMode: 'one_click' as const,
        timeSensitive: false,
        reviewOnly: false,
        approvalCurrent: false,
        weekPlanId: linked ? linked.plan.id : null,
        planSlotId: body.planSlot?.slotId ?? null,
      };
      return HttpResponse.json({ post: versioned(post, '"post-etag"'), jobId: 'job-post-1' }, { status: 202 });
    }),

    route('get', '/api/articles', () =>
      HttpResponse.json(page(articles.map((article) => ({ item: article, etag: `"${article.id}-etag"` })))),
    ),
    route('post', '/api/articles', (sentRequest) => {
      const body = sentRequest.body as Schemas['ArticleIn'];
      const article = articleSummary({ id: 'art-new-1', ideaId: body.ideaId ?? null });
      return HttpResponse.json(versioned({ ...article, meta: { ...article.meta, tags: [] } }, '"art-etag"'), { status: 201 });
    }),

    route('get', '/api/ideas', (sentRequest) => {
      const statuses = sentRequest.query.getAll('status');
      const pillar = sentRequest.query.get('pillar');
      const lane = sentRequest.query.get('lane');
      const items = [...ideas.values()]
        .filter(({ idea }) => (statuses.length === 0 ? ['new', 'accepted'] : statuses).includes(idea.status))
        .filter(({ idea }) => !pillar || idea.pillar === pillar)
        .filter(({ idea }) => !lane || idea.suggestedLane === lane)
        .map(({ idea, etag }) => ({ item: idea, etag }));
      return HttpResponse.json(page(items));
    }),
    route('get', '/api/ideas/:id', (_, params) => {
      const stored = ideas.get(params.id ?? '');
      if (!stored) return problemResponse(404, 'not_found');
      return HttpResponse.json(versioned(stored.idea, stored.etag));
    }),
    route('post', '/api/ideas', (sentRequest) => {
      const body = sentRequest.body as Schemas['IdeaIn'];
      const idea: Idea = {
        id: `idea-new-${String(ideas.size + 1)}`,
        schemaVersion: 1,
        createdAt: STAMP,
        updatedAt: STAMP,
        status: 'new',
        statusChangedAt: STAMP,
        origin: 'quick_capture',
        title: body.title,
        note: body.note ?? null,
        url: body.url ?? null,
        pillar: body.pillar ?? null,
        suggestedLane: body.suggestedLane ?? null,
        timeSensitive: body.timeSensitive,
      };
      const etag = nextEtag();
      ideas.set(idea.id, { idea, etag });
      return HttpResponse.json(versioned(idea, etag), { status: 201 });
    }),
    route('put', '/api/ideas/:id', (sentRequest, params) => {
      const stored = ideas.get(params.id ?? '');
      if (!stored) return problemResponse(404, 'not_found');
      if (sentRequest.ifMatch !== stored.etag) {
        return problemResponse(412, 'etag_mismatch', { current: { etag: stored.etag, updatedAt: STAMP, updatedBy: 'owner' } });
      }
      const idea: Idea = { ...stored.idea, ...(sentRequest.body as Partial<Idea>) };
      const etag = nextEtag();
      ideas.set(idea.id, { idea, etag });
      return HttpResponse.json(versioned(idea, etag));
    }),
    route('post', '/api/ideas/:id/transition', (sentRequest, params) => {
      const stored = ideas.get(params.id ?? '');
      if (!stored) return problemResponse(404, 'not_found');
      if (sentRequest.ifMatch !== stored.etag) {
        return problemResponse(412, 'etag_mismatch', { current: { etag: stored.etag } });
      }
      const body = sentRequest.body as Schemas['IdeaTransitionIn'];
      const idea: Idea = { ...stored.idea, status: body.to, snoozedUntil: body.snoozedUntil ?? null };
      const etag = nextEtag();
      ideas.set(idea.id, { idea, etag });
      return HttpResponse.json(versioned(idea, etag));
    }),

    route('post', '/api/stories/intake/redact', (sentRequest) => {
      const body = sentRequest.body as Schemas['RedactIn'];
      return HttpResponse.json({
        findings: [],
        proposal: body.text.replace(/Contoso/g, 'a large client'),
        reason: null,
        anonymizerUsed: true,
      });
    }),
    route('post', '/api/stories/intake/check', () => HttpResponse.json({ ok: true, findings: [] })),
    route('post', '/api/povs', (sentRequest) => {
      const body = sentRequest.body as Schemas['PovIn'];
      return HttpResponse.json(
        versioned({
          id: 'pov-1',
          schemaVersion: 1,
          createdAt: STAMP,
          updatedAt: STAMP,
          status: 'approved',
          statusChangedAt: STAMP,
          origin: 'idea_take',
          pillar: body.pillar,
          statement: body.statement,
          confidentiality: 'anonymized',
        }),
        { status: 201 },
      );
    }),

    route('get', '/api/jobs/:id', (_, params) =>
      HttpResponse.json({
        jobId: params.id ?? '',
        type: 'planner.plan-week',
        status: 'running',
        attempt: 1,
        maxAttempts: 3,
        seq: 1,
        cancelRequested: false,
        createdAt: STAMP,
        updatedAt: STAMP,
        steps: [],
      }),
    ),
  ];

  return {
    handlers,
    sent,
    plans,
    ideas,
    posts,
    requests: (prefix) => sent.filter((entry) => `${entry.method} ${entry.path}`.startsWith(prefix)),
  };
}
