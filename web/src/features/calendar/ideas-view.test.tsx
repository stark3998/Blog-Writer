import { act, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { queryKeys } from '@/lib/query/keys';
import { server } from '@/test/msw';
import { findPageHeading, renderApp } from '@/test/render-app';

import { at, idea, NEXT_WEEK, TEST_NOW, weekPlan } from './testing/fixtures';
import { calendarServer, problemResponse, type CalendarServerOptions } from './testing/handlers';

function serve(options: CalendarServerOptions = {}) {
  const fake = calendarServer(options);
  server.use(...fake.handlers);
  return fake;
}

const MCP = idea({
  id: 'idea-mcp',
  title: 'MCP servers now need explicit tool consent',
  origin: 'feed',
  publisher: 'The Register',
  publishedAt: '2026-11-25T14:00:00.000000Z',
  url: 'https://example.com/mcp-consent',
  pillar: 'agents',
  relevance: 0.82,
  novelty: 0.64,
  suggestedLane: 'quick_take',
  suggestedFormat: 'text',
  angle: 'What tool consent means for SecOps teams running agents',
  triage: { ranAt: '2026-11-25T15:00:00.000000Z', reasons: 'Directly on the agents pillar; new since last week.' },
});

const NEWS = idea({
  id: 'idea-news',
  title: 'NIST opens comments on agent identity profile',
  origin: 'scout',
  pillar: 'governance',
  relevance: 0.7,
  novelty: 0.9,
  timeSensitive: true,
  expiresAt: at('2026-11-28', '05:00'),
  createdAt: '2026-11-20T10:00:00.000000Z',
});

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(new Date(TEST_NOW));
});

afterEach(() => {
  vi.useRealTimers();
});

function card(title: string): HTMLElement {
  const element = screen.getByRole('article', { name: title });
  return element;
}

describe('Calendar > Ideas: triage', () => {
  it('lists new ideas with their triage, time-sensitive ones first', async () => {
    serve({ ideas: [MCP, NEWS] });
    renderApp('/calendar/ideas');
    await findPageHeading('Calendar');
    expect(await screen.findByRole('heading', { level: 2, name: '2 new ideas' })).toBeInTheDocument();

    const articles = screen.getAllByRole('article');
    expect(articles.map((article) => within(article).getByRole('heading', { level: 3 }).textContent)).toEqual([
      'NIST opens comments on agent identity profile',
      'MCP servers now need explicit tool consent',
    ]);
    expect(within(card(NEWS.title)).getByText('Time-sensitive: expires in 19 hours')).toBeInTheDocument();

    const mcp = card(MCP.title);
    expect(within(mcp).getByText('From your feeds: The Register, 25 Nov')).toBeInTheDocument();
    expect(within(mcp).getByRole('meter', { name: 'Relevance' })).toHaveAttribute('aria-valuetext', '82%');
    expect(within(mcp).getByRole('meter', { name: 'Novelty' })).toHaveAttribute('aria-valuenow', '64');
    expect(within(mcp).getByText('AI agents & security')).toBeInTheDocument();
    expect(within(mcp).getByText('Quick take')).toBeInTheDocument();
    expect(within(mcp).getByText('What tool consent means for SecOps teams running agents')).toBeInTheDocument();
    const source = within(mcp).getByRole('link', { name: /Read the source/ });
    expect(source).toHaveAttribute('target', '_blank');
    expect(source).toHaveAttribute('rel', 'noopener noreferrer');
  });

  it('keeps an idea with If-Match, moves focus to the next one, and can undo it', async () => {
    const fake = serve({ ideas: [MCP, NEWS] });
    const { user } = renderApp('/calendar/ideas');
    await screen.findByRole('heading', { level: 2, name: '2 new ideas' });

    await user.click(within(card(NEWS.title)).getByRole('button', { name: 'Keep' }));
    await waitFor(() => {
      expect(screen.queryByRole('article', { name: NEWS.title })).not.toBeInTheDocument();
    });
    const [keep] = fake.requests('POST /api/ideas/idea-news/transition');
    expect(keep?.body).toEqual({ to: 'accepted' });
    expect(keep?.ifMatch).toBe('"idea-news-etag-1"');
    await waitFor(() => {
      expect(screen.getByRole('heading', { level: 3, name: MCP.title })).toHaveFocus();
    });

    await user.click(await screen.findByRole('button', { name: 'Undo' }));
    await waitFor(() => {
      expect(fake.requests('POST /api/ideas/idea-news/transition')).toHaveLength(2);
    });
    const undo = fake.requests('POST /api/ideas/idea-news/transition')[1];
    expect(undo?.body).toEqual({ to: 'new' });
    // Undo sends the etag the keep answered with, not the stale one.
    expect(undo?.ifMatch).not.toBe('"idea-news-etag-1"');
    expect(fake.ideas.get('idea-news')?.idea.status).toBe('new');
    expect(await screen.findByRole('article', { name: NEWS.title })).toBeInTheDocument();
  });

  it('dismisses, and snoozes until tomorrow morning in the owner’s zone', async () => {
    const fake = serve({ ideas: [MCP, NEWS] });
    const { user } = renderApp('/calendar/ideas');
    await screen.findByRole('heading', { level: 2, name: '2 new ideas' });

    await user.click(within(card(MCP.title)).getByRole('button', { name: `More for “${MCP.title}”` }));
    await user.click(within(await screen.findByRole('menu')).getByRole('menuitem', { name: 'Until tomorrow morning' }));
    await waitFor(() => {
      expect(fake.requests('POST /api/ideas/idea-mcp/transition')).toHaveLength(1);
    });
    expect(fake.requests('POST /api/ideas/idea-mcp/transition')[0]?.body).toEqual({
      to: 'snoozed',
      snoozedUntil: '2026-11-28T14:00:00.000000Z',
    });
    expect(await screen.findByText('Snoozed until Sat 28 Nov, 09:00.')).toBeInTheDocument();

    await user.click(within(await screen.findByRole('article', { name: NEWS.title })).getByRole('button', { name: 'Dismiss' }));
    await waitFor(() => {
      expect(fake.requests('POST /api/ideas/idea-news/transition')[0]?.body).toEqual({ to: 'dismissed' });
    });
    expect(await screen.findByRole('heading', { level: 2, name: 'No ideas yet.' })).toBeInTheDocument();
  });

  it('filters by pillar and lane, in the URL', async () => {
    const fake = serve({ ideas: [MCP, NEWS] });
    const { user, router } = renderApp('/calendar/ideas');
    await screen.findByRole('heading', { level: 2, name: '2 new ideas' });

    await user.click(screen.getByRole('radio', { name: 'AI governance' }));
    expect(await screen.findByRole('heading', { level: 2, name: '1 new idea' })).toBeInTheDocument();
    expect(router.state.location.search).toBe('?pillar=governance');
    expect(fake.requests('GET /api/ideas').at(-1)?.query.get('pillar')).toBe('governance');

    await user.click(screen.getByRole('combobox', { name: 'Filter by lane' }));
    await user.click(await screen.findByRole('option', { name: 'Native' }));
    expect(await screen.findByRole('heading', { level: 2, name: 'No ideas match these filters.' })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Clear filters' }));
    expect(await screen.findByRole('heading', { level: 2, name: '2 new ideas' })).toBeInTheDocument();
  });

  it('shows the other views: kept ideas with "Back to new"', async () => {
    serve({ ideas: [{ ...MCP, status: 'accepted' }] });
    const { user } = renderApp('/calendar/ideas');
    await user.click(await screen.findByRole('radio', { name: 'Kept' }));
    const kept = await screen.findByRole('article', { name: MCP.title });
    expect(within(kept).getByRole('button', { name: 'Back to new' })).toBeInTheDocument();
    expect(within(kept).queryByRole('button', { name: 'Keep' })).not.toBeInTheDocument();
    expect(within(kept).getByRole('button', { name: 'Dismiss' })).toBeInTheDocument();
  });

  it('has an honest empty inbox, and an error state that tries again', async () => {
    let failing = true;
    serve({
      ideas: [],
      override: (request) => (failing && request.path === '/api/ideas' ? problemResponse(400, 'bad_request') : undefined),
    });
    const { user } = renderApp('/calendar/ideas');
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Couldn’t load the ideas.');
    failing = false;
    await user.click(within(alert).getByRole('button', { name: 'Try loading the ideas again' }));
    const empty = await screen.findByRole('heading', { level: 2, name: 'No ideas yet.' });
    expect(within(empty.closest('section') ?? document.body).getByText('Your part')).toBeInTheDocument();
  });
});

describe('Calendar > Ideas: "What’s your take?"', () => {
  it('saves only the confirmed wording to the POV bank; the raw words go to redaction alone', async () => {
    const fake = serve({ ideas: [MCP] });
    const { user } = renderApp('/calendar/ideas');
    await user.click(within(await screen.findByRole('article', { name: MCP.title })).getByRole('button', { name: 'What’s your take?' }));

    const sheet = await screen.findByRole('dialog', { name: 'What’s your take?' });
    const raw = 'We tried tool consent at Contoso and it slowed every incident.';
    await user.type(within(sheet).getByRole('textbox', { name: 'Your take' }), raw);
    await user.click(within(sheet).getByRole('button', { name: 'Make it public-safe' }));

    const wording = await within(sheet).findByRole('textbox', { name: 'Wording to keep' });
    expect(wording).toHaveValue('We tried tool consent at a large client and it slowed every incident.');
    await user.click(within(sheet).getByRole('button', { name: 'Use this wording' }));
    await user.click(await within(sheet).findByRole('button', { name: 'Save my take' }));

    await waitFor(() => {
      expect(fake.requests('POST /api/povs')).toHaveLength(1);
    });
    expect(fake.requests('POST /api/povs')[0]?.body).toEqual({
      pillar: 'agents',
      statement: 'We tried tool consent at a large client and it slowed every incident.',
      origin: 'idea_take',
      ideaId: 'idea-mcp',
      confidentiality: 'anonymized',
    });
    // The raw words went to the redact call and nowhere else.
    const carriedRaw = fake.sent.filter((request) => JSON.stringify(request.body ?? '').includes('Contoso'));
    expect(carriedRaw.map((request) => request.path)).toEqual(['/api/stories/intake/redact']);
    expect(fake.requests('POST /api/stories/intake/redact')[0]?.body).toMatchObject({ purpose: 'take', pillar: 'agents' });
    expect(fake.requests('POST /api/posts')).toHaveLength(0);
    expect(await screen.findByText('Take saved to your POV bank.')).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });
  });

  it('starts the quick take for a slot from Today’s link, linked to the slot, with the take', async () => {
    const eu = idea({ id: 'idea-eu', title: 'EU AI Act guidance for agent builders', pillar: 'governance' });
    const fake = serve({ ideas: [eu], plans: [weekPlan({ status: 'approved' })] });
    const { user, router } = renderApp(`/calendar/ideas?take=wed&week=${NEXT_WEEK}&idea=idea-eu`, { viewport: 'mobile' });

    const sheet = await screen.findByRole('dialog', { name: 'What’s your take?' });
    expect(sheet).toHaveClass('bottom-0');
    expect(await within(sheet).findByText('For the quick-take slot on Wed 2 Dec, 10:00.')).toBeInTheDocument();
    await user.type(within(sheet).getByRole('textbox', { name: 'Your take' }), 'Guidance helps, but agent builders need examples.');
    await user.click(within(sheet).getByRole('button', { name: 'Make it public-safe' }));
    await user.click(await within(sheet).findByRole('button', { name: 'Use this wording' }));
    expect(await within(sheet).findByRole('radio', { name: 'Save it and start a quick take' })).toBeChecked();
    await user.click(within(sheet).getByRole('button', { name: 'Save and start the quick take' }));

    await waitFor(() => {
      expect(fake.requests('POST /api/posts')).toHaveLength(1);
    });
    expect(fake.requests('POST /api/posts')[0]?.body).toEqual({
      lane: 'quick_take',
      pillar: 'governance',
      format: 'text',
      ideaId: 'idea-eu',
      takeText: 'Guidance helps, but agent builders need examples.',
      povIds: ['pov-1'],
      planSlot: { weekStart: NEXT_WEEK, slotId: 'wed' },
    });
    // The plan's slot names the post and the approved plan stays approved (no second approval).
    const plan = fake.plans.get(NEXT_WEEK)?.plan;
    expect(plan?.status).toBe('approved');
    expect(plan?.slots?.find((slot) => slot.id === 'wed')?.postId).toBe('post-quick-1');
    expect(fake.requests('PUT /api/plan/weeks')).toHaveLength(0);
    expect(await screen.findByRole('heading', { name: 'Your quick take is being written' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Writing the post' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Open the post' })).toHaveAttribute('href', '/studio/post-quick-1/posts');
    await waitFor(() => {
      expect(router.state.location.search).toBe('');
    });
  });

  it('reads the week plan again after a take fills its slot, so the Calendar edits with its new etag', async () => {
    const eu = idea({ id: 'idea-eu', title: 'EU AI Act guidance for agent builders', pillar: 'governance' });
    const fake = serve({ ideas: [eu], plans: [weekPlan({ status: 'approved' })] });
    const { user, router, appData } = renderApp(`/calendar/ideas?take=wed&week=${NEXT_WEEK}&idea=idea-eu`);
    const sheet = await screen.findByRole('dialog', { name: 'What’s your take?' });
    expect(await within(sheet).findByText('For the quick-take slot on Wed 2 Dec, 10:00.')).toBeInTheDocument();
    await user.type(within(sheet).getByRole('textbox', { name: 'Your take' }), 'Guidance helps, but agent builders need examples.');
    await user.click(within(sheet).getByRole('button', { name: 'Make it public-safe' }));
    await user.click(await within(sheet).findByRole('button', { name: 'Use this wording' }));
    await user.click(await within(sheet).findByRole('button', { name: 'Save and start the quick take' }));
    expect(await screen.findByRole('heading', { name: 'Your quick take is being written' })).toBeInTheDocument();
    // Linking the slot rewrote the plan on the server: a new etag, and no event says so. The
    // plan this page read (the Calendar's cache entry) is read again.
    const linked = fake.plans.get(NEXT_WEEK)?.etag;
    expect(linked).not.toBe('"plan-etag-1"');
    await waitFor(() => {
      const cached = appData.queryClient.getQueryData<{ etag?: string | null }>(queryKeys.plans.detail(`plan-${NEXT_WEEK}`));
      expect(cached?.etag).toBe(linked);
    });

    await act(async () => {
      await router.navigate('/calendar/plan');
    });
    await user.click(
      await screen.findByRole('button', { name: 'Actions for the Tue 1 Dec, 10:00 slot: MCP tool permissions in practice' }),
    );
    await user.click(within(await screen.findByRole('menu')).getByRole('menuitem', { name: /Fri 4 Dec, 15:00/ }));
    await waitFor(() => {
      expect(fake.requests(`PUT /api/plan/weeks/${NEXT_WEEK}`)).toHaveLength(1);
    });
    expect(fake.requests(`PUT /api/plan/weeks/${NEXT_WEEK}`)[0]?.ifMatch).toBe(linked);
    expect(await screen.findByText('Moved to Fri 4 Dec, 15:00.')).toBeInTheDocument();
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
  });

  it('says what to do when AI use isn’t confirmed, keeping the take', async () => {
    serve({
      ideas: [MCP],
      override: (request) =>
        request.path === '/api/posts' && request.method === 'POST' ? problemResponse(409, 'ai_use_not_confirmed') : undefined,
    });
    const { user } = renderApp('/calendar/ideas');
    await user.click(within(await screen.findByRole('article', { name: MCP.title })).getByRole('button', { name: `More for “${MCP.title}”` }));
    await user.click(within(await screen.findByRole('menu')).getByRole('menuitem', { name: 'Start a quick take…' }));
    const sheet = await screen.findByRole('dialog', { name: 'What’s your take?' });
    await user.type(within(sheet).getByRole('textbox', { name: 'Your take' }), 'Consent prompts are a speed bump, not a control.');
    await user.click(within(sheet).getByRole('button', { name: 'Make it public-safe' }));
    await user.click(await within(sheet).findByRole('button', { name: 'Use this wording' }));
    await user.click(await within(sheet).findByRole('button', { name: 'Save and start the quick take' }));
    expect(await within(sheet).findByText('Confirm your employer’s AI-use policy first')).toBeInTheDocument();
    expect(within(sheet).getByText('Your take is saved. Only the quick take still needs to start.')).toBeInTheDocument();
    expect(within(sheet).getByRole('link', { name: /Open Blocklist & policy/ })).toHaveAttribute('href', '/settings/policy');
  });
});

describe('Calendar > Ideas: pieces, capture and edits', () => {
  it('starts a flagship article from an idea', async () => {
    const fake = serve({ ideas: [MCP] });
    const { user } = renderApp('/calendar/ideas');
    await user.click(within(await screen.findByRole('article', { name: MCP.title })).getByRole('button', { name: `More for “${MCP.title}”` }));
    await user.click(within(await screen.findByRole('menu')).getByRole('menuitem', { name: 'Start a flagship article…' }));
    const dialog = await screen.findByRole('dialog', { name: 'Start a flagship article' });
    await user.click(within(dialog).getByRole('button', { name: 'Start the article' }));
    await waitFor(() => {
      expect(fake.requests('POST /api/articles')).toHaveLength(1);
    });
    expect(fake.requests('POST /api/articles')[0]?.body).toEqual({
      ideaId: 'idea-mcp',
      meta: { title: MCP.title, pillar: 'agents', format: 'case-study' },
    });
    expect(await screen.findByText('Flagship article started.')).toBeInTheDocument();
  });

  it('adds an idea, showing a blocklist hit on its field', async () => {
    let blocked = true;
    const fake = serve({
      ideas: [],
      override: (request) =>
        blocked && request.method === 'POST' && request.path === '/api/ideas'
          ? problemResponse(422, 'confidential_text', {
              findings: [{ field: 'note', code: 'blocklist', severity: 'block', message: 'Blocklisted term.', start: 0, end: 4, ref: 'term-1' }],
            })
          : undefined,
    });
    const { user } = renderApp('/calendar/ideas');
    await user.click(await screen.findByRole('button', { name: 'Add an idea' }));
    const dialog = await screen.findByRole('dialog', { name: 'Add an idea' });
    await user.type(within(dialog).getByRole('textbox', { name: /Title/ }), 'Passkeys for service accounts');
    await user.type(within(dialog).getByRole('textbox', { name: /Note/ }), 'Saw this at a client');
    await user.click(within(dialog).getByRole('button', { name: 'Add idea' }));

    const note = await within(dialog).findByRole('textbox', { name: /Note/ });
    await waitFor(() => {
      expect(note).toHaveAttribute('aria-invalid', 'true');
    });
    expect(within(dialog).getByText('This matches your blocklist. Rephrase it.')).toBeInTheDocument();

    blocked = false;
    await user.clear(note);
    await user.type(note, 'Worth a quick take');
    await user.click(within(dialog).getByRole('button', { name: 'Add idea' }));
    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });
    expect(fake.requests('POST /api/ideas').at(-1)?.body).toEqual({
      title: 'Passkeys for service accounts',
      note: 'Worth a quick take',
      url: null,
      pillar: null,
      suggestedLane: null,
      timeSensitive: false,
    });
    expect(await screen.findByRole('article', { name: 'Passkeys for service accounts' })).toBeInTheDocument();
  });

  it('edits the triage, sending only what changed, and resolves a conflict', async () => {
    let refused = false;
    const fake = serve({
      ideas: [MCP],
      override: (request) => {
        if (request.method === 'PUT' && !refused) {
          refused = true;
          return problemResponse(412, 'etag_mismatch', { current: { etag: '"idea-mcp-etag-1"', updatedBy: 'owner' } });
        }
        return undefined;
      },
    });
    const { user } = renderApp('/calendar/ideas');
    await user.click(within(await screen.findByRole('article', { name: MCP.title })).getByRole('button', { name: `More for “${MCP.title}”` }));
    await user.click(within(await screen.findByRole('menu')).getByRole('menuitem', { name: 'Edit the triage…' }));
    const dialog = await screen.findByRole('dialog', { name: 'Edit the triage' });
    const angle = within(dialog).getByRole('textbox', { name: /Angle/ });
    await user.clear(angle);
    await user.type(angle, 'Consent fatigue is the real risk');
    await user.click(within(dialog).getByRole('button', { name: 'Save triage' }));

    const conflict = await screen.findByRole('alertdialog', { name: 'This idea changed since you opened it' });
    await user.click(within(conflict).getByRole('button', { name: 'Keep mine' }));
    await waitFor(() => {
      expect(fake.requests('PUT /api/ideas/idea-mcp')).toHaveLength(2);
    });
    const [first, second] = fake.requests('PUT /api/ideas/idea-mcp');
    expect(first?.body).toEqual({ angle: 'Consent fatigue is the real risk' });
    expect(first?.ifMatch).toBe('"idea-mcp-etag-1"');
    expect(second?.ifMatch).toBe('"idea-mcp-etag-1"');
    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });
    expect(await screen.findByText('Consent fatigue is the real risk')).toBeInTheDocument();
  });
});

// Last in the file: its toasts ("Flagship article started.") outlive the test, and the toaster is global.
describe('Calendar > Ideas: an idea a link opens (repurpose)', () => {
  const RECUT = idea({
    id: 'idea-recut',
    title: 'Re-cut: Okta to Entra in 90 days',
    origin: 'repurpose',
    pillar: 'identity',
    suggestedLane: 'repurpose',
    suggestedFormat: 'carousel',
    repurposeOf: 'post-1',
  });
  const CANDIDATE = idea({
    id: 'idea-candidate',
    title: 'Flagship candidate: Okta to Entra in 90 days',
    origin: 'repurpose',
    pillar: 'identity',
    suggestedLane: 'flagship',
    repurposeOf: 'post-1',
  });

  it('shows the idea first and focused, never the take sheet, and starts a re-cut as a repurpose post', async () => {
    const fake = serve({ ideas: [MCP, RECUT] });
    const { user, router } = renderApp('/calendar/ideas?idea=idea-recut');
    const opened = await screen.findByRole('region', { name: 'The idea you opened' });
    const card = within(opened).getByRole('article', { name: RECUT.title });
    await waitFor(() => {
      expect(within(card).getByRole('heading', { level: 3, name: RECUT.title })).toHaveFocus();
    });
    // A new idea from a link is not a quick take: only Today's link (`take=`) opens the take sheet.
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(within(card).queryByRole('button', { name: 'What’s your take?' })).not.toBeInTheDocument();
    // The list below leaves it out: it is shown once.
    expect(await screen.findByRole('heading', { level: 2, name: '1 new idea' })).toBeInTheDocument();
    expect(screen.getAllByRole('article', { name: RECUT.title })).toHaveLength(1);

    await user.click(within(card).getByRole('button', { name: 'Start the re-cut…' }));
    const dialog = await screen.findByRole('dialog', { name: 'Start the re-cut' });
    await user.click(within(dialog).getByRole('button', { name: 'Start the re-cut' }));
    await waitFor(() => {
      expect(fake.requests('POST /api/posts')).toHaveLength(1);
    });
    expect(fake.requests('POST /api/posts')[0]?.body).toEqual({
      lane: 'repurpose',
      pillar: 'identity',
      format: 'carousel',
      ideaId: 'idea-recut',
    });
    expect(await screen.findByText('Re-cut started.')).toBeInTheDocument();
    expect(fake.requests('POST /api/povs')).toHaveLength(0);

    await user.click(within(opened).getByRole('button', { name: 'Close the idea you opened' }));
    await waitFor(() => {
      expect(router.state.location.search).toBe('');
    });
    expect(screen.queryByRole('region', { name: 'The idea you opened' })).not.toBeInTheDocument();
  });

  it('starts the article for a flagship candidate', async () => {
    const fake = serve({ ideas: [CANDIDATE] });
    const { user } = renderApp('/calendar/ideas?idea=idea-candidate');
    const opened = await screen.findByRole('region', { name: 'The idea you opened' });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    await user.click(within(opened).getByRole('button', { name: 'Start the article…' }));
    const dialog = await screen.findByRole('dialog', { name: 'Start a flagship article' });
    await user.click(within(dialog).getByRole('button', { name: 'Start the article' }));
    await waitFor(() => {
      expect(fake.requests('POST /api/articles')).toHaveLength(1);
    });
    expect(fake.requests('POST /api/articles')[0]?.body).toMatchObject({
      ideaId: 'idea-candidate',
      meta: { title: CANDIDATE.title, pillar: 'identity' },
    });
    expect(fake.requests('POST /api/posts')).toHaveLength(0);
  });

  it('keeps the opened idea in place when it changes, and focus on it', async () => {
    const fake = serve({ ideas: [RECUT] });
    const { user } = renderApp('/calendar/ideas?idea=idea-recut');
    const opened = await screen.findByRole('region', { name: 'The idea you opened' });
    await user.click(within(opened).getByRole('button', { name: 'Keep' }));
    await waitFor(() => {
      expect(fake.requests('POST /api/ideas/idea-recut/transition')).toHaveLength(1);
    });
    expect(fake.requests('POST /api/ideas/idea-recut/transition')[0]?.ifMatch).toBe('"idea-recut-etag-1"');
    const card = within(opened).getByRole('article', { name: RECUT.title });
    expect(await within(card).findByText('Kept')).toBeInTheDocument();
    expect(within(card).getByRole('heading', { level: 3, name: RECUT.title })).toHaveFocus();
  });
});
