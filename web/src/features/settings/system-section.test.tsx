import { screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';

import { apiUrl, problem } from '@/test/msw';
import { findPageHeading, renderApp } from '@/test/render-app';

import { dumpHref, formatBytes } from './system/backups-panel';
import { schedulesLook } from './system/status-panel';
import { evalResult, evalTopic, jobView, page, systemStatus } from './testing/fixtures';
import { serveSettings } from './testing/handlers';

async function open() {
  const view = renderApp('/settings/system');
  await findPageHeading('Settings');
  return view;
}

const region = (name: string) => screen.getByRole('region', { name });

describe('Settings > System: status, jobs and alerts', () => {
  it('leaves safe mode after asking', async () => {
    let status = systemStatus({ safeMode: true, boot: { boots: 4, safeMode: true, reason: 'crash_loop' } });
    serveSettings(
      http.get(apiUrl('/api/system/status'), () => HttpResponse.json(status)),
      http.post(apiUrl('/api/system/safe-mode/exit'), () => {
        status = systemStatus();
        return HttpResponse.json(status);
      }),
    );
    const { user } = await open();
    const block = region('Status');
    expect(await within(block).findByText('Safe mode: nothing runs in the background.')).toBeInTheDocument();
    await user.click(within(block).getByRole('button', { name: 'Leave safe mode' }));
    const confirm = await screen.findByRole('alertdialog', { name: 'Leave safe mode?' });
    await user.click(within(confirm).getByRole('button', { name: 'Leave safe mode' }));
    await waitFor(() => {
      expect(within(block).queryByText('Safe mode: nothing runs in the background.')).not.toBeInTheDocument();
    });
  });

  it('lists every service with its state in words', async () => {
    serveSettings();
    await open();
    const services = await within(region('Status')).findByRole('list', { name: 'Services' });
    const rendering = within(services).getByText('Rendering').closest('li');
    expect(rendering).toHaveTextContent('The renderer’s browser is missing, so carousels and images can’t render.');
    expect(rendering).toHaveTextContent('Needs attention');
  });

  it('says when storage is degraded and when the schedules are out of date', async () => {
    const status = systemStatus();
    serveSettings(
      http.get(apiUrl('/api/system/status'), () =>
        HttpResponse.json(
          systemStatus({
            runner: { running: true, claimed: 0, schedulerRunning: false },
            services: { ...status.services, storage: { state: 'degraded', reason: 'unreachable' } },
            schedules: { state: 'out_of_date', reason: 'reseed_failed', seededAt: null, retrying: true },
          }),
        ),
      ),
    );
    await open();
    const block = region('Status');
    const services = await within(block).findByRole('list', { name: 'Services' });
    const storage = within(services).getByText('File storage').closest('li');
    expect(storage).toHaveTextContent('Storage isn’t answering. Uploads, media and renders wait until it does; nothing is lost.');
    expect(storage).toHaveTextContent('Needs attention');
    expect(within(block).getByText('Out of date')).toBeInTheDocument();
    // The scheduler is held until the update lands: nothing earlier can run meanwhile.
    expect(
      within(block).getByText('The last update failed and is being retried. Scheduled jobs wait until it lands.'),
    ).toBeInTheDocument();
    expect(within(block).queryByText(/earlier times may still run/)).not.toBeInTheDocument();
  });

  it('says what a failed schedule update means while the scheduler runs and while it is held', () => {
    const failed = { state: 'out_of_date', reason: 'reseed_failed', seededAt: null, retrying: true } as const;
    expect(schedulesLook(failed, true).sentence).toBe(
      'The last update failed and is being retried. Until it lands, earlier times may still run.',
    );
    expect(schedulesLook(failed, false).sentence).toBe(
      'The last update failed and is being retried. Scheduled jobs wait until it lands.',
    );
    expect(schedulesLook({ ...failed, reason: 'not_seeded', retrying: false }, false).sentence).toBe(
      'Being brought up to date after the restart. The scheduler starts once they match your cadence.',
    );
  });

  it.each([
    [true, 'Safe mode is off. The runner and the scheduler are running again.'],
    [false, 'Safe mode is off. The runner is running again; scheduled jobs wait until the schedules are up to date.'],
  ])('says whether the scheduler runs after safe mode (running: %s)', async (schedulerRunning, toast) => {
    serveSettings(
      http.get(apiUrl('/api/system/status'), () =>
        HttpResponse.json(systemStatus({ safeMode: true, boot: { boots: 4, safeMode: true, reason: 'crash_loop' } })),
      ),
      http.post(apiUrl('/api/system/safe-mode/exit'), () =>
        HttpResponse.json(
          systemStatus({
            runner: { running: true, claimed: 0, schedulerRunning },
            schedules: schedulerRunning
              ? systemStatus().schedules
              : { state: 'out_of_date', reason: 'reseed_failed', seededAt: null, retrying: true },
          }),
        ),
      ),
    );
    const { user } = await open();
    const block = region('Status');
    const other = schedulerRunning
      ? 'Safe mode is off. The runner is running again; scheduled jobs wait until the schedules are up to date.'
      : 'Safe mode is off. The runner and the scheduler are running again.';
    // Toasts outlive a test (the toaster is global): count what is there before leaving.
    const shown = screen.queryAllByText(toast).length;
    const otherShown = screen.queryAllByText(other).length;
    await user.click(await within(block).findByRole('button', { name: 'Leave safe mode' }));
    const confirm = await screen.findByRole('alertdialog', { name: 'Leave safe mode?' });
    await user.click(within(confirm).getByRole('button', { name: 'Leave safe mode' }));
    await waitFor(() => {
      expect(screen.queryAllByText(toast).length).toBeGreaterThan(shown);
    });
    expect(screen.queryAllByText(other)).toHaveLength(otherShown);
  });

  it('says the schedules match the cadence', async () => {
    serveSettings();
    await open();
    const block = region('Status');
    expect(await within(block).findByText('Up to date')).toBeInTheDocument();
    expect(within(block).getByText(/Match your cadence since/)).toBeInTheDocument();
  });

  it('opens a job’s steps and retries a failed one', async () => {
    let retried: string | undefined;
    const failed = jobView({
      jobId: 'job-backup-1',
      type: 'backups.dump',
      status: 'failed',
      error: { code: 'storage_unavailable', step: 'dump', detail: 'Storage is not configured.', at: '2026-11-27T03:31:00.000000Z' },
      steps: [{ name: 'dump', status: 'failed', runs: 3, startedAt: null, finishedAt: null }],
    });
    serveSettings(
      http.get(apiUrl('/api/jobs'), () => HttpResponse.json(page([{ item: failed, etag: '"j1"' }]))),
      http.get(apiUrl('/api/jobs/:id'), () => HttpResponse.json(failed)),
      http.post(apiUrl('/api/jobs/:id/retry'), ({ params }) => {
        retried = String(params.id);
        return HttpResponse.json({ ...failed, status: 'queued', error: null });
      }),
    );
    const { user } = await open();
    const jobs = await within(region('Jobs')).findByRole('list', { name: 'Jobs' });
    await user.click(within(jobs).getByRole('button', { name: /Backup, Failed/ }));
    expect(await within(jobs).findByText('Stopped: Storage is not configured.')).toBeInTheDocument();
    await user.click(within(jobs).getByRole('button', { name: 'Retry' }));
    await waitFor(() => {
      expect(retried).toBe('job-backup-1');
    });
  });

  it('asks before cancelling a running job', async () => {
    let cancelled = false;
    const running = jobView({ jobId: 'job-run-1', type: 'pipeline.article', status: 'running', finishedAt: null });
    serveSettings(
      http.get(apiUrl('/api/jobs'), () => HttpResponse.json(page([{ item: running, etag: '"j2"' }]))),
      http.get(apiUrl('/api/jobs/:id'), () => HttpResponse.json(running)),
      http.post(apiUrl('/api/jobs/:id/cancel'), () => {
        cancelled = true;
        return HttpResponse.json({ ...running, cancelRequested: true });
      }),
    );
    const { user } = await open();
    const jobs = await within(region('Jobs')).findByRole('list', { name: 'Jobs' });
    await user.click(within(jobs).getByRole('button', { name: /Article pipeline, Running/ }));
    await user.click(await within(jobs).findByRole('button', { name: 'Cancel' }));
    const confirm = await screen.findByRole('alertdialog', { name: 'Cancel “Article pipeline”?' });
    await user.click(within(confirm).getByRole('button', { name: 'Cancel the job' }));
    await waitFor(() => {
      expect(cancelled).toBe(true);
    });
  });

  it('filters the jobs by status', async () => {
    const statuses: (string | null)[] = [];
    serveSettings(
      http.get(apiUrl('/api/jobs'), ({ request }) => {
        statuses.push(new URL(request.url).searchParams.get('status'));
        return HttpResponse.json(page([]));
      }),
    );
    const { user } = await open();
    const block = region('Jobs');
    expect(await within(block).findByText('No jobs have run yet.')).toBeInTheDocument();
    await user.click(within(block).getByRole('combobox', { name: 'Status' }));
    await user.click(await screen.findByRole('option', { name: 'Failed' }));
    expect(await within(block).findByText('No jobs match these filters.')).toBeInTheDocument();
    expect(statuses).toContain('failed');
  });

  it('sends a test alert with a test notification', async () => {
    let body: unknown;
    serveSettings(
      http.post(apiUrl('/api/system/test-alert'), async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ alert: 'sent', push: { delivered: 1, failed: 0, expired: 0 } }, { status: 202 });
      }),
    );
    const { user } = await open();
    const block = region('Alerts');
    await user.click(within(block).getByRole('button', { name: 'Send a test alert' }));
    expect(await within(block).findByRole('status')).toHaveTextContent('Notifications: 1 delivered, 0 failed.');
    expect(body).toEqual({ push: true });
  });
});

describe('Settings > System: evaluation set', () => {
  it('shows the topic grid and adds a missing topic', async () => {
    let created: unknown;
    serveSettings(
      http.get(apiUrl('/api/evals/topics'), () => HttpResponse.json(page([evalTopic()]))),
      http.post(apiUrl('/api/evals/topics'), async ({ request }) => {
        created = await request.json();
        return HttpResponse.json(evalTopic({ id: 'topic-2', pillar: 'identity', format: 'framework' }), { status: 201 });
      }),
    );
    const { user } = await open();
    const block = region('Evaluation set');
    expect(await within(block).findByText('Locking down an MCP server in a week')).toBeInTheDocument();
    expect(within(block).getByText('1 active topic of the 12 the set calls for.', { exact: false })).toBeInTheDocument();

    await user.click(within(block).getByRole('button', { name: 'Add a Identity modernization Framework topic' }));
    const dialog = await screen.findByRole('dialog', { name: 'Add a topic' });
    await user.type(within(dialog).getByRole('textbox', { name: /Topic/ }), 'Moving 2,000 apps off Okta');
    await user.click(within(dialog).getByRole('button', { name: 'Add the topic' }));
    await waitFor(() => {
      expect(created).toEqual({ pillar: 'identity', format: 'framework', topic: 'Moving 2,000 apps off Okta', active: true });
    });
  });

  it('explains why a run cannot start yet', async () => {
    serveSettings(
      http.get(apiUrl('/api/evals/topics'), () => HttpResponse.json(page([evalTopic()]))),
      http.post(apiUrl('/api/evals/runs'), () => problem(409, 'voice_guide_missing')),
    );
    const { user } = await open();
    const block = region('Evaluation set');
    await user.click(await within(block).findByRole('button', { name: 'Run the evaluation' }));
    expect(await within(block).findByText(/Activate a voice guide first/)).toBeInTheDocument();
  });

  it('labels the latest run’s drafts with their versions', async () => {
    let sent: { ifMatch: string | null; body: unknown } | undefined;
    serveSettings(
      http.get(apiUrl('/api/evals/topics'), () => HttpResponse.json(page([evalTopic()]))),
      http.get(apiUrl('/api/evals/results'), () => HttpResponse.json(page([evalResult()]))),
      http.post(apiUrl('/api/evals/results/:id/label'), async ({ request }) => {
        sent = { ifMatch: request.headers.get('If-Match'), body: await request.json() };
        return HttpResponse.json(evalResult({ ownerLabel: 'as_is' }));
      }),
    );
    const { user } = await open();
    const drafts = await within(region('Evaluation set')).findByRole('list', { name: 'Evaluation drafts' });
    expect(within(drafts).getByText('Judge passed')).toBeInTheDocument();
    expect(within(drafts).getByRole('link', { name: 'Read the draft' })).toHaveAttribute('href', '/studio/art-eval-1/draft');
    await user.click(within(drafts).getByRole('radio', { name: 'As is' }));
    await waitFor(() => {
      expect(sent).toEqual({ ifMatch: '"evr-1-1"', body: { label: 'as_is' } });
    });
  });
});

describe('Settings > System: backups and the data export', () => {
  it('lists the downloads and records a restore drill', async () => {
    let drilled = false;
    serveSettings(
      http.post(apiUrl('/api/backups/restore-drill'), () => {
        drilled = true;
        return HttpResponse.json({ item: {}, etag: '"b"' });
      }),
    );
    const { user } = await open();
    const block = region('Backups and your data');
    expect(await within(block).findByText(/Last backup .*: 160 documents in 2 containers\./)).toBeInTheDocument();
    const files = within(block).getAllByRole('list', { name: /Files of/ })[0];
    if (!files) throw new Error('no files list');
    expect(within(files).getByRole('link', { name: /articles/ })).toHaveAttribute('href', '/api/backups/2026-11-27/articles');
    expect(within(block).getByText('A restore drill is due.')).toBeInTheDocument();
    await user.click(within(block).getByRole('button', { name: 'I did the restore drill' }));
    const confirm = await screen.findByRole('alertdialog', { name: 'Record the restore drill?' });
    await user.click(within(confirm).getByRole('button', { name: 'Record the drill' }));
    await waitFor(() => {
      expect(drilled).toBe(true);
    });
  });

  it('says when storage is not set up', async () => {
    serveSettings(http.get(apiUrl('/api/backups'), () => problem(503, 'storage_unavailable')));
    await open();
    expect(
      await within(region('Backups and your data')).findByText('File storage isn’t set up, so backups and the data export are off.'),
    ).toBeInTheDocument();
  });

  it('formats sizes and download links', () => {
    expect(formatBytes(512)).toBe('512 bytes');
    expect(formatBytes(840_000)).toBe('840 KB');
    expect(formatBytes(1_200_000)).toBe('1.2 MB');
    expect(dumpHref('2026-11-27', 'article-versions')).toBe('/api/backups/2026-11-27/article-versions');
  });
});
