import { screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { server } from '@/test/msw';
import { findPageHeading, renderApp } from '@/test/render-app';

import { NEXT_WEEK, TEST_NOW, THIS_WEEK, weekPlan } from './testing/fixtures';
import { calendarServer, problemResponse, type CalendarServerOptions } from './testing/handlers';

function serve(options: CalendarServerOptions = {}) {
  const fake = calendarServer(options);
  server.use(...fake.handlers);
  return fake;
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(new Date(TEST_NOW));
});

afterEach(() => {
  vi.useRealTimers();
});

const PLANS = [weekPlan({ weekStart: THIS_WEEK, status: 'approved' }), weekPlan({ mode: 'light' })];

function week(name: RegExp) {
  return screen.findByRole('radiogroup', { name });
}

describe('Calendar > Availability', () => {
  it('shows this week and the next five, with their modes and plans', async () => {
    serve({ plans: PLANS });
    renderApp('/calendar/availability');
    await findPageHeading('Calendar');

    const intro = await screen.findByRole('heading', { level: 2, name: 'Set how much you can give each week.' });
    expect(within(intro.closest('section') ?? document.body).getByText('Your part')).toBeInTheDocument();

    const weeks = await screen.findAllByRole('radiogroup');
    expect(weeks).toHaveLength(6);
    expect(within(await week(/^This week/)).getByRole('radio', { name: 'Normal' })).toBeChecked();
    expect(within(await week(/^Next week/)).getByRole('radio', { name: 'Light' })).toBeChecked();
    expect(screen.getByRole('link', { name: 'Review the plan' })).toHaveAttribute('href', `/calendar/plan?week=${NEXT_WEEK}`);
    expect(screen.getByText('Not planned yet. The planner proposes it Sun 6 Dec, 17:00.')).toBeInTheDocument();
    const lastWeek = await week(/^Week of 28 Dec/);
    expect(within(lastWeek).getByRole('radio', { name: 'Normal' })).toBeChecked();
    expect(screen.getByText('28 Dec 2026 – 3 Jan 2027')).toBeInTheDocument();
  });

  it('changes a planned week’s mode with If-Match', async () => {
    const fake = serve({ plans: PLANS });
    const { user } = renderApp('/calendar/availability');
    await user.click(within(await week(/^Next week/)).getByRole('radio', { name: 'Away' }));
    await waitFor(() => {
      expect(fake.requests(`PUT /api/plan/weeks/${NEXT_WEEK}/mode`)).toHaveLength(1);
    });
    const [put] = fake.requests(`PUT /api/plan/weeks/${NEXT_WEEK}/mode`);
    expect(put?.body).toEqual({ mode: 'away' });
    expect(put?.ifMatch).toBe('"plan-etag-1"');
    expect(put?.ifNoneMatch).toBeNull();
    expect(await screen.findByText('Next week: Away.')).toBeInTheDocument();
    await waitFor(() => {
      expect(within(screen.getByRole('radiogroup', { name: /^Next week/ })).getByRole('radio', { name: 'Away' })).toBeChecked();
    });
  });

  it('gives a week without a plan one that holds its mode (If-None-Match: *)', async () => {
    const fake = serve({ plans: PLANS });
    const { user } = renderApp('/calendar/availability');
    await user.click(within(await week(/^Week of 14 Dec/)).getByRole('radio', { name: 'Light' }));
    await waitFor(() => {
      expect(fake.requests('PUT /api/plan/weeks/2026-12-14/mode')).toHaveLength(1);
    });
    const [put] = fake.requests('PUT /api/plan/weeks/2026-12-14/mode');
    expect(put?.ifNoneMatch).toBe('*');
    expect(put?.ifMatch).toBeNull();
    await waitFor(() => {
      expect(within(screen.getByRole('radiogroup', { name: /^Week of 14 Dec/ })).getByRole('radio', { name: 'Light' })).toBeChecked();
    });
  });

  it('opens the conflict dialog when the week changed meanwhile', async () => {
    let refused = false;
    const fake = serve({
      plans: PLANS,
      override: (request) => {
        if (request.method === 'PUT' && !refused) {
          refused = true;
          return problemResponse(412, 'etag_mismatch', { current: { etag: '"plan-etag-1"', updatedBy: 'pipeline' } });
        }
        return undefined;
      },
    });
    const { user } = renderApp('/calendar/availability');
    await user.click(within(await week(/^Next week/)).getByRole('radio', { name: 'Normal' }));
    const dialog = await screen.findByRole('alertdialog', { name: 'Next week’s mode changed since you opened it' });
    expect(dialog).toHaveTextContent('The pipeline saved a newer version.');
    await user.click(within(dialog).getByRole('button', { name: 'Keep mine' }));
    await waitFor(() => {
      expect(fake.requests(`PUT /api/plan/weeks/${NEXT_WEEK}/mode`)).toHaveLength(2);
    });
    expect(fake.requests(`PUT /api/plan/weeks/${NEXT_WEEK}/mode`)[1]?.ifMatch).toBe('"plan-etag-1"');
  });

  it('keeps a closed week read-only and gives advice for an approved week going away', async () => {
    serve({ plans: [weekPlan({ weekStart: THIS_WEEK, status: 'closed' }), weekPlan({ status: 'approved', mode: 'away' })] });
    renderApp('/calendar/availability');
    const thisWeek = await week(/^This week/);
    expect(within(thisWeek).getByRole('radio', { name: 'Normal' })).toBeDisabled();
    expect(screen.getByText('This week is closed.')).toBeInTheDocument();
    expect(
      screen.getByText(/The plan is already approved: its posts stay\. If you won’t be there to tap Publish/),
    ).toBeInTheDocument();
  });

  it('falls back to the default cadence when the slot settings can’t be loaded, and says so', async () => {
    let failing = true;
    serve({
      plans: PLANS,
      override: (request) => (failing && request.path === '/api/kb/cadence' ? problemResponse(400, 'bad_request') : undefined),
    });
    const { user } = renderApp('/calendar/availability');
    const notice = await screen.findByText(
      'Couldn’t load your slot settings. The calendar uses the defaults until they load.',
    );
    expect(await screen.findAllByRole('radiogroup')).toHaveLength(6);
    failing = false;
    const alert = notice.closest('[data-slot="alert"]');
    if (!(alert instanceof HTMLElement)) throw new Error('no alert');
    await user.click(within(alert).getByRole('button', { name: 'Try again' }));
    await waitFor(() => {
      expect(screen.queryByText(/Couldn’t load your slot settings/)).not.toBeInTheDocument();
    });
  });

  it('says when the weeks can’t be loaded', async () => {
    serve({ override: (request) => (request.path === '/api/plan/weeks' ? problemResponse(400, 'bad_request') : undefined) });
    renderApp('/calendar/availability');
    expect(await screen.findByRole('alert')).toHaveTextContent('Couldn’t load your weeks.');
  });
});
