import { http, HttpResponse, type HttpHandler } from 'msw';

import { apiUrl, server } from '@/test/msw';

import {
  autonomyView,
  backupsView,
  blocklist,
  cadence,
  connection,
  formats,
  jobView,
  modelsView,
  ownerInputs,
  page,
  pillars,
  policy,
  profileChecklist,
  schedules,
  source,
  spendView,
  systemStatus,
} from './fixtures';
import { machinesResponse } from '@/test/machines';

/**
 * Default answers for every read the Settings sections make, so a page renders without a test
 * having to know all of them. Tests override single routes with `server.use(...)` after these.
 */
export function settingsReads(): HttpHandler[] {
  return [
    http.get(apiUrl('/api/system/status'), () => HttpResponse.json(systemStatus())),
    http.get(apiUrl('/api/kb/models'), () => HttpResponse.json(modelsView())),
    http.get(apiUrl('/api/analytics/spend'), () => HttpResponse.json(spendView())),
    http.get(apiUrl('/api/kb/cadence'), () => HttpResponse.json(cadence())),
    http.get(apiUrl('/api/schedules'), () => HttpResponse.json(schedules())),
    http.get(apiUrl('/api/meta/machines'), () =>
      HttpResponse.json(
        machinesResponse({
          source: { active: ['paused', 'failing'], failing: ['active', 'paused'], paused: ['active'] },
          experiment: { draft: ['running', 'abandoned'], running: ['concluded', 'abandoned'], concluded: [], abandoned: [] },
        }),
      ),
    ),
    http.get(apiUrl('/api/kb/pillars'), () => HttpResponse.json(pillars())),
    http.get(apiUrl('/api/kb/formats'), () => HttpResponse.json(formats())),
    http.get(apiUrl('/api/sources'), () =>
      HttpResponse.json(
        page([
          source(),
          source({
            id: 'feed-0002',
            type: 'scout_query',
            title: 'MCP security',
            url: null,
            siteUrl: null,
            query: 'model context protocol security',
            pillar: 'agents',
            status: 'failing',
            consecutiveFailures: 5,
            lastError: 'http_404',
          }),
        ]),
      ),
    ),
    http.get(apiUrl('/api/linkedin/connection'), () => HttpResponse.json(connection())),
    http.get(apiUrl('/api/engagement/profile-checklist'), () => HttpResponse.json(profileChecklist())),
    http.get(apiUrl('/api/push/key'), () => HttpResponse.json({ applicationServerKey: 'BKey' })),
    http.get(apiUrl('/api/kb/blocklist'), () => HttpResponse.json(blocklist([{ id: 't-1', term: 'Contoso', category: 'client', match: 'word' }]))),
    http.get(apiUrl('/api/kb/policy'), () => HttpResponse.json(policy())),
    http.get(apiUrl('/api/kb/autonomy'), () => HttpResponse.json(autonomyView())),
    http.get(apiUrl('/api/kb/inputs'), () => HttpResponse.json(ownerInputs())),
    http.get(apiUrl('/api/jobs'), () => HttpResponse.json(page([{ item: jobView(), etag: '"job-1"' }]))),
    http.get(apiUrl('/api/jobs/:id'), ({ params }) => HttpResponse.json(jobView({ jobId: String(params.id) }))),
    http.get(apiUrl('/api/backups'), () => HttpResponse.json(backupsView())),
    http.get(apiUrl('/api/evals/topics'), () => HttpResponse.json(page([]))),
    http.get(apiUrl('/api/evals/results'), () => HttpResponse.json(page([]))),
  ];
}

/**
 * Installs the default reads, then `overrides` in front of them (msw answers with the first
 * matching handler, and each `server.use` call goes before the earlier ones).
 */
export function serveSettings(...overrides: HttpHandler[]): void {
  server.use(...settingsReads());
  if (overrides.length > 0) server.use(...overrides);
}
