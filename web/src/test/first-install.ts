import { http, HttpResponse, type HttpHandler } from 'msw';

import { calendarServer } from '@/features/calendar/testing/handlers';
import { insightsReads } from '@/features/insights/testing/handlers';
import { overview } from '@/features/insights/testing/fixtures';
import {
  brandKit,
  employerPolicy,
  formatPacks,
  guideView,
  laneSettings,
  ownerInputs,
  pillarSettings,
  versioned,
  MACHINES as LIBRARY_MACHINES,
} from '@/features/library/testing/fixtures';
import { blocklist, cadence, connection, modelsView } from '@/features/settings/testing/fixtures';
import { settingsReads } from '@/features/settings/testing/handlers';
import { gatesViewFixture } from '@/features/studio/draft/testing/fixtures';
import { MACHINES as STUDIO_MACHINES } from '@/features/studio/piece/testing/fixtures';
import { studioDefaultHandlers } from '@/features/studio/piece/testing/handlers';
import { goLiveView, makePost } from '@/features/studio/posts/testing/fixtures';
import { quietTodayHandlers } from '@/features/today/testing/handlers';

import { apiUrl } from './msw';
import { machinesResponse } from './machines';

/**
 * What a first install answers: every area's first reads with nothing saved yet (no plan, no
 * pieces, no sources, no jobs, LinkedIn not connected). The shell tests (`src/app/routes.test.tsx`,
 * `src/components/shell/navigation.test.tsx`) install these so each page shows its honest empty
 * state, and no request goes unanswered. Each area's own tests use its `testing/` helpers.
 */
export type Area = 'today' | 'calendar' | 'studio' | 'library' | 'settings' | 'insights';

const emptyPage = () => HttpResponse.json({ items: [], nextCursor: null });

function today(): HttpHandler[] {
  return quietTodayHandlers();
}

function calendar(): HttpHandler[] {
  return calendarServer().handlers;
}

function studio(): HttpHandler[] {
  return [
    ...studioDefaultHandlers(),
    // The Interview, Draft and Go-live tabs' first reads.
    http.get(apiUrl('/api/interviews'), emptyPage),
    http.get(apiUrl('/api/articles/:articleId/gates'), () => HttpResponse.json(gatesViewFixture([], { report: null }))),
    http.get(apiUrl('/api/posts/:postId/go-live'), ({ params }) =>
      HttpResponse.json(goLiveView(makePost({ id: String(params.postId), status: 'draft', approval: null }))),
    ),
  ];
}

function library(): HttpHandler[] {
  const json = (path: string, body: unknown) => http.get(apiUrl(path), () => HttpResponse.json(body as never));
  return [
    // Singletons never saved: the defaults, with no etag (the first save sends If-None-Match: *).
    json('/api/kb/pillars', versioned(pillarSettings(), null)),
    json('/api/kb/lanes', versioned(laneSettings(), null)),
    json('/api/kb/formats', versioned(formatPacks(), null)),
    json('/api/kb/brand', versioned(brandKit(), null)),
    json('/api/kb/policy', versioned(employerPolicy({ configured: false }), null)),
    json('/api/kb/inputs', ownerInputs(0, 0)),
    json('/api/voice/guide', guideView({ linkedinSamples: 0 })),
    json('/api/meta/machines', machinesResponse(LIBRARY_MACHINES)),
    http.get(apiUrl('/api/sources'), emptyPage),
    http.get(apiUrl('/api/voice/samples'), emptyPage),
    http.get(apiUrl('/api/voice/digests'), emptyPage),
    http.get(apiUrl('/api/stories'), emptyPage),
    http.get(apiUrl('/api/povs'), emptyPage),
  ];
}

function settings(): HttpHandler[] {
  return [
    // In front of the defaults: msw answers with the first handler that matches.
    http.get(apiUrl('/api/kb/models'), () => HttpResponse.json(modelsView({ routes: [] }))),
    http.get(apiUrl('/api/kb/cadence'), () => HttpResponse.json(cadence(null))),
    http.get(apiUrl('/api/sources'), emptyPage),
    http.get(apiUrl('/api/linkedin/connection'), () =>
      HttpResponse.json(
        connection({
          status: 'disconnected',
          memberUrn: null,
          connectedAt: null,
          expiresAt: null,
          tokenDay: null,
          lastIntrospectedAt: null,
          scopes: [],
        }),
      ),
    ),
    http.get(apiUrl('/api/kb/blocklist'), () => HttpResponse.json(blocklist([], false))),
    http.get(apiUrl('/api/jobs'), emptyPage),
    ...settingsReads(),
  ];
}

function insights(): HttpHandler[] {
  return [
    http.get(apiUrl('/api/analytics/overview'), () => HttpResponse.json(overview({ posts: 0, rows: [] }))),
    http.get(apiUrl('/api/analytics/experiments'), emptyPage),
    http.get(apiUrl('/api/analytics/suggestions'), emptyPage),
    http.get(apiUrl('/api/posts'), emptyPage),
    ...insightsReads(),
  ];
}

const AREAS: Readonly<Record<Area, () => HttpHandler[]>> = { today, calendar, studio, library, settings, insights };

/** The first-install answers for one area. */
export function firstInstallHandlers(area: Area): HttpHandler[] {
  return AREAS[area]();
}

/** The area a path belongs to (Insights is a Settings tab for now). */
export function areaOf(path: string): Area | undefined {
  const pathname = path.split(/[?#]/, 1)[0] ?? '';
  if (pathname.startsWith('/settings/insights')) return 'insights';
  const area = /^\/(today|calendar|studio|library|settings)(?:\/|$)/.exec(pathname)?.[1];
  return area as Area | undefined;
}

/**
 * Every area at once, for tests that move between areas. Where two areas read the same route
 * the first answer wins, so the state machines come first as one table (the server serves them
 * all from one route).
 */
export function allFirstInstallHandlers(): HttpHandler[] {
  return [
    http.get(apiUrl('/api/meta/machines'), () => HttpResponse.json(machinesResponse({ ...LIBRARY_MACHINES, ...STUDIO_MACHINES }))),
    ...today(),
    ...studio(),
    ...calendar(),
    ...library(),
    ...insights(),
    ...settings(),
  ];
}
