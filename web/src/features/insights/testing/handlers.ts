import { http, HttpResponse, type HttpHandler } from 'msw';

import { apiUrl, server } from '@/test/msw';

import { pillars } from '../../settings/testing/fixtures';
import { experiment, overview, post, postSummary, spendView, suggestion } from './fixtures';
import { machinesResponse } from '@/test/machines';

const page = <T,>(items: T[]) => ({ items, nextCursor: null });

/** Default answers for every read the Insights section makes. */
export function insightsReads(): HttpHandler[] {
  return [
    http.get(apiUrl('/api/analytics/overview'), () => HttpResponse.json(overview())),
    http.get(apiUrl('/api/analytics/spend'), () => HttpResponse.json(spendView())),
    http.get(apiUrl('/api/analytics/experiments'), () => HttpResponse.json(page([experiment()]))),
    http.get(apiUrl('/api/analytics/suggestions'), () => HttpResponse.json(page([suggestion()]))),
    http.get(apiUrl('/api/analytics/imports'), () => HttpResponse.json(page([]))),
    http.get(apiUrl('/api/posts'), () =>
      HttpResponse.json(
        page([
          postSummary({ id: 'post-2', hook: 'Old post', postedAt: '2026-10-01T10:00:00.000000Z' }),
          postSummary(),
        ]),
      ),
    ),
    http.get(apiUrl('/api/posts/:id'), ({ params }) => HttpResponse.json(post({ id: String(params.id) }))),
    http.get(apiUrl('/api/kb/pillars'), () => HttpResponse.json(pillars())),
    http.get(apiUrl('/api/meta/machines'), () =>
      HttpResponse.json(
        machinesResponse({
          experiment: { draft: ['running', 'abandoned'], running: ['concluded', 'abandoned'], concluded: [], abandoned: [] },
        }),
      ),
    ),
  ];
}

/** Installs the default reads, then `overrides` in front of them. */
export function serveInsights(...overrides: HttpHandler[]): void {
  server.use(...insightsReads());
  if (overrides.length > 0) server.use(...overrides);
}
