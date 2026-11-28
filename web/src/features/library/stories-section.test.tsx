import { act, screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { toast } from 'sonner';
import { afterEach, describe, expect, it } from 'vitest';

import { apiUrl, server } from '@/test/msw';
import { findPageHeading, renderApp } from '@/test/render-app';

import type { PovEntry, ReviewStatus, StoryEntry } from './lib/types';
import { ownerInputs, pillarSettings, pov, story, T2, versioned } from './testing/fixtures';
import { getJson, machinesHandler, problemWith, record, type RecordedRequest } from './testing/handlers';

afterEach(() => {
  act(() => {
    toast.dismiss();
  });
});

const RAW = 'At Contoso we gave the agent our admin token, and jane@contoso.example found out the hard way.';
const PROPOSAL = 'Give an agent its own narrowly scoped identity, never a shared admin token.';

const TARGETS: Record<string, (status: ReviewStatus) => ReviewStatus> = {
  approve: () => 'approved',
  reject: () => 'rejected',
  archive: () => 'archived',
  restore: (status) => (status === 'archived' ? 'approved' : 'proposed'),
};

interface BankOptions {
  stories?: StoryEntry[];
  povs?: PovEntry[];
  /** Answers a review instead of the bank (a 412, say) for the given call number. */
  onReview?: (call: number) => Response | undefined;
  onPutStory?: () => Response | undefined;
}

/** An in-memory story and position bank with the server's list filters and ETag rules. */
function setupBank({ stories = [], povs = [], onReview, onPutStory }: BankOptions = {}) {
  const bank = {
    stories: stories.map((item) => ({ item, etag: `"${item.id}-1"` })),
    povs: povs.map((item) => ({ item, etag: `"${item.id}-1"` })),
  };
  const requests: RecordedRequest[] = [];
  let reviews = 0;
  const list = (entries: { item: StoryEntry | PovEntry; etag: string }[], url: URL, pillarsOf: (item: StoryEntry | PovEntry) => readonly string[]) => {
    const status = url.searchParams.get('status');
    const pillar = url.searchParams.get('pillar');
    const items = entries.filter(({ item }) => {
      if (status && item.status !== status) return false;
      if (pillar && !pillarsOf(item).includes(pillar)) return false;
      return true;
    });
    return HttpResponse.json({ items, nextCursor: null });
  };
  const review = (entries: { item: StoryEntry | PovEntry; etag: string }[]) =>
    async ({ request, params }: { request: Request; params: Record<string, string | readonly string[] | undefined> }) => {
      const recorded = await record(requests, request);
      reviews += 1;
      const custom = onReview?.(reviews);
      if (custom) return custom;
      const entry = entries.find(({ item }) => item.id === params.id);
      if (!entry) return problemWith(404, 'not_found');
      if (recorded.ifMatch !== entry.etag) return problemWith(412, 'etag_mismatch', { current: { etag: entry.etag, updatedAt: T2 } });
      const { decision } = recorded.body as { decision: string };
      entry.item = { ...entry.item, status: (TARGETS[decision] ?? (() => entry.item.status))(entry.item.status), updatedAt: T2 };
      entry.etag = `${entry.etag.slice(0, -1)}+"`;
      return HttpResponse.json(entry);
    };

  server.use(
    machinesHandler,
    getJson('/api/kb/pillars', versioned(pillarSettings(), '"pillars-1"')),
    getJson('/api/kb/inputs', ownerInputs()),
    http.get(apiUrl('/api/stories'), ({ request }) =>
      list(bank.stories, new URL(request.url), (item) => (Array.isArray(item.pillars) ? (item.pillars as string[]) : [])),
    ),
    http.get(apiUrl('/api/povs'), ({ request }) =>
      list(bank.povs, new URL(request.url), (item) => (typeof item.pillar === 'string' ? [item.pillar] : [])),
    ),
    http.post(apiUrl('/api/stories/:id/review'), review(bank.stories)),
    http.post(apiUrl('/api/povs/:id/review'), review(bank.povs)),
    http.post(apiUrl('/api/povs'), async ({ request }) => {
      const recorded = await record(requests, request);
      const created = { item: pov({ id: 'pov-new', ...(recorded.body as object), status: 'approved' }), etag: '"pov-new-1"' };
      bank.povs.unshift(created);
      return HttpResponse.json(created, { status: 201 });
    }),
    http.put(apiUrl('/api/stories/:id'), async ({ request }) => {
      await record(requests, request);
      return onPutStory?.() ?? HttpResponse.json(bank.stories[0]);
    }),
    http.post(apiUrl('/api/stories/seed'), async ({ request }) => {
      await record(requests, request);
      return HttpResponse.json({ jobId: 'seed-1', status: 'queued' }, { status: 202 });
    }),
    http.post(apiUrl('/api/stories/intake/redact'), async ({ request }) => {
      await record(requests, request);
      const start = RAW.indexOf('jane@');
      return HttpResponse.json({
        findings: [
          { code: 'email-address', severity: 'block', message: 'Looks like an e-mail address. Remove it, or name the role instead.', start, end: start + 'jane@contoso.example'.length, overridable: false },
        ],
        proposal: PROPOSAL,
        reason: null,
        anonymizerUsed: true,
      });
    }),
    http.post(apiUrl('/api/stories/intake/check'), async ({ request }) => {
      await record(requests, request);
      return HttpResponse.json({ ok: true, findings: [] });
    }),
  );
  return { bank, requests };
}

const SEEDED = story({
  id: 'story-seed-1',
  status: 'proposed',
  origin: 'seed',
  seedSource: 'cv',
  title: 'Cut sign-in tickets by about 40%',
  approvedAt: null,
  classifier: {},
  updatedAt: '2026-11-25T10:00:00.000000Z',
});
const PROPOSED_POV = pov({
  id: 'pov-seed-1',
  status: 'proposed',
  origin: 'seed',
  statement: 'Identity is the control plane for agents.',
  approvedAt: null,
  updatedAt: '2026-11-24T10:00:00.000000Z',
});

async function openStories(options: BankOptions = {}, viewport: 'desktop' | 'mobile' = 'desktop') {
  const state = setupBank(options);
  const view = renderApp('/library/stories', { viewport });
  await findPageHeading('Library');
  await screen.findByRole('heading', { level: 2, name: 'Stories and positions' });
  return { ...state, ...view };
}

describe('Library > Stories & POV: the review queue', () => {
  it('reviews proposed entries one at a time on a phone, focus following', async () => {
    const { requests, user } = await openStories({ stories: [SEEDED, story()], povs: [PROPOSED_POV] }, 'mobile');
    const heading = await screen.findByRole('heading', { level: 3, name: 'To review: 2' });
    const queue = heading.closest('section');
    if (!queue) throw new Error('the queue is not a section');
    expect(within(queue).getByRole('heading', { level: 4, name: 'Cut sign-in tickets by about 40%' })).toBeInTheDocument();
    expect(within(queue).getByText('Proposed from your resume')).toBeInTheDocument();
    expect(within(queue).getByText('Not checked yet')).toBeInTheDocument();

    await user.click(within(queue).getByRole('button', { name: 'Approve' }));
    expect(await screen.findByText('Approved “Cut sign-in tickets by about 40%”.')).toBeInTheDocument();
    const approve = requests.find((request) => request.path === '/api/stories/story-seed-1/review');
    expect(approve?.ifMatch).toBe('"story-seed-1-1"');
    expect(approve?.body).toEqual({ decision: 'approve' });

    const next = await screen.findByRole('heading', { level: 3, name: 'To review: 1' });
    await waitFor(() => {
      expect(next).toHaveFocus();
    });
    const card = next.closest('section');
    if (!card) throw new Error('the queue is not a section');
    expect(within(card).getByText('Identity is the control plane for agents.')).toBeInTheDocument();
    await user.click(within(card).getByRole('button', { name: 'Reject' }));
    expect(await screen.findByRole('heading', { level: 3, name: 'Nothing to review' })).toBeInTheDocument();
    expect(requests.find((request) => request.path === '/api/povs/pov-seed-1/review')?.body).toEqual({ decision: 'reject' });
  });

  it('shows the latest version when an entry changed during the review', async () => {
    const { requests, user } = await openStories({
      stories: [SEEDED],
      onReview: (call) => (call === 1 ? problemWith(412, 'etag_mismatch', { current: { etag: '"story-seed-1-1"' } }) : undefined),
    });
    await screen.findByRole('heading', { level: 3, name: 'To review: 1' });
    await user.click(screen.getByRole('button', { name: 'Approve' }));
    expect(await screen.findByText('This entry changed while you were reading it.')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Approve' }));
    expect(await screen.findByRole('heading', { level: 3, name: 'Nothing to review' })).toBeInTheDocument();
    expect(requests.filter((request) => request.path.endsWith('/review'))).toHaveLength(2);
  });
});

describe('Library > Stories & POV: the banks', () => {
  it('lists stories with their status, pillars and classifier verdict, and archives one', async () => {
    const { requests, user } = await openStories({ stories: [story()] });
    expect(await screen.findByText('4 of 10')).toBeInTheDocument();
    const row = await screen.findByRole('button', { name: /Moving 2,000\+ apps without a sign-in outage/ });
    expect(row).toHaveTextContent('Identity modernization');
    expect(row).toHaveTextContent('Approved');
    expect(row).toHaveTextContent('Classifier passed');
    await user.click(row);
    expect(await screen.findByText('Kept legacy federation for six weeks.')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Archive' }));
    expect(await screen.findByText('Archived: “Moving 2,000+ apps without a sign-in outage”.')).toBeInTheDocument();
    expect(requests.find((request) => request.path === '/api/stories/story-0001/review')?.body).toEqual({ decision: 'archive' });
  });

  it('marks the field the blocklist refused when an edit is saved', async () => {
    const { user } = await openStories({
      stories: [story()],
      onPutStory: () =>
        problemWith(422, 'confidential_text', {
          detail: 'Some text needs redacting before it can be saved. Check the highlighted parts.',
          findings: [{ field: 'lessons', code: 'blocklist', severity: 'block', message: 'Matches a blocklisted client name.', ref: 't-3' }],
        }),
    });
    await user.click(await screen.findByRole('button', { name: /Moving 2,000\+ apps/ }));
    await user.click(await screen.findByRole('button', { name: 'Edit' }));
    expect(await screen.findByRole('heading', { level: 3, name: 'Edit story' })).toHaveFocus();
    const lessons = screen.getByRole('textbox', { name: /^What you learned/ });
    await user.type(lessons, ' Ask the client first.');
    await user.click(screen.getByRole('button', { name: 'Save story' }));
    expect(await screen.findByText('Some details can’t be stored.')).toBeInTheDocument();
    expect(lessons).toHaveAttribute('aria-invalid', 'true');
    expect(lessons).toHaveAccessibleDescription(/Matches a blocklisted client name/);
  });

  it('switches to the position bank', async () => {
    const { user } = await openStories({ povs: [pov()] });
    await user.click(screen.getByRole('tab', { name: 'Positions' }));
    expect(await screen.findByText('An MCP server is a production integration and needs the same review as one.')).toBeInTheDocument();
  });
});

describe('Library > Stories & POV: new entries', () => {
  it('writes a position through the intake flow and stores only the confirmed wording', async () => {
    const { requests, user } = await openStories();
    await user.click(screen.getByRole('button', { name: 'New position' }));
    const heading = await screen.findByRole('heading', { level: 3, name: 'New position' });
    expect(heading).toHaveFocus();
    const panel = heading.closest('section');
    if (!panel) throw new Error('the composer is not a section');

    await user.click(within(panel).getByRole('combobox', { name: /^Pillar/ }));
    await user.click(await screen.findByRole('option', { name: 'AI agents & security' }));
    await user.type(screen.getByRole('textbox', { name: /^Your position, in your own words/ }), RAW);
    await user.click(screen.getByRole('button', { name: 'Make it public-safe' }));
    expect(await screen.findByRole('heading', { name: 'Check the wording' })).toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: /^Wording to keep/ })).toHaveValue(PROPOSAL);
    await user.click(screen.getByRole('button', { name: 'Use this wording' }));
    expect(await screen.findByRole('heading', { name: 'Wording confirmed' })).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Save position' }));
    expect(await screen.findByText('Position saved and approved.')).toBeInTheDocument();
    const redact = requests.find((request) => request.path === '/api/stories/intake/redact');
    expect(redact?.body).toEqual({ text: RAW, purpose: 'pov', pillar: 'agents' });
    const created = requests.find((request) => request.path === '/api/povs');
    expect(created?.body).toEqual({
      pillar: 'agents',
      statement: PROPOSAL,
      context: null,
      confidentiality: 'anonymized',
      origin: 'owner',
      ideaId: null,
    });
    // The raw words went to the redaction route and nowhere else.
    const elsewhere = requests.filter((request) => request.path !== '/api/stories/intake/redact');
    expect(elsewhere.some((request) => JSON.stringify(request.body).includes('Contoso'))).toBe(false);
    expect(screen.queryByRole('heading', { level: 3, name: 'New position' })).not.toBeInTheDocument();
  });

  it('proposes entries from the resume text and follows the job', async () => {
    const { requests, user } = await openStories();
    await user.type(screen.getByRole('textbox', { name: /^Resume text/ }), 'Led a 2,000-app identity migration.');
    await user.click(screen.getByRole('button', { name: 'Propose entries' }));
    expect(await screen.findByRole('heading', { name: 'Proposing entries' })).toBeInTheDocument();
    expect(requests.find((request) => request.path === '/api/stories/seed')?.body).toEqual({
      text: 'Led a 2,000-app identity migration.',
      source: 'cv',
    });
    expect(screen.getByRole('textbox', { name: /^Resume text/ })).toHaveValue('');
  });

  it('asks before closing a new entry that holds unsaved words', async () => {
    const { requests, user } = await openStories();
    await user.click(screen.getByRole('button', { name: 'New story' }));
    await user.type(await screen.findByRole('textbox', { name: /^The story, in your own words/ }), RAW);
    await user.click(screen.getByRole('button', { name: 'Close: New story' }));
    const dialog = await screen.findByRole('alertdialog', { name: 'Discard what you wrote?' });
    await user.click(within(dialog).getByRole('button', { name: 'Keep writing' }));
    expect(screen.getByRole('textbox', { name: /^The story, in your own words/ })).toHaveValue(RAW);
    await user.click(screen.getByRole('button', { name: 'Close: New story' }));
    await user.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: 'Discard' }));
    expect(screen.queryByRole('heading', { level: 3, name: 'New story' })).not.toBeInTheDocument();
    expect(requests).toHaveLength(0);
  });
});
