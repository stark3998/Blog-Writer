import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { server } from '@/test/msw';
import { findPageHeading, renderApp } from '@/test/render-app';

import {
  articleSummary,
  NEXT_WEEK,
  plannedSlot,
  postSummary,
  TEST_NOW,
  THIS_WEEK,
  weekPlan,
} from './testing/fixtures';
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

describe('Calendar > Plan', () => {
  it('shows next week’s proposed plan by day, in New York time, with the flagship’s week and the pillar balance', async () => {
    serve({ plans: [weekPlan()] });
    renderApp('/calendar/plan');
    await findPageHeading('Calendar');

    expect(await screen.findByRole('heading', { level: 2, name: 'Week of 30 Nov – 6 Dec' })).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: /Next week/ })).toBeChecked();
    expect(screen.getByText('All times are New York time (EST).')).toBeInTheDocument();

    const days = screen.getByRole('list', { name: 'Days of the week' });
    const tuesday = within(days).getByRole('listitem', { name: /Tue 1 Dec/ });
    expect(within(tuesday).getByText('MCP tool permissions in practice')).toBeInTheDocument();
    expect(within(tuesday).getByText('Native carousel')).toBeInTheDocument();
    expect(within(tuesday).getByText('AI agents & security')).toBeInTheDocument();
    expect(within(tuesday).getByText('Planned')).toBeInTheDocument();

    const flagship = screen.getByRole('region', { name: 'Flagship article' });
    expect(within(flagship).getByText('Okta to Entra: what 2,000 apps taught us')).toBeInTheDocument();
    const steps = within(flagship).getByRole('list', { name: 'The flagship’s week' });
    expect(within(steps).getByText('Mon 30 Nov, 08:00 EST')).toBeInTheDocument();
    expect(within(steps).getByText('Mon 7 Dec, 09:00 EST')).toBeInTheDocument();

    const balance = screen.getByRole('region', { name: 'Pillar balance' });
    expect(within(balance).getByText(/3 of 3 posts on a pillar \(100%\)/)).toBeInTheDocument();
  });
});

describe('Calendar > Plan: approval', () => {
  it('approves the plan with If-Match and says what starts', async () => {
    const fake = serve({ plans: [weekPlan()] });
    const { user } = renderApp('/calendar/plan');
    await user.click(await screen.findByRole('button', { name: 'Approve plan' }));

    // On the page, and in the toast that reads it out.
    const outcome = await screen.findAllByText(
      'Plan approved. Research for the flagship starts Mon 30 Nov, 08:00. Writing 2 posts now. 1 quick take waits for your take.',
    );
    expect(outcome).toHaveLength(2);
    const [approve] = fake.requests(`POST /api/plan/weeks/${NEXT_WEEK}/approve`);
    expect(approve?.ifMatch).toBe('"plan-etag-1"');
    expect(await screen.findByText('Approved')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Approve plan' })).not.toBeInTheDocument();
  });

  it('lists what blocks the approval when the server refuses it', async () => {
    serve({
      plans: [weekPlan()],
      override: (request) =>
        request.path.endsWith('/approve')
          ? problemResponse(
              409,
              'slot_conflict',
              {
                findings: [
                  { code: 'slot-same-day', severity: 'block', message: 'Two posts on the same day.', ref: '2026-12-02T15:00:00.000000Z' },
                ],
              },
              'Two posts would go out on the same day. Move one first.',
            )
          : undefined,
    });
    const { user } = renderApp('/calendar/plan');
    await user.click(await screen.findByRole('button', { name: 'Approve plan' }));

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('The plan isn’t approved yet');
    expect(alert).toHaveTextContent('Two posts would go out on the same day. Move one first.');
    expect(alert).toHaveTextContent('Wed 2 Dec, 10:00: Two posts on the same day.');
  });

  it('never approves a plan that changed unseen: a 412 asks to check it again', async () => {
    serve({
      plans: [weekPlan()],
      override: (request) =>
        request.path.endsWith('/approve')
          ? problemResponse(412, 'etag_mismatch', { current: { etag: '"newer"', updatedBy: 'pipeline' } })
          : undefined,
    });
    const { user } = renderApp('/calendar/plan');
    await user.click(await screen.findByRole('button', { name: 'Approve plan' }));
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('The plan changed while you had it open');
    expect(alert).toHaveTextContent('The pipeline saved a newer version. Check it again, then approve.');
  });

  it('shows the blocking findings the plan already has next to the approve button', async () => {
    serve({
      plans: [
        weekPlan({
          warnings: [
            { code: 'mix-off-pillar', severity: 'block', message: 'At least 80% of the posts must be on a pillar.', overridable: false },
            { code: 'slot-too-close', severity: 'warn', message: 'Posts less than 24 hours apart.', ref: 'wed', overridable: false },
          ],
        }),
      ],
    });
    renderApp('/calendar/plan');
    expect(await screen.findByText('1 issue blocks approval')).toBeInTheDocument();
    expect(screen.getByText('At least 80% of the posts must be on a pillar.')).toBeInTheDocument();
    const advisory = screen.getByRole('region', { name: 'Worth a look' });
    expect(within(advisory).getByText('Wed 2 Dec, 10:00: Posts less than 24 hours apart.')).toBeInTheDocument();
  });
});

describe('Calendar > Plan: moving slots', () => {
  it('moves a slot with its "Move to" menu, sending every slot with If-Match', async () => {
    const fake = serve({ plans: [weekPlan()] });
    const { user } = renderApp('/calendar/plan');
    await user.click(
      await screen.findByRole('button', { name: 'Actions for the Tue 1 Dec, 10:00 slot: MCP tool permissions in practice' }),
    );
    const menu = await screen.findByRole('menu');
    // A day another post holds is a hard block: never two posts on one day.
    expect(within(menu).getByRole('menuitem', { name: /Wed 2 Dec, 10:00/ })).toHaveAttribute('aria-disabled', 'true');
    expect(within(menu).getByRole('menuitem', { name: /Wed 2 Dec, 10:00/ })).toHaveTextContent(
      'Taken: a post goes out Wed 2 Dec, 10:00',
    );
    await user.click(within(menu).getByRole('menuitem', { name: /Fri 4 Dec, 15:00/ }));

    await waitFor(() => {
      expect(fake.requests(`PUT /api/plan/weeks/${NEXT_WEEK}`)).toHaveLength(1);
    });
    const [put] = fake.requests(`PUT /api/plan/weeks/${NEXT_WEEK}`);
    expect(put?.ifMatch).toBe('"plan-etag-1"');
    expect(put?.body).toEqual({
      slots: [
        { id: 'tue', localDate: '2026-12-04', localTime: '15:00' },
        { id: 'wed', localDate: '2026-12-02', localTime: '10:00' },
        { id: 'thu', localDate: '2026-12-03', localTime: '10:00' },
      ],
    });
    const friday = within(screen.getByRole('list', { name: 'Days of the week' })).getByRole('listitem', { name: /Fri 4 Dec/ });
    expect(await within(friday).findByText('MCP tool permissions in practice')).toBeInTheDocument();
    expect(await screen.findByText('Moved to Fri 4 Dec, 15:00.')).toBeInTheDocument();
  });

  it('warns when a move lands under 24 hours from another post', async () => {
    serve({ plans: [weekPlan()] });
    const { user } = renderApp('/calendar/plan');
    await user.click(
      await screen.findByRole('button', { name: 'Actions for the Thu 3 Dec, 10:00 slot: Conditional access checklist' }),
    );
    const monday = within(await screen.findByRole('menu')).getByRole('menuitem', { name: /Mon 30 Nov, 12:00/ });
    expect(monday).toHaveTextContent('Under 24 hours from Tue 1 Dec, 10:00 (22 hours)');
    expect(monday).not.toHaveAttribute('aria-disabled');
  });

  it('opens the conflict dialog when the plan changed meanwhile, and keeps mine with the new etag', async () => {
    let refused = false;
    const fake = serve({
      plans: [weekPlan()],
      override: (request) => {
        if (request.method === 'PUT' && !refused) {
          refused = true;
          return problemResponse(412, 'etag_mismatch', { current: { etag: '"plan-etag-1"', updatedBy: 'owner' } });
        }
        return undefined;
      },
    });
    const { user } = renderApp('/calendar/plan');
    await user.click(
      await screen.findByRole('button', { name: 'Actions for the Tue 1 Dec, 10:00 slot: MCP tool permissions in practice' }),
    );
    await user.click(within(await screen.findByRole('menu')).getByRole('menuitem', { name: /Fri 4 Dec, 15:00/ }));

    const dialog = await screen.findByRole('alertdialog', { name: 'This week’s plan changed since you opened it' });
    expect(dialog).toHaveTextContent('You saved a newer version in another tab or on another device.');
    await user.click(within(dialog).getByRole('button', { name: 'Keep mine' }));
    await waitFor(() => {
      expect(fake.requests(`PUT /api/plan/weeks/${NEXT_WEEK}`)).toHaveLength(2);
    });
    expect(fake.requests(`PUT /api/plan/weeks/${NEXT_WEEK}`)[1]?.ifMatch).toBe('"plan-etag-1"');
    await waitFor(() => {
      expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
    });
  });

  it('edits a slot’s contents and removes another, after asking', async () => {
    const fake = serve({ plans: [weekPlan()] });
    const { user } = renderApp('/calendar/plan');
    await user.click(
      await screen.findByRole('button', { name: 'Actions for the Thu 3 Dec, 10:00 slot: Conditional access checklist' }),
    );
    await user.click(within(await screen.findByRole('menu')).getByRole('menuitem', { name: 'Edit the slot…' }));
    const dialog = await screen.findByRole('dialog', { name: 'Edit the slot' });
    const title = within(dialog).getByRole('textbox', { name: 'Working title' });
    await user.clear(title);
    await user.type(title, 'Phishing-resistant MFA rollout');
    await user.click(within(dialog).getByRole('button', { name: 'Save slot' }));
    await waitFor(() => {
      expect(fake.requests('PUT /api/plan/weeks')).toHaveLength(1);
    });
    expect(fake.requests('PUT /api/plan/weeks')[0]?.body).toMatchObject({
      slots: [
        { id: 'tue' },
        { id: 'wed' },
        { id: 'thu', localDate: '2026-12-03', localTime: '10:00', lane: 'native', pillar: 'identity', format: 'image_card', title: 'Phishing-resistant MFA rollout' },
      ],
    });

    await user.click(
      await screen.findByRole('button', { name: 'Actions for the Wed 2 Dec, 10:00 slot: EU AI Act guidance for agent builders' }),
    );
    await user.click(within(await screen.findByRole('menu')).getByRole('menuitem', { name: 'Remove the slot…' }));
    const confirm = await screen.findByRole('alertdialog', { name: 'Remove the Wed 2 Dec, 10:00 slot?' });
    await user.click(within(confirm).getByRole('button', { name: 'Remove slot' }));
    await waitFor(() => {
      expect(fake.requests('PUT /api/plan/weeks')).toHaveLength(2);
    });
    const removal = fake.requests('PUT /api/plan/weeks')[1]?.body as { slots: { id: string }[] };
    expect(removal.slots.map((slot) => slot.id)).toEqual(['tue', 'thu']);
  });

  it('edits the flagship', async () => {
    const fake = serve({ plans: [weekPlan()] });
    const { user } = renderApp('/calendar/plan');
    const flagship = await screen.findByRole('region', { name: 'Flagship article' });
    await user.click(within(flagship).getByRole('button', { name: 'Edit' }));
    const dialog = await screen.findByRole('dialog', { name: 'Edit the flagship' });
    const title = within(dialog).getByRole('textbox', { name: /Working title/ });
    await user.clear(title);
    await user.type(title, 'The Okta to Entra playbook');
    await user.click(within(dialog).getByRole('button', { name: 'Save flagship' }));
    await waitFor(() => {
      expect(fake.requests('PUT /api/plan/weeks')).toHaveLength(1);
    });
    expect(fake.requests('PUT /api/plan/weeks')[0]?.body).toMatchObject({
      flagship: { title: 'The Okta to Entra playbook', pillar: 'identity', format: 'case-study', optional: false },
    });
    expect(await within(flagship).findByText('The Okta to Entra playbook')).toBeInTheDocument();
  });
});

describe('Calendar > Plan: quick takes', () => {
  it.each(['archived', 'expired'] as const)('asks for the take again when the slot’s take was %s', async (gone) => {
    const approved = weekPlan({ status: 'approved' });
    const slots = (approved.slots ?? []).map((slot) => (slot.id === 'wed' ? { ...slot, postId: 'post-old' } : slot));
    const old = postSummary({
      id: 'post-old',
      lane: 'quick_take',
      pillar: 'governance',
      status: gone,
      hook: 'The take that went',
      date: '2026-12-02',
      time: '10:00',
      weekPlanId: approved.id,
      planSlotId: 'wed',
    });
    serve({ plans: [{ ...approved, slots }], posts: [old] });
    const { user } = renderApp('/calendar/plan');

    const days = await screen.findByRole('list', { name: 'Days of the week' });
    const wednesday = within(days).getByRole('listitem', { name: /Wed 2 Dec/ });
    expect(await within(wednesday).findByText('Needs your take')).toBeInTheDocument();
    expect(within(wednesday).queryByText('The take that went')).not.toBeInTheDocument();
    await user.click(
      within(wednesday).getByRole('button', { name: 'Actions for the Wed 2 Dec, 10:00 slot: EU AI Act guidance for agent builders' }),
    );
    const menu = await screen.findByRole('menu');
    expect(within(menu).getByRole('menuitem', { name: 'Write your take' })).toHaveAttribute(
      'href',
      `/calendar/ideas?take=wed&week=${NEXT_WEEK}&idea=idea-eu`,
    );
    expect(within(menu).queryByRole('menuitem', { name: 'Open the post' })).not.toBeInTheDocument();
    expect(within(menu).getByRole('menuitem', { name: 'Remove the slot…' })).toBeInTheDocument();
  });
});

describe('Calendar > Plan: drag and drop (desktop)', () => {
  /** jsdom has no layout: lay the seven day columns out side by side, 100 px each. */
  function layOutWeek() {
    vi.spyOn(Element.prototype, 'getBoundingClientRect').mockImplementation(function (this: Element) {
      const column = this.closest('ol[aria-label="Days of the week"] > li');
      if (!column?.parentElement) return new DOMRect(0, 0, 0, 0);
      const x = Array.from(column.parentElement.children).indexOf(column) * 100;
      return this === column ? new DOMRect(x, 0, 100, 600) : new DOMRect(x + 10, 40, 80, 120);
    });
  }

  it('drops a slot on a free day, and shows taken days as a hard block while it moves', async () => {
    const fake = serve({ plans: [weekPlan()] });
    renderApp('/calendar/plan');
    const days = await screen.findByRole('list', { name: 'Days of the week' });
    layOutWeek();
    const tuesday = within(days).getByRole('listitem', { name: /Tue 1 Dec/ });
    const card = within(tuesday).getByRole('article', { name: 'MCP tool permissions in practice' });

    fireEvent.pointerDown(card, { isPrimary: true, button: 0, pointerId: 1, clientX: 150, clientY: 60 });
    fireEvent.pointerMove(document, { isPrimary: true, pointerId: 1, clientX: 170, clientY: 60 });
    fireEvent.pointerMove(document, { isPrimary: true, pointerId: 1, clientX: 450, clientY: 60 });

    const wednesday = within(days).getByRole('listitem', { name: /Wed 2 Dec/ });
    expect(await within(wednesday).findByText('Taken: one post a day')).toBeInTheDocument();
    const friday = within(days).getByRole('listitem', { name: /Fri 4 Dec/ });
    expect(within(friday).getByText('Drop for 15:00')).toBeInTheDocument();

    fireEvent.pointerUp(document, { isPrimary: true, pointerId: 1, clientX: 450, clientY: 60 });
    await waitFor(() => {
      expect(fake.requests(`PUT /api/plan/weeks/${NEXT_WEEK}`)).toHaveLength(1);
    });
    expect(fake.requests(`PUT /api/plan/weeks/${NEXT_WEEK}`)[0]?.body).toEqual({
      slots: [
        { id: 'tue', localDate: '2026-12-04', localTime: '15:00' },
        { id: 'wed', localDate: '2026-12-02', localTime: '10:00' },
        { id: 'thu', localDate: '2026-12-03', localTime: '10:00' },
      ],
    });
  });

  it('refuses a drop on a day another post holds: nothing is sent', async () => {
    const fake = serve({ plans: [weekPlan()] });
    renderApp('/calendar/plan');
    const days = await screen.findByRole('list', { name: 'Days of the week' });
    layOutWeek();
    const card = within(within(days).getByRole('listitem', { name: /Tue 1 Dec/ })).getByRole('article', {
      name: 'MCP tool permissions in practice',
    });
    fireEvent.pointerDown(card, { isPrimary: true, button: 0, pointerId: 1, clientX: 150, clientY: 60 });
    fireEvent.pointerMove(document, { isPrimary: true, pointerId: 1, clientX: 170, clientY: 60 });
    fireEvent.pointerMove(document, { isPrimary: true, pointerId: 1, clientX: 250, clientY: 60 });
    fireEvent.pointerUp(document, { isPrimary: true, pointerId: 1, clientX: 250, clientY: 60 });
    await waitFor(() => {
      expect(within(days).queryByText('Taken: one post a day')).not.toBeInTheDocument();
    });
    expect(fake.requests('PUT /api/plan/weeks')).toHaveLength(0);
  });
});

describe('Calendar > Plan: phones', () => {
  it('opens a slot’s sheet with "Move to" days, keeps the approval in thumb reach', async () => {
    const fake = serve({ plans: [weekPlan()] });
    const { user } = renderApp('/calendar/plan', { viewport: 'mobile' });
    expect(await screen.findByRole('button', { name: 'Approve plan' })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /MCP tool permissions in practice/ }));

    const sheet = await screen.findByRole('dialog', { name: 'MCP tool permissions in practice' });
    const moveTo = within(sheet).getByRole('region', { name: 'Move to' });
    const wednesday = within(moveTo).getByRole('button', { name: /Wed 2 Dec, 10:00/ });
    expect(wednesday).toHaveAttribute('aria-disabled', 'true');
    await user.click(wednesday);
    expect(fake.requests('PUT /api/plan/weeks')).toHaveLength(0);

    await user.click(within(moveTo).getByRole('button', { name: /Fri 4 Dec, 15:00/ }));
    await waitFor(() => {
      expect(fake.requests('PUT /api/plan/weeks')).toHaveLength(1);
    });
    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });
    // Focus follows the slot to its new day.
    const friday = within(screen.getByRole('list', { name: 'Days of the week' })).getByRole('listitem', { name: /Friday 4 December/ });
    await waitFor(() => {
      expect(within(friday).getByRole('button', { name: /MCP tool permissions in practice/ })).toHaveFocus();
    });
  });
});

describe('Calendar > Plan: other weeks and states', () => {
  it('has no plan yet: says when the planner proposes it, and plans it on request', async () => {
    const fake = serve({ plans: [] });
    const { user } = renderApp('/calendar/plan');
    const empty = await screen.findByRole('heading', { level: 2, name: 'No plan for next week yet.' });
    const section = empty.closest('section');
    if (!section) throw new Error('no section');
    expect(within(section).getByText('Your part')).toBeInTheDocument();
    expect(within(section).getByText(/The planner proposes it on Sun 29 Nov, 17:00/)).toBeInTheDocument();

    await user.click(within(section).getByRole('button', { name: 'Plan it now' }));
    expect(await screen.findByRole('heading', { name: 'Planning the week' })).toBeInTheDocument();
    expect(fake.requests(`POST /api/plan/weeks/${NEXT_WEEK}/generate`)).toHaveLength(1);
  });

  it('shows this week from its link, with missed posts and their offer', async () => {
    const thisWeek = weekPlan({
      weekStart: THIS_WEEK,
      status: 'approved',
      approvedAt: '2026-11-22T23:10:00.000000Z',
      flagship: null,
      slots: [
        plannedSlot({ id: 'tue', date: '2026-11-24', time: '10:00', postId: 'post-tue', title: 'Agent identity basics' }),
        plannedSlot({ id: 'thu', date: '2026-11-26', time: '10:00', postId: 'post-thu', pillar: 'governance' }),
      ],
    });
    const fake = serve({
      plans: [thisWeek, weekPlan()],
      posts: [
        postSummary({ id: 'post-tue', status: 'posted', date: '2026-11-24', time: '10:00', hook: 'Agent identity, in one diagram' }),
        postSummary({ id: 'post-thu', status: 'missed', date: '2026-11-26', time: '10:00', hook: 'Model cards are not governance' }),
      ],
      articles: [articleSummary({ id: 'art-1', weekPlanId: `plan-${THIS_WEEK}` })],
    });
    const { user } = renderApp(`/calendar/plan?week=${THIS_WEEK}`);
    expect(await screen.findByRole('heading', { level: 2, name: 'Week of 23 Nov – 29 Nov' })).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: 'This week' })).toBeChecked();
    expect(await screen.findByText('Approved Sun 22 Nov, 18:10.', { exact: false })).toBeInTheDocument();
    expect((await screen.findAllByText('Posted')).length).toBeGreaterThan(0);

    const missed = await screen.findByText('Missed Thu 26 Nov, 10:00: Model cards are not governance');
    const alert = missed.closest('[data-slot="alert"]');
    if (!(alert instanceof HTMLElement)) throw new Error('no alert');
    await user.click(within(alert).getByRole('button', { name: 'Take the next free slot' }));
    await waitFor(() => {
      expect(fake.requests('POST /api/posts/post-thu/reslot')).toHaveLength(1);
    });
    const [reslot] = fake.requests('POST /api/posts/post-thu/reslot');
    expect(reslot?.body).toEqual({ action: 'next_slot' });
    expect(reslot?.ifMatch).toBe('"post-thu-etag"');
    expect(await screen.findByText('Moved to Wed 2 Dec, 10:00.')).toBeInTheDocument();
  });

  it('names the day the clocks change, and shows a closed week’s scorecard', async () => {
    serve({
      plans: [
        weekPlan({
          weekStart: '2026-10-26',
          status: 'closed',
          flagship: null,
          slots: [],
          scorecard: {
            postsPublished: 3,
            postsWithOwnerSignal: 2,
            replies: 11,
            substantiveComments: 4,
            onPillarShare: 1,
            computedAt: '2026-11-02T12:00:00.000000Z',
          },
        }),
      ],
    });
    renderApp('/calendar/plan?week=2026-10-26');
    expect(await screen.findByText('Clocks go back an hour on Sun 1 Nov: EDT before, EST after.')).toBeInTheDocument();
    const scorecard = await screen.findByRole('region', { name: 'The week’s scorecard' });
    expect(within(scorecard).getByText('Posts published')).toBeInTheDocument();
    expect(within(scorecard).getByText('100%')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Approve plan' })).not.toBeInTheDocument();
  });

  it('says when the plan can’t be loaded, and tries again', async () => {
    let failing = true;
    serve({
      plans: [weekPlan()],
      override: (request) =>
        failing && request.method === 'GET' && request.path === `/api/plan/weeks/${NEXT_WEEK}`
          ? problemResponse(400, 'bad_request')
          : undefined,
    });
    const { user } = renderApp(`/calendar/plan?week=${NEXT_WEEK}`);
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Couldn’t load the plan.');
    failing = false;
    await user.click(within(alert).getByRole('button', { name: 'Try loading the plan again' }));
    expect(await screen.findByRole('heading', { level: 2, name: 'Week of 30 Nov – 6 Dec' })).toBeInTheDocument();
  });
});
