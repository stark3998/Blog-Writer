import { act, screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { toast } from 'sonner';
import { afterEach, describe, expect, it } from 'vitest';

import { createEngagementBackend } from '@/features/engagement/testing/handlers';
import { apiUrl, problem, server } from '@/test/msw';
import { findPageHeading, renderApp } from '@/test/render-app';

import { autonomyView, busyItems, inMinutes, systemStatus, todayItem, todayResponse, todayWeek } from './testing/fixtures';
import { todayHandlers, type TodayServerOptions } from './testing/handlers';

// Toasts live in a module-level store that replays them to the next test's toaster.
afterEach(() => {
  act(() => {
    toast.dismiss();
  });
});

function setup(options: TodayServerOptions = {}) {
  const backend = createEngagementBackend();
  server.use(...backend.handlers, ...todayHandlers(options));
  return backend;
}

async function inbox() {
  return screen.findByRole('list', { name: 'What needs you, most urgent first' });
}

describe('Today: the inbox', () => {
  it('leads with how much needs the owner and puts the most urgent item first', async () => {
    setup();
    renderApp('/today');
    await findPageHeading('Today');

    expect(await screen.findByRole('heading', { level: 2, name: 'Five things need you.' })).toBeInTheDocument();
    expect(
      screen.getByText('1 post to publish, 1 to review and approve, 1 interview to answer, 1 reply to write and 1 set of numbers to log.'),
    ).toBeInTheDocument();

    const rows = within(await inbox()).getAllByRole('listitem');
    expect(rows).toHaveLength(5);
    const [first] = rows;
    if (!first) throw new Error('no first card');
    expect(within(first).getByText('Up next')).toBeInTheDocument();
    expect(within(first).getByRole('heading', { level: 3, name: 'Post at 10:00 AM: tap to publish' })).toBeInTheDocument();
    expect(within(first).getByText('Urgent')).toBeInTheDocument();
    // The handler builds its times per request, a moment after the page read the clock.
    expect(within(first).getByText(/^Due in 2[456] min$/)).toBeInTheDocument();
    // One action, named with its card, that opens the go-live page (publishing needs a fresh tap there).
    expect(within(first).getByRole('link', { name: 'Go live: Post at 10:00 AM: tap to publish' })).toHaveAttribute(
      'href',
      '/studio/post-1/go-live?post=post-1',
    );
  });

  it('says when an item is overdue, in words as well as colour', async () => {
    setup();
    renderApp('/today');
    const row = (await screen.findByRole('heading', { level: 3, name: 'Answer 2 quick questions' })).closest('li');
    if (!row) throw new Error('no row');
    expect(within(row).getByText('Overdue by 1 h').closest('p')).toHaveClass('text-danger');
    expect(within(row).getByRole('link', { name: 'Answer: Answer 2 quick questions' })).toHaveAttribute('href', '/studio/art-1/interview');
  });

  it('asks once to schedule an away week’s kit posts in LinkedIn, opening the first kit', async () => {
    setup({
      today: todayResponse({
        items: [
          todayItem({
            id: 'schedule_kit:plan-2026-11-30',
            kind: 'schedule_kit',
            title: 'Away week of Nov 30: schedule 2 posts in LinkedIn',
            href: '/studio/post-7/go-live?post=post-7',
            priority: 'high',
            dueAt: inMinutes(60 * 24),
            refs: { postId: 'post-7', weekStart: '2026-11-30' },
          }),
        ],
      }),
    });
    renderApp('/today');
    const row = (await screen.findByRole('heading', { level: 3, name: 'Away week of Nov 30: schedule 2 posts in LinkedIn' })).closest('li');
    if (!row) throw new Error('no row');
    expect(within(row).getByText('Away week')).toBeInTheDocument();
    expect(
      within(row).getByRole('link', { name: 'Schedule them in LinkedIn: Away week of Nov 30: schedule 2 posts in LinkedIn' }),
    ).toHaveAttribute('href', '/studio/post-7/go-live?post=post-7');
    expect(screen.getByText('1 to sort out.')).toBeInTheDocument();
  });

  it('lists setup items quietly after the cards', async () => {
    setup();
    renderApp('/today');
    const setupHeading = await screen.findByRole('heading', { level: 3, name: 'Finish setting up' });
    const section = setupHeading.closest('section');
    if (!section) throw new Error('no setup section');
    expect(within(section).getByRole('link', { name: 'Save your blocklist' })).toHaveAttribute('href', '/settings/policy');
  });

  it('dismisses a reminder in place and keeps focus on the inbox', async () => {
    const backend = setup();
    let dismissed = false;
    server.use(
      http.post(apiUrl('/api/reminders/:id/dismiss'), ({ params }) => {
        dismissed = params.id === 'rem-snap-1';
        return HttpResponse.json({ item: { id: params.id }, etag: '"r2"' });
      }),
      http.get(apiUrl('/api/today'), () =>
        HttpResponse.json(todayResponse({ items: dismissed ? busyItems().filter((item) => item.kind !== 'snapshot') : busyItems() })),
      ),
    );
    const { user } = renderApp('/today');
    await user.click(await screen.findByRole('button', { name: 'Dismiss: Log the 48-hour numbers for your post' }));

    expect(await screen.findByText('Reminder dismissed.')).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.queryByRole('heading', { name: 'Log the 48-hour numbers for your post' })).not.toBeInTheDocument();
    });
    expect(dismissed).toBe(true);
    expect(screen.getByRole('heading', { level: 2, name: 'Four things need you.' })).toHaveFocus();
    expect(backend.calls).toEqual(expect.any(Array));
  });

  it('opens the Engage panel in place for a reply reminder, replying on that post', async () => {
    setup();
    const { user, router } = renderApp('/today', { viewport: 'mobile' });
    await user.click(await screen.findByRole('button', { name: 'Reply: Live for 15 min: reply to comments on your post' }));

    const sheet = await screen.findByRole('dialog', { name: 'Reply helper' });
    expect(sheet).toHaveClass('bottom-0');
    expect(within(sheet).getByRole('radio', { name: 'Reply to a comment' })).toBeChecked();
    expect(await within(sheet).findByText(/Agents need identities, not shared keys\./)).toBeInTheDocument();
    expect(router.state.location.pathname).toBe('/today');
  });

  it('shows the empty state with the four kinds when nothing waits', async () => {
    setup({ today: todayResponse({ items: [] }) });
    renderApp('/today');
    const empty = await screen.findByRole('heading', { level: 2, name: 'Nothing needs you right now.' });
    const section = empty.closest('section');
    if (!section) throw new Error('no section');
    expect(within(section).getByText('Your part')).toBeInTheDocument();
    expect(within(section).getByText('Approve')).toBeInTheDocument();
  });

  it('shows the error with a request id and recovers on Try again', async () => {
    let healthy = false;
    setup({ today: () => (healthy ? todayResponse() : problem(500, 'internal_error', 'req-today-9')) });
    const { user } = renderApp('/today');
    const alert = await screen.findByRole('alert');
    expect(within(alert).getByText('Couldn’t load what needs you.')).toBeInTheDocument();
    expect(within(alert).getByText('req-today-9')).toBeInTheDocument();

    healthy = true;
    await user.click(within(alert).getByRole('button', { name: 'Try loading Today again' }));
    expect(await screen.findByRole('heading', { level: 2, name: 'Five things need you.' })).toBeInTheDocument();
  });
});

describe('Today: notices', () => {
  it('shows safe mode, the brake, the pause switch and a LinkedIn reconnect, each with one action', async () => {
    setup({
      status: systemStatus({
        safeMode: true,
        brake: { spendTodayUsd: 7.5, thresholdUsd: 5, engaged: true },
        expiry: { linkedinTokenDay: 52, patExpiresAt: inMinutes(60 * 24 * 5) },
      }),
      autonomy: autonomyView({ paused: true }),
      today: todayResponse({
        items: [
          todayItem({
            id: 'reconnect_linkedin:linkedin-connection',
            kind: 'reconnect_linkedin',
            title: 'LinkedIn token day 52: reconnect before day 60',
            href: '/settings/integrations',
            priority: 'high',
          }),
        ],
      }),
    });
    renderApp('/today');
    const notices = (await screen.findByRole('heading', { level: 2, name: 'Notices' })).closest('section');
    if (!notices) throw new Error('no notices');
    const box = within(notices);
    expect(await box.findByText('Safe mode is on')).toBeInTheDocument();
    expect(box.getByRole('link', { name: 'Check the jobs' })).toHaveAttribute('href', '/settings/system');
    expect(box.getByText('LinkedIn token day 52: reconnect before day 60')).toBeInTheDocument();
    expect(box.getByText(/this connection is on day 52/)).toBeInTheDocument();
    expect(box.getByRole('link', { name: 'Reconnect LinkedIn' })).toHaveAttribute('href', '/settings/integrations');
    expect(box.getByText('The spending brake is on')).toBeInTheDocument();
    expect(box.getByText(/\$7\.50.*\$5\.00/)).toBeInTheDocument();
    expect(await box.findByText('Scheduled work is paused')).toBeInTheDocument();
    expect(box.getByText('The portfolio token expires in 5 days')).toBeInTheDocument();
    // Standing notices are not read out as alerts when the page opens.
    expect(within(notices).queryByRole('alert')).not.toBeInTheDocument();
    // The reconnect item is a notice, not a card.
    expect(screen.queryByRole('list', { name: 'What needs you, most urgent first' })).not.toBeInTheDocument();
  });

  it('offers a retry when the status cannot be read', async () => {
    setup({ status: () => problem(503, 'store_unavailable') });
    renderApp('/today');
    expect(await screen.findByText('Couldn’t check safe mode and the spending brake.', {}, { timeout: 4000 })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Check again' })).toBeInTheDocument();
  });
});

describe('Today: the week', () => {
  it('shows the mode, the plan, the scorecard against last week and the time budget', async () => {
    setup({ today: todayResponse({ week: todayWeek({ mode: 'light', planStatus: 'proposed' }) }) });
    renderApp('/today');
    // The heading shows while the week loads; wait for the week itself.
    const week = (await screen.findByText('Waiting for your approval')).closest('section');
    if (!week) throw new Error('no week section');
    const box = within(week);
    expect(box.getByText('Light')).toBeInTheDocument();
    expect(box.getByRole('link', { name: 'Open the plan' })).toHaveAttribute('href', '/calendar/plan');

    const replies = box.getByText('Replies').closest('dl');
    if (!replies) throw new Error('no replies tile');
    expect(within(replies).getByText('4')).toBeInTheDocument();
    expect(await within(replies).findByText('+2')).toBeInTheDocument();
    expect(within(replies).getByText('vs all of last week')).toBeInTheDocument();
    const pillar = box.getByText('On a pillar').closest('dl');
    if (!pillar) throw new Error('no pillar tile');
    expect(within(pillar).getByText('100%')).toBeInTheDocument();
    expect(within(pillar).getByText('+33 pts')).toBeInTheDocument();
    expect(box.getByRole('heading', { level: 2, name: 'Your week' })).toBeInTheDocument();
    expect(box.getByText('About 2½ to 3 hours a week in total.')).toBeInTheDocument();
  });

  it('keeps the time budget when Today cannot load', async () => {
    setup({ today: () => problem(500, 'internal_error') });
    renderApp('/today');
    expect(await screen.findByText('Couldn’t load what needs you.', {}, { timeout: 4000 })).toBeInTheDocument();
    const week = screen.getByRole('heading', { level: 2, name: 'Your week' }).closest('section');
    if (!week) throw new Error('no week section');
    expect(within(week).getByText('Each post slot')).toBeInTheDocument();
    expect(within(week).queryByText('The scorecard so far')).not.toBeInTheDocument();
  });
});

describe('Today: quick capture', () => {
  it('saves an idea for the planner without leaving Today', async () => {
    setup();
    const sent: unknown[] = [];
    server.use(
      http.post(apiUrl('/api/ideas'), async ({ request }) => {
        sent.push(await request.json());
        return HttpResponse.json({ item: { id: 'idea-1' }, etag: '"i1"' }, { status: 201 });
      }),
    );
    const { user } = renderApp('/today');
    await user.click(await screen.findByRole('button', { name: 'Capture an idea' }));
    const idea = await screen.findByRole('textbox', { name: 'The idea' });
    await user.type(idea, 'Agent identities expire with the task');
    await user.click(screen.getByRole('checkbox', { name: 'It’s news' }));
    await user.click(screen.getByRole('button', { name: 'Save the idea' }));

    expect(await screen.findByText('Idea saved to Calendar > Ideas.')).toBeInTheDocument();
    expect(sent).toEqual([
      { title: 'Agent identities expire with the task', note: null, url: null, pillar: null, timeSensitive: true },
    ]);
    await waitFor(() => {
      expect(screen.getByRole('textbox', { name: 'The idea' })).toHaveValue('');
    });
  });

  it('points at the blocklist when it was never saved, and marks a blocklisted title', async () => {
    setup();
    let attempt = 0;
    server.use(
      http.post(apiUrl('/api/ideas'), () => {
        attempt += 1;
        return attempt === 1
          ? problem(409, 'blocklist_not_configured')
          : HttpResponse.json(
              {
                type: 'about:blank',
                title: 'Error',
                status: 422,
                code: 'confidential_text',
                request_id: 'req-idea-2',
                findings: [
                  { field: 'title', code: 'blocklisted-term', severity: 'block', message: 'Matches a blocklisted name.', start: 0, end: 5, ref: 'term-1' },
                ],
              },
              { status: 422, headers: { 'Content-Type': 'application/problem+json' } },
            );
      }),
    );
    const { user } = renderApp('/today');
    await user.click(await screen.findByRole('button', { name: 'Capture an idea' }));
    await user.type(await screen.findByRole('textbox', { name: 'The idea' }), 'Contoso migration lessons');
    await user.click(screen.getByRole('button', { name: 'Save the idea' }));
    expect(await screen.findByText('Save your blocklist first')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Open Blocklist & policy' })).toHaveAttribute('href', '/settings/policy');

    await user.click(screen.getByRole('button', { name: 'Save the idea' }));
    expect(await screen.findByText('Matches a blocklisted name.')).toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: 'The idea' })).toHaveAttribute('aria-invalid', 'true');
  });
});
