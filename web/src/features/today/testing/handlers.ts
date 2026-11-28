import { http, HttpResponse, type HttpHandler } from 'msw';

import { apiUrl } from '@/test/msw';

import { autonomyView, scorecardHistory, systemStatus, todayResponse, type AutonomyView, type SystemStatus, type Today, type WeekScorecard } from './fixtures';

type Answer<T> = T | (() => T | Response);

function answer<T>(value: Answer<T>): Response {
  const result = typeof value === 'function' ? (value as () => T | Response)() : value;
  return result instanceof Response ? result : HttpResponse.json(result as object);
}

export interface TodayServerOptions {
  readonly today?: Answer<Today>;
  readonly status?: Answer<SystemStatus>;
  readonly autonomy?: Answer<AutonomyView>;
  readonly scorecards?: Answer<WeekScorecard[]>;
}

/**
 * The reads Today makes when it opens (`GET /api/today`, status, autonomy, scorecards) plus an
 * empty Engage list, so a page test needs only `server.use(...todayHandlers())`. Pass a value or
 * a function (a `Response` from it answers as is, for errors).
 */
export function todayHandlers(options: TodayServerOptions = {}): HttpHandler[] {
  return [
    http.get(apiUrl('/api/today'), () => answer(options.today ?? todayResponse())),
    http.get(apiUrl('/api/system/status'), () => answer(options.status ?? systemStatus())),
    http.get(apiUrl('/api/kb/autonomy'), () => answer(options.autonomy ?? autonomyView())),
    http.get(apiUrl('/api/plan/scorecard'), () => answer(options.scorecards ?? scorecardHistory())),
    http.get(apiUrl('/api/engagement/items'), () => HttpResponse.json({ items: [], nextCursor: null })),
  ];
}

/** Today with nothing waiting: what the shared route tests can install. */
export function quietTodayHandlers(): HttpHandler[] {
  return todayHandlers({ today: todayResponse({ items: [] }) });
}
