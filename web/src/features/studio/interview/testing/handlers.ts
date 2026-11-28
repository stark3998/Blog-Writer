import { http, HttpResponse } from 'msw';

import type { components } from '@/lib/api/schema';
import { apiUrl, problem } from '@/test/msw';

import type { InterviewEntry } from '../interview-model';

import { articleFixture, MACHINES } from './fixtures';
import { machinesResponse } from '@/test/machines';

type AnswersBody = components['schemas']['AnswersIn'];
type Article = components['schemas']['Article'];

export interface AnswerCall {
  readonly interviewId: string;
  readonly body: AnswersBody;
  readonly ifMatch: string | null;
}

export interface SkipCall {
  readonly interviewId: string;
  readonly ifMatch: string | null;
}

export interface InterviewHandlerOptions {
  /** What `GET /api/interviews` lists (newest first). Answers and skips update it. */
  interviews?: InterviewEntry[];
  /** `GET /api/articles/{id}`; `null` answers 404. */
  article?: Article | null;
  /** Overrides the answers route (return `undefined` for the default: the interview becomes answered). */
  answers?: (call: AnswerCall, count: number) => Response | undefined;
  /** Overrides the skip route. */
  skip?: (call: SkipCall, count: number) => Response | undefined;
  /** Overrides the list route. */
  list?: (count: number) => Response | undefined;
}

const STAMP = '2026-11-25T09:30:00.000000Z';

/**
 * msw handlers for the interview tab: the list, one interview, answers and skip (with If-Match
 * recorded), the article and `/api/meta/machines`. The routes behave like the server's: a stale
 * If-Match is a 412 with `current`, answers move the interview to `answered`.
 */
export function interviewHandlers(options: InterviewHandlerOptions = {}) {
  let entries = [...(options.interviews ?? [])];
  const calls = { answers: [] as AnswerCall[], skip: [] as SkipCall[], lists: [] as URLSearchParams[] };
  let version = 1;

  const replace = (next: InterviewEntry) => {
    entries = entries.map((entry) => (entry.item.id === next.item.id ? next : entry));
    return next;
  };
  const find = (id: string) => entries.find((entry) => entry.item.id === id);
  const stale = (entry: InterviewEntry) =>
    HttpResponse.json(
      {
        type: 'about:blank',
        title: 'Error',
        status: 412,
        code: 'etag_mismatch',
        request_id: 'req-412',
        current: { etag: entry.etag, updatedAt: entry.item.updatedAt, status: entry.item.status },
      },
      { status: 412, headers: { 'Content-Type': 'application/problem+json', 'X-Request-Id': 'req-412' } },
    );

  const handlers = [
    http.get(apiUrl('/api/meta/machines'), () => HttpResponse.json(machinesResponse(MACHINES))),
    http.get(apiUrl('/api/articles/:id'), ({ params }) => {
      const article = options.article === undefined ? articleFixture({ id: String(params.id) }) : options.article;
      return article ? HttpResponse.json({ item: article, etag: '"article-1"' }) : problem(404, 'not_found');
    }),
    http.get(apiUrl('/api/interviews'), ({ request }) => {
      calls.lists.push(new URL(request.url).searchParams);
      const custom = options.list?.(calls.lists.length);
      return custom ?? HttpResponse.json({ items: entries, nextCursor: null });
    }),
    http.get(apiUrl('/api/interviews/:id'), ({ params }) => {
      const entry = find(String(params.id));
      return entry ? HttpResponse.json(entry, { headers: { ETag: entry.etag ?? '' } }) : problem(404, 'not_found');
    }),
    http.post(apiUrl('/api/interviews/:id/answers'), async ({ request, params }) => {
      const call: AnswerCall = {
        interviewId: String(params.id),
        body: (await request.json()) as AnswersBody,
        ifMatch: request.headers.get('If-Match'),
      };
      calls.answers.push(call);
      const custom = options.answers?.(call, calls.answers.length);
      if (custom) return custom;
      const entry = find(call.interviewId);
      if (!entry) return problem(404, 'not_found');
      if (call.ifMatch === null) return problem(428, 'if_match_required');
      if (call.ifMatch !== entry.etag) return stale(entry);
      version += 1;
      const answered = replace({
        item: {
          ...entry.item,
          status: 'answered',
          statusChangedAt: STAMP,
          updatedAt: STAMP,
          answers: call.body.answers.map((answer) => ({ ...answer, confirmedAt: STAMP })),
        },
        etag: `"etag-${String(version)}"`,
      });
      return HttpResponse.json(answered);
    }),
    http.post(apiUrl('/api/interviews/:id/skip'), ({ request, params }) => {
      const call: SkipCall = { interviewId: String(params.id), ifMatch: request.headers.get('If-Match') };
      calls.skip.push(call);
      const custom = options.skip?.(call, calls.skip.length);
      if (custom) return custom;
      const entry = find(call.interviewId);
      if (!entry) return problem(404, 'not_found');
      if (call.ifMatch !== entry.etag) return stale(entry);
      version += 1;
      return HttpResponse.json(
        replace({
          item: { ...entry.item, status: 'skipped', statusChangedAt: STAMP, updatedAt: STAMP },
          etag: `"etag-${String(version)}"`,
        }),
      );
    }),
  ];

  return {
    handlers,
    calls,
    /** Changes what the server holds (another device answered, the expiry job ran). */
    set(next: InterviewEntry[]) {
      entries = [...next];
    },
  };
}
