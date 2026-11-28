import { act, screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse, type HttpHandler } from 'msw';
import { describe, expect, it } from 'vitest';

import type { components } from '@/lib/api/schema';
import { MockEventSource } from '@/test/event-source';
import { snapshotPayload, stepPayload } from '@/test/job-events';
import { apiUrl, server } from '@/test/msw';
import { findPageHeading, renderApp } from '@/test/render-app';

import { articleDoc, ideaDoc, page, postDoc, versioned } from '../piece/testing/fixtures';
import { articleHandler, postHandler, problemResponse, settingsHandlers } from '../piece/testing/handlers';

import { briefDoc, CITATION_A, CITATION_B, quickSearchDoc, runDoc } from './testing/fixtures';
import { researchHandlers, type ResearchData } from './testing/handlers';

type Schemas = components['schemas'];

/** Test-specific handlers come first: msw uses the first one that matches. */
function setup(article: Schemas['Article'], research: ResearchData = {}, ...handlers: HttpHandler[]) {
  server.use(...handlers, ...settingsHandlers(), articleHandler([article]), ...researchHandlers(research));
}

async function openArticle(path = '/studio/art-1/brief') {
  const view = renderApp(path);
  await findPageHeading('The MCP threat model nobody ships');
  return view;
}

describe('the Brief tab of an article', () => {
  it('starts empty, honestly, with the way to research', async () => {
    setup(articleDoc());
    await openArticle();
    const empty = await screen.findByRole('heading', { level: 2, name: 'No brief yet.' });
    const section = empty.closest('section');
    if (!section) throw new Error('no section');
    expect(within(section).getByText('Your part')).toBeInTheDocument();
    expect(within(section).getByText(/“Start research” at the top begins it/)).toBeInTheDocument();
    expect(within(section).getByRole('button', { name: 'Research on its own' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { level: 2, name: 'Quick search' })).toBeInTheDocument();
  });

  it('starts a research run with your sources and follows it live', async () => {
    const runs: Schemas['ResearchRun'][] = [];
    let sent: unknown;
    setup(
      articleDoc(),
      {},
      http.get(apiUrl('/api/research/runs'), () => HttpResponse.json(page(runs))),
      http.post(apiUrl('/api/research/runs'), async ({ request }) => {
        const body = (await request.json()) as Schemas['RunIn'];
        sent = body;
        const run = runDoc({ id: 'run-new', status: 'queued', briefId: null, jobId: 'research-run-new', sources: [], topic: body.topic });
        runs.push(run);
        return HttpResponse.json({ run: versioned(run), jobId: 'research-run-new' }, { status: 202 });
      }),
    );
    const { user } = await openArticle();
    await user.click(await screen.findByRole('button', { name: 'Research on its own' }));
    const dialog = await screen.findByRole('dialog', { name: 'New research run' });
    expect(within(dialog).getByText(/The draft is written from the pipeline’s own research/)).toBeInTheDocument();
    expect(within(dialog).getByRole('textbox', { name: /Topic/ })).toHaveValue('The MCP threat model nobody ships');
    await waitFor(() => {
      expect(within(dialog).getByRole('combobox', { name: 'Pillar' })).toHaveTextContent('AI agents & security');
    });
    expect(within(dialog).getByRole('combobox', { name: /How recent/ })).toHaveTextContent('Last year');

    await user.click(within(dialog).getByRole('button', { name: 'Add a source link' }));
    const link = within(dialog).getByRole('textbox', { name: 'Source link 1' });
    await user.type(link, 'http://example.com/report');
    await user.click(within(dialog).getByRole('button', { name: 'Start research' }));
    expect(link).toHaveAccessibleDescription('Use an https:// link.');
    expect(link).toHaveFocus();
    expect(sent).toBeUndefined();

    await user.clear(link);
    await user.type(link, 'https://example.com/report');
    await user.type(within(dialog).getByRole('textbox', { name: /Audience/ }), 'Security architects');
    await user.click(within(dialog).getByRole('button', { name: 'Start research' }));

    expect(await screen.findByText('Research started.')).toBeInTheDocument();
    expect(sent).toEqual({
      topic: 'The MCP threat model nobody ships',
      pillar: 'agents',
      audience: 'Security architects',
      recencyDays: 365,
      ownerUrls: ['https://example.com/report'],
      articleId: 'art-1',
    });
    const progress = await screen.findByRole('region', { name: 'Research in progress' });
    expect(within(progress).getByText('The MCP threat model nobody ships')).toBeInTheDocument();

    // The job's steps arrive on the event stream.
    const stream = MockEventSource.latest();
    act(() => {
      stream.open();
      stream.emit(
        'snapshot',
        snapshotPayload({
          jobId: 'research-run-new',
          steps: [
            stepPayload('plan', 'succeeded'),
            stepPayload('search', 'running'),
            stepPayload('extract'),
            stepPayload('verify'),
            stepPayload('brief'),
            stepPayload('audit'),
          ],
        }),
        'research-run-new:3',
      );
    });
    const steps = await within(progress).findByRole('list', { name: 'Steps' });
    expect(within(steps).getAllByRole('listitem').map((item) => item.querySelector('p')?.textContent)).toEqual([
      'Plan the questions: done',
      'Search trusted sites and the open web: in progress',
      'Read the sources: not started',
      'Check dates, numbers and conflicts: not started',
      'Write the brief: not started',
      'Check everything against your blocklist: not started',
    ]);
    expect(screen.getByRole('button', { name: 'New research run' })).toBeDisabled();
  });

  it('puts a blocklist hit on the field it came from', async () => {
    setup(
      articleDoc(),
      {},
      http.post(apiUrl('/api/research/runs'), () =>
        problemResponse(422, 'confidential_text', {
          findings: [{ field: 'topic', code: 'blocklist', severity: 'block', message: 'Blocked term.', ref: 'term-1' }],
        }),
      ),
    );
    const { user } = await openArticle();
    await user.click(await screen.findByRole('button', { name: 'Research on its own' }));
    const dialog = await screen.findByRole('dialog', { name: 'New research run' });
    await waitFor(() => {
      expect(within(dialog).getByRole('combobox', { name: 'Pillar' })).toHaveTextContent('AI agents & security');
    });
    await user.click(within(dialog).getByRole('button', { name: 'Start research' }));
    const topic = within(dialog).getByRole('textbox', { name: /Topic/ });
    await waitFor(() => {
      expect(topic).toHaveAccessibleDescription(/This matches a term on your blocklist\. Rephrase it\./);
    });
    expect(topic).toHaveAttribute('aria-invalid', 'true');
    // Explained in place: no toast.
    expect(screen.queryByText('Research didn’t start.')).not.toBeInTheDocument();
  });

  it('shows the brief the draft uses: cited findings, the two-source rule, ideas and sources', async () => {
    setup(articleDoc({ status: 'in_review', briefId: 'brief-run-1' }), { runs: [runDoc()], briefs: [briefDoc()] });
    await openArticle();
    const brief = await screen.findByRole('article', { name: 'Brief' });
    expect(within(brief).getByText('Used for drafting')).toBeInTheDocument();
    expect(within(brief).getByText(/Passkeys are ready for workforce sign-in/)).toBeInTheDocument();

    const findings = within(brief).getByRole('region', { name: /^Findings/ });
    const chips = within(findings).getAllByRole('link', { name: /^Source: Passkeys in Microsoft Entra ID/ });
    expect(chips[0]).toHaveAccessibleName(
      'Source: Passkeys in Microsoft Entra ID, Microsoft Learn, Mar 2025, a trusted source (opens in a new tab)',
    );
    expect(chips[0]).toHaveAttribute('href', CITATION_A.url);
    expect(chips[0]).toHaveAttribute('target', '_blank');
    expect(chips[0]).toHaveAttribute('rel', 'noopener noreferrer');

    const numbers = within(brief).getByRole('region', { name: /^Numbers/ });
    expect(within(numbers).getByText('87%')).toBeInTheDocument();
    expect(within(numbers).getByText('Two independent sources')).toBeInTheDocument();
    expect(within(numbers).getByText('One source: check it')).toBeInTheDocument();

    expect(within(brief).getByText('Hardware keys stay cheaper to govern.')).toBeInTheDocument();
    expect(within(brief).getByText('Can a synced passkey be revoked centrally?')).toBeInTheDocument();
    expect(within(brief).getByText('What breaks first in an Okta-to-Entra move')).toBeInTheDocument();
    expect(within(brief).getByText('We turned off passwords for 2,000 people.')).toBeInTheDocument();

    const sources = within(brief).getByRole('region', { name: /^Sources/ });
    expect(within(sources).getByText('1 of 2 sources read in full; the rest come from search results.')).toBeInTheDocument();
    expect(within(sources).getByText('Trusted source')).toBeInTheDocument();

    const history = screen.getByRole('region', { name: 'Research runs' });
    expect(within(history).getByText('Shown above')).toBeInTheDocument();
    expect(within(history).getByText('$0.84')).toBeInTheDocument();
  });

  it('saves an edited summary with the brief’s etag', async () => {
    let sent: { ifMatch: string | null; body: unknown } | undefined;
    setup(
      articleDoc({ briefId: 'brief-run-1' }),
      { runs: [runDoc()], briefs: [briefDoc()] },
      http.put(apiUrl('/api/research/briefs/:briefId'), async ({ request }) => {
        sent = { ifMatch: request.headers.get('If-Match'), body: await request.json() };
        return HttpResponse.json(
          versioned(briefDoc({ executiveSummary: 'A sharper summary.', editedByOwner: true }), '"etag-2"'),
        );
      }),
    );
    const { user } = await openArticle();
    await user.click(await screen.findByRole('button', { name: 'Edit the summary' }));
    const field = screen.getByRole('textbox', { name: 'Summary' });
    await user.clear(field);
    await user.type(field, 'A sharper summary.');
    await user.click(screen.getByRole('button', { name: 'Save' }));

    expect(await screen.findByText('A sharper summary.', { selector: 'p' })).toBeInTheDocument();
    expect(sent).toEqual({ ifMatch: '"etag-1"', body: { executiveSummary: 'A sharper summary.' } });
    expect(screen.getByText('Edited by you')).toBeInTheDocument();
    expect(screen.queryByRole('textbox', { name: 'Summary' })).not.toBeInTheDocument();
    // The editor's buttons are gone: focus moved to the section heading.
    expect(screen.getByRole('heading', { level: 3, name: 'Summary' })).toHaveFocus();
  });

  it('opens the conflict dialog on a 412 and keeps mine with the newer etag', async () => {
    let briefReads = 0;
    const puts: (string | null)[] = [];
    setup(
      articleDoc({ briefId: 'brief-run-1' }),
      { runs: [runDoc()] },
      http.get(apiUrl('/api/research/briefs/:briefId'), () => {
        briefReads += 1;
        return briefReads === 1
          ? HttpResponse.json(versioned(briefDoc(), '"etag-1"'))
          : HttpResponse.json(versioned(briefDoc({ executiveSummary: 'Their summary.' }), '"etag-2"'));
      }),
      http.put(apiUrl('/api/research/briefs/:briefId'), ({ request }) => {
        puts.push(request.headers.get('If-Match'));
        return puts.length === 1
          ? problemResponse(412, 'etag_mismatch', {
              current: { etag: '"etag-2"', updatedAt: '2026-11-27T11:00:00.000000Z', updatedBy: 'owner' },
            })
          : HttpResponse.json(versioned(briefDoc({ executiveSummary: 'My summary.', editedByOwner: true }), '"etag-3"'));
      }),
    );
    const { user } = await openArticle();
    await user.click(await screen.findByRole('button', { name: 'Edit the summary' }));
    const field = screen.getByRole('textbox', { name: 'Summary' });
    await user.clear(field);
    await user.type(field, 'My summary.');
    await user.click(screen.getByRole('button', { name: 'Save' }));

    const dialog = await screen.findByRole('alertdialog', { name: 'The summary changed since you opened it' });
    expect(dialog).toHaveTextContent('You saved a newer version in another tab or on another device');
    // Once the newer version is in, the dialog compares it with mine.
    const diff = await within(dialog).findByRole('group', { name: 'Your version compared with the saved one' });
    expect(diff).toHaveTextContent(/removed Their/);
    expect(diff).toHaveTextContent(/added My/);
    await user.click(within(dialog).getByRole('button', { name: 'Keep mine' }));

    await waitFor(() => {
      expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
    });
    expect(puts).toEqual(['"etag-1"', '"etag-2"']);
    expect(await screen.findByText('My summary.', { selector: 'p' })).toBeInTheDocument();
  });

  it('removes sources after asking, with what goes with them', async () => {
    let sent: unknown;
    setup(
      articleDoc({ briefId: 'brief-run-1' }),
      { runs: [runDoc()], briefs: [briefDoc()] },
      http.put(apiUrl('/api/research/briefs/:briefId'), async ({ request }) => {
        sent = await request.json();
        return HttpResponse.json(versioned(briefDoc({ citations: [CITATION_A], editedByOwner: true }), '"etag-2"'));
      }),
    );
    const { user } = await openArticle();
    const sources = await screen.findByRole('region', { name: /^Sources/ });
    await user.click(within(sources).getByRole('checkbox', { name: 'Select “The state of passkeys” to remove it' }));
    await user.click(within(sources).getByRole('button', { name: 'Remove 1 source…' }));
    const dialog = await screen.findByRole('alertdialog', { name: 'Remove 1 source from the brief?' });
    expect(dialog).toHaveTextContent('numbers that rest only on them disappear');
    await user.click(within(dialog).getByRole('button', { name: 'Remove' }));

    await waitFor(() => {
      expect(sent).toEqual({ removeCitationIds: [CITATION_B.id] });
    });
    await waitFor(() => {
      expect(within(screen.getByRole('region', { name: /^Sources/ })).queryByText('The state of passkeys')).not.toBeInTheDocument();
    });
  });

  it('adds sources with a new run that starts from the brief and focuses the new link', async () => {
    setup(articleDoc({ status: 'in_review', briefId: 'brief-run-1' }), {
      runs: [runDoc({ ownerUrls: ['https://example.com/mine'] })],
      briefs: [briefDoc()],
    });
    const { user } = await openArticle();
    const sources = await screen.findByRole('region', { name: /^Sources/ });
    await user.click(within(sources).getByRole('button', { name: 'Add sources' }));
    const dialog = await screen.findByRole('dialog', { name: 'New research run' });
    expect(within(dialog).getByText('The draft keeps using its current brief. This run gives you another one to read.')).toBeInTheDocument();
    expect(within(dialog).getByRole('textbox', { name: /Topic/ })).toHaveValue('Passkeys for workforce identity in Entra');
    expect(within(dialog).getByRole('textbox', { name: 'Source link 1' })).toHaveValue('https://example.com/mine');
    await waitFor(() => {
      expect(within(dialog).getByRole('textbox', { name: 'Source link 2' })).toHaveFocus();
    });
  });

  it('shows a blocklist hit on the list item it came from', async () => {
    setup(
      articleDoc({ briefId: 'brief-run-1' }),
      { runs: [runDoc()], briefs: [briefDoc()] },
      http.put(apiUrl('/api/research/briefs/:briefId'), () =>
        problemResponse(422, 'confidential_text', {
          findings: [{ field: 'angles[1]', code: 'blocklist', severity: 'block', message: 'Blocked.', ref: 'term-4' }],
        }),
      ),
    );
    const { user } = await openArticle();
    await user.click(await screen.findByRole('button', { name: 'Edit the angles' }));
    await user.click(screen.getByRole('button', { name: 'Add an angle' }));
    const added = screen.getByRole('textbox', { name: 'Angle 2' });
    expect(added).toHaveFocus();
    await user.type(added, 'How Contoso did it');
    await user.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => {
      expect(screen.getByRole('textbox', { name: 'Angle 2' })).toHaveAccessibleDescription(
        'This matches a term on your blocklist. Rephrase it.',
      );
    });
    expect(screen.getByRole('alert')).toHaveTextContent('Your change wasn’t saved.');
  });

  it('cancels a running run after asking, with its etag', async () => {
    let ifMatch: string | null | undefined;
    setup(
      articleDoc({ status: 'researching' }),
      { runs: [runDoc({ status: 'running', briefId: null })] },
      http.post(apiUrl('/api/research/runs/:runId/cancel'), ({ request }) => {
        ifMatch = request.headers.get('If-Match');
        return HttpResponse.json(versioned(runDoc({ status: 'cancelled', briefId: null, errorCode: 'cancelled' }), '"etag-2"'));
      }),
    );
    const { user } = await openArticle();
    const progress = await screen.findByRole('region', { name: 'Research in progress' });
    await user.click(within(progress).getByRole('button', { name: 'Cancel research' }));
    const dialog = await screen.findByRole('alertdialog', { name: 'Cancel this research run?' });
    expect(within(dialog).getByRole('button', { name: 'Keep researching' })).toHaveFocus();
    await user.click(within(dialog).getByRole('button', { name: 'Cancel research' }));
    expect(await screen.findByText('Research cancelled.')).toBeInTheDocument();
    expect(ifMatch).toBe('"etag-1"');
  });

  it('leads with a stopped run and runs it again with the same settings', async () => {
    setup(articleDoc(), {
      runs: [
        runDoc({
          status: 'failed',
          errorCode: 'no_sources',
          briefId: null,
          topic: 'Agent identity in practice',
          ownerUrls: ['https://example.com/a'],
        }),
      ],
    });
    const { user } = await openArticle();
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('The last research run stopped.');
    expect(alert).toHaveTextContent('The research found no usable sources for this topic.');
    await user.click(within(alert).getByRole('button', { name: 'Run it again' }));
    const dialog = await screen.findByRole('dialog', { name: 'New research run' });
    expect(within(dialog).getByRole('textbox', { name: /Topic/ })).toHaveValue('Agent identity in practice');
    expect(within(dialog).getByRole('textbox', { name: 'Source link 1' })).toHaveValue('https://example.com/a');
  });

  it('shows another run’s brief on request and says which one the draft uses', async () => {
    const older = runDoc();
    const newer = runDoc({ id: 'run-2', createdAt: '2026-11-28T10:00:00.000000Z', briefId: 'brief-run-2', topic: 'Passkey recovery' });
    setup(articleDoc({ status: 'in_review', briefId: 'brief-run-1' }), {
      runs: [older, newer],
      briefs: [briefDoc(), briefDoc({ id: 'brief-run-2', runId: 'run-2', executiveSummary: 'Recovery is the hard part.' })],
    });
    const { user } = await openArticle();
    await screen.findByText(/Passkeys are ready for workforce sign-in/);
    const history = screen.getByRole('region', { name: 'Research runs' });
    await user.click(within(history).getByRole('button', { name: 'Show this brief' }));
    expect(await screen.findByText('Recovery is the hard part.')).toBeInTheDocument();
    expect(screen.getByText('The draft is written from another brief: the one marked “Used for drafting” below.')).toBeInTheDocument();
  });
});

describe('the Brief tab of a post', () => {
  it('shows the idea and the search the post cites', async () => {
    server.use(
      ...settingsHandlers(),
      postHandler([postDoc({ ideaId: 'idea-1', quickSearchId: 'qs-1' })]),
      ...researchHandlers({ quick: [quickSearchDoc({ articleId: null, ideaId: 'idea-1' })] }),
      http.get(apiUrl('/api/ideas/:ideaId'), () => HttpResponse.json(versioned(ideaDoc({ url: 'https://example.com/news', publisher: 'Example News' })))),
    );
    renderApp('/studio/post-1/brief');
    await findPageHeading('Okta to Entra in 90 days: what broke first');
    const idea = await screen.findByRole('region', { name: 'The idea' });
    expect(within(idea).getByText('Entra adds passkey sync for workforce accounts')).toBeInTheDocument();
    expect(within(idea).getByRole('link', { name: /Read it on Example News/ })).toHaveAttribute('href', 'https://example.com/news');
    const cited = await screen.findByRole('region', { name: 'Cited in this post' });
    expect(within(cited).getByText('Sync arrived in 2025.')).toBeInTheDocument();
  });

  it('starts empty for a post without research', async () => {
    server.use(...settingsHandlers(), postHandler([postDoc()]), ...researchHandlers());
    renderApp('/studio/post-1/brief');
    await findPageHeading('Okta to Entra in 90 days: what broke first');
    expect(await screen.findByRole('heading', { level: 2, name: 'No brief yet.' })).toBeInTheDocument();
    expect(screen.getByText(/A LinkedIn post doesn’t get a full research run/)).toBeInTheDocument();
  });
});
