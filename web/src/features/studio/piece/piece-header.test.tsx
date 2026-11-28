import { screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse, type HttpHandler } from 'msw';
import { describe, expect, it } from 'vitest';

import { idea as calendarIdea } from '@/features/calendar/testing/fixtures';
import { calendarServer } from '@/features/calendar/testing/handlers';
import { apiUrl, server } from '@/test/msw';
import { findPageHeading, renderApp } from '@/test/render-app';

import { researchHandlers } from '../brief/testing/handlers';

import { articleDoc, postDoc, servedBy, versioned } from './testing/fixtures';
import { articleHandler, postHandler, problemResponse, settingsHandlers } from './testing/handlers';

/** Test-specific handlers come first: msw uses the first one that matches. */
function setup(...handlers: HttpHandler[]) {
  server.use(...handlers, ...settingsHandlers(), ...researchHandlers());
}

function nextStep(): HTMLElement {
  const element = document.querySelector<HTMLElement>('[data-slot="next-step"]');
  if (!element) throw new Error('no next step');
  return element;
}

describe('the piece header', () => {
  it('shows the article’s title, status, pillar, format and next step', async () => {
    setup(articleHandler([articleDoc()]));
    renderApp('/studio/art-1/brief');

    expect(await findPageHeading('The MCP threat model nobody ships')).toBeInTheDocument();
    const facts = screen.getByRole('list', { name: 'About this article' });
    expect(within(facts).getByText('Planned')).toBeInTheDocument();
    expect(await within(facts).findByText('AI agents & security')).toBeInTheDocument();
    expect(await within(facts).findByText('Deep explainer article')).toBeInTheDocument();
    expect(within(nextStep()).getByText(/Start research when you’re ready/)).toBeInTheDocument();
    // Enabled once the state machines say planned -> researching exists.
    await waitFor(() => {
      expect(within(nextStep()).getByRole('button', { name: 'Start research' })).toBeEnabled();
    });
    await waitFor(() => {
      expect(document.title).toBe('Brief & research – The MCP threat model nobody ships – Studio – Blog Writer');
    });
    expect(screen.getByRole('link', { name: 'Back to Studio' })).toHaveAttribute('href', '/studio');
  });

  it('starts research with the article’s etag and shows the new status', async () => {
    let sent: { ifMatch: string | null; body: unknown } | undefined;
    setup(
      articleHandler([articleDoc()]),
      http.post(apiUrl('/api/articles/:articleId/pipeline'), async ({ request }) => {
        sent = { ifMatch: request.headers.get('If-Match'), body: await request.json() };
        return HttpResponse.json(
          { article: versioned(articleDoc({ status: 'researching', pipelineJobId: 'pipeline-art-1-1' }), '"etag-2"'), jobId: 'pipeline-art-1-1' },
          { status: 202 },
        );
      }),
    );
    const { user } = renderApp('/studio/art-1/brief');
    await findPageHeading('The MCP threat model nobody ships');
    const start = await within(nextStep()).findByRole('button', { name: 'Start research' });
    await waitFor(() => {
      expect(start).toBeEnabled();
    });
    await user.click(start);

    expect(await screen.findByText('Research started.')).toBeInTheDocument();
    expect(sent).toEqual({ ifMatch: '"etag-1"', body: { startFrom: 'research' } });
    const facts = screen.getByRole('list', { name: 'About this article' });
    expect(await within(facts).findByText('Researching')).toBeInTheDocument();
    // Already on the Brief tab: the step says what happens, without a link to where we are.
    expect(within(nextStep()).getByText('Research is running. The interview questions come next.')).toBeInTheDocument();
    expect(within(nextStep()).queryByRole('link')).not.toBeInTheDocument();
  });

  it('explains a refused start and links to the fix', async () => {
    setup(
      articleHandler([articleDoc()]),
      http.post(apiUrl('/api/articles/:articleId/pipeline'), () =>
        problemResponse(409, 'voice_guide_missing', { detail: 'Activate a Voice & Style Guide in Library > Voice first.' }),
      ),
    );
    const { user } = renderApp('/studio/art-1/brief');
    const start = await within(await waitForNextStep()).findByRole('button', { name: 'Start research' });
    await waitFor(() => {
      expect(start).toBeEnabled();
    });
    await user.click(start);

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Research didn’t start.');
    expect(alert).toHaveTextContent('Activate a Voice & Style Guide in Library > Voice first.');
    expect(alert).toHaveTextContent('req-studio-1');
    expect(within(alert).getByRole('link', { name: 'Open Voice guide' })).toHaveAttribute('href', '/library/voice');
    // Handled in place: no toast repeats it.
    expect(screen.queryByText('Couldn’t start research.')).not.toBeInTheDocument();
  });

  it('keeps the next step usable when the state machines cannot be loaded', async () => {
    setup(
      articleHandler([articleDoc()]),
      http.get(apiUrl('/api/meta/machines'), () => problemResponse(422, 'validation_failed')),
    );
    renderApp('/studio/art-1/brief');
    const start = await within(await waitForNextStep()).findByRole('button', { name: 'Start research' });
    await waitFor(() => {
      expect(start).toBeEnabled();
    });
  });

  it('names what Research needs when the article details are missing', async () => {
    setup(
      articleHandler([articleDoc()]),
      http.post(apiUrl('/api/articles/:articleId/pipeline'), () =>
        problemResponse(409, 'meta_incomplete', { missing: ['pillar', 'format'] }),
      ),
    );
    const { user } = renderApp('/studio/art-1/brief');
    const start = await within(await waitForNextStep()).findByRole('button', { name: 'Start research' });
    await waitFor(() => {
      expect(start).toBeEnabled();
    });
    await user.click(start);
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Research needs a pillar and a format first.');
    expect(within(alert).getByRole('link', { name: 'Fill in the details' })).toHaveAttribute('href', '/studio/art-1/draft');
  });

  it('links an article in review to its draft, and shows review-only and the models', async () => {
    setup(
      articleHandler([
        articleDoc({
          status: 'in_review',
          wordCount: 2140,
          reviewOnly: true,
          reviewOnlyReasons: ['fallback_model'],
          servedBy: [servedBy({ step: 'outline', model: 'gpt-6.2-sol', provider: 'openai', costUsd: 0.05 }), servedBy({ fallbackReason: 'rate_limited' })],
        }),
      ]),
    );
    const { user } = renderApp('/studio/art-1/brief');
    await findPageHeading('The MCP threat model nobody ships');
    expect(within(nextStep()).getByRole('link', { name: 'Review the draft' })).toHaveAttribute('href', '/studio/art-1/draft');
    expect(screen.getByText('2,140 words')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Review only' }));
    const review = await screen.findByRole('dialog', { name: 'You review it before it goes out' });
    expect(review).toHaveTextContent('A fallback model wrote part of it');
    await user.keyboard('{Escape}');

    await user.click(screen.getByRole('button', { name: 'Models: claude-opus-5-5' }));
    const models = await screen.findByRole('dialog', { name: 'Models that worked on it' });
    expect(within(models).getByText('Outline')).toBeInTheDocument();
    expect(within(models).getByText('gpt-6.2-sol')).toBeInTheDocument();
    expect(within(models).getByText(/fallback \(Rate limited\)/)).toBeInTheDocument();
    expect(within(models).getByText('Total $0.47')).toBeInTheDocument();
  });

  it('says so when the piece does not exist', async () => {
    setup(articleHandler([]));
    renderApp('/studio/art-404/brief');
    expect(await findPageHeading('Piece not found')).toBeInTheDocument();
    expect(screen.getByText('This article isn’t there any more, or the link is wrong.')).toBeInTheDocument();
  });

  it('shows a load failure with its request id and a retry', async () => {
    let calls = 0;
    setup(
      http.get(apiUrl('/api/articles/:articleId'), () => {
        calls += 1;
        return calls === 1 ? problemResponse(409, 'conflict') : HttpResponse.json(versioned(articleDoc()));
      }),
    );
    const { user } = renderApp('/studio/art-1/brief');
    expect(await findPageHeading('Untitled piece')).toBeInTheDocument();
    const alert = screen.getAllByRole('alert')[0];
    expect(alert).toHaveTextContent('Couldn’t load this article.');
    await user.click(screen.getByRole('button', { name: 'Try loading the article again' }));
    expect(await findPageHeading('The MCP threat model nobody ships')).toBeInTheDocument();
  });

  it('titles a post by its first line and sends a ready post to go-live', async () => {
    setup(
      postHandler([
        postDoc({
          status: 'staged',
          slot: {
            localDate: '2026-12-01',
            localTime: '08:30',
            timezone: 'America/New_York',
            utc: '2026-12-01T13:30:00.000000Z',
            kind: 'primary',
          },
        }),
      ]),
    );
    renderApp('/studio/post-1/brief');
    expect(await findPageHeading('Okta to Entra in 90 days: what broke first')).toBeInTheDocument();
    const facts = screen.getByRole('list', { name: 'About this post' });
    expect(within(facts).getByText('Ready')).toBeInTheDocument();
    expect(within(facts).getByText('Carousel')).toBeInTheDocument();
    expect(within(facts).getByText('Quick take')).toBeInTheDocument();
    expect(await within(facts).findByText('Identity modernization')).toBeInTheDocument();
    expect(within(nextStep()).getByText(/^Ready to post at .+New York time\.$/)).toBeInTheDocument();
    expect(within(nextStep()).getByRole('link', { name: 'Open go-live' })).toHaveAttribute(
      'href',
      '/studio/post-1/go-live?post=post-1',
    );
  });

  it('archives an article after asking, with its etag', async () => {
    let sent: { ifMatch: string | null; body: unknown } | undefined;
    setup(
      articleHandler([articleDoc()]),
      http.post(apiUrl('/api/articles/:articleId/transition'), async ({ request }) => {
        sent = { ifMatch: request.headers.get('If-Match'), body: await request.json() };
        return HttpResponse.json(versioned(articleDoc({ status: 'archived' }), '"etag-2"'));
      }),
    );
    const { user } = renderApp('/studio/art-1/brief');
    await findPageHeading('The MCP threat model nobody ships');
    const more = await screen.findByRole('button', { name: 'More actions for this piece' });
    more.focus();
    await user.keyboard('{Enter}');
    await user.click(await screen.findByRole('menuitem', { name: 'Archive…' }));
    const dialog = await screen.findByRole('alertdialog', { name: 'Archive this article?' });
    // The safe answer has focus.
    expect(within(dialog).getByRole('button', { name: 'Keep it' })).toHaveFocus();
    await user.click(within(dialog).getByRole('button', { name: 'Archive' }));

    expect(await screen.findByText('Article archived.')).toBeInTheDocument();
    expect(sent).toEqual({ ifMatch: '"etag-1"', body: { to: 'archived', reason: 'Archived by the owner.' } });
    expect(await within(nextStep()).findByRole('button', { name: 'Restore' })).toBeInTheDocument();
  });

  it('reloads instead of archiving over a newer version (412)', async () => {
    let reads = 0;
    setup(
      http.get(apiUrl('/api/articles/:articleId'), () => {
        reads += 1;
        return HttpResponse.json(versioned(articleDoc(), reads === 1 ? '"etag-1"' : '"etag-2"'));
      }),
      http.post(apiUrl('/api/articles/:articleId/transition'), () =>
        problemResponse(412, 'etag_mismatch', { current: { etag: '"etag-2"', updatedBy: 'pipeline' } }),
      ),
    );
    const { user } = renderApp('/studio/art-1/brief');
    await findPageHeading('The MCP threat model nobody ships');
    const more = await screen.findByRole('button', { name: 'More actions for this piece' });
    more.focus();
    await user.keyboard('{Enter}');
    await user.click(await screen.findByRole('menuitem', { name: 'Archive…' }));
    await user.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: 'Archive' }));

    expect(await screen.findByText('It changed since you opened it. Check it, then archive it again.')).toBeInTheDocument();
    await waitFor(() => {
      expect(reads).toBeGreaterThan(1);
    });
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
  });

  it('opens the new idea from the toast: shown first in the ideas inbox, with its own start, never the take sheet', async () => {
    const candidate = calendarIdea({
      id: 'idea-repurpose-2',
      title: 'Flagship candidate: Okta to Entra in 90 days',
      origin: 'repurpose',
      pillar: 'identity',
      suggestedLane: 'flagship',
      repurposeOf: 'post-1',
    });
    // The ideas inbox's reads come from the Calendar's test server (the Studio's answer first).
    server.use(...calendarServer({ ideas: [candidate] }).handlers);
    setup(
      postHandler([postDoc({ status: 'posted', postedAt: '2026-11-20T15:00:00.000000Z' })]),
      http.post(apiUrl('/api/ideas/repurpose'), () => HttpResponse.json(versioned(candidate), { status: 201 })),
    );
    const { user, router } = renderApp('/studio/post-1/brief');
    await findPageHeading('Okta to Entra in 90 days: what broke first');
    await user.click(await screen.findByRole('button', { name: 'More actions for this piece' }));
    await user.click(await screen.findByRole('menuitem', { name: 'Turn it into an article idea' }));
    const notice = await screen.findByText('An article idea from this post is in your ideas.');
    const toast = notice.closest('li');
    if (!toast) throw new Error('no toast');
    await user.click(within(toast).getByRole('button', { name: 'Open it' }));

    await waitFor(() => {
      expect(`${router.state.location.pathname}${router.state.location.search}`).toBe('/calendar/ideas?idea=idea-repurpose-2');
    });
    const opened = await screen.findByRole('region', { name: 'The idea you opened' });
    expect(await within(opened).findByRole('button', { name: 'Start the article…' })).toBeInTheDocument();
    expect(within(opened).queryByRole('button', { name: 'What’s your take?' })).not.toBeInTheDocument();
    expect(screen.queryByRole('dialog', { name: 'What’s your take?' })).not.toBeInTheDocument();
  });

  it('makes a post that went out into a new idea (repurpose)', async () => {
    const sent: unknown[] = [];
    setup(
      postHandler([postDoc({ status: 'posted', postedAt: '2026-11-20T15:00:00.000000Z' })]),
      http.post(apiUrl('/api/ideas/repurpose'), async ({ request }) => {
        sent.push(await request.json());
        return HttpResponse.json(
          versioned({
            id: 'idea-repurpose-1',
            schemaVersion: 1,
            createdAt: '2026-11-27T12:00:00.000000Z',
            updatedAt: '2026-11-27T12:00:00.000000Z',
            status: 'new',
            statusChangedAt: '2026-11-27T12:00:00.000000Z',
            title: 'A new angle on: Okta to Entra in 90 days',
            origin: 'repurpose',
            repurposeOf: 'post-1',
          }),
          { status: 201 },
        );
      }),
    );
    const { user } = renderApp('/studio/post-1/brief');
    await findPageHeading('Okta to Entra in 90 days: what broke first');
    await user.click(await screen.findByRole('button', { name: 'More actions for this piece' }));
    // A posted post cannot be archived, but it can become an idea.
    expect(screen.queryByRole('menuitem', { name: 'Archive…' })).not.toBeInTheDocument();
    await user.click(await screen.findByRole('menuitem', { name: 'Repurpose as a new post' }));
    expect(await screen.findByText('A new angle on this post is in your ideas.')).toBeInTheDocument();
    expect(sent).toEqual([{ postId: 'post-1', kind: 'recut' }]);
    await user.click(screen.getByRole('button', { name: 'More actions for this piece' }));
    await user.click(await screen.findByRole('menuitem', { name: 'Turn it into an article idea' }));
    await waitFor(() => {
      expect(sent).toEqual([
        { postId: 'post-1', kind: 'recut' },
        { postId: 'post-1', kind: 'flagship' },
      ]);
    });
  });

  it('offers no repurpose before a post went out', async () => {
    setup(postHandler([postDoc({ status: 'draft' })]));
    const { user } = renderApp('/studio/post-1/brief');
    await findPageHeading('Okta to Entra in 90 days: what broke first');
    await user.click(await screen.findByRole('button', { name: 'More actions for this piece' }));
    expect(await screen.findByRole('menuitem', { name: 'Archive…' })).toBeInTheDocument();
    expect(screen.queryByRole('menuitem', { name: 'Repurpose as a new post' })).not.toBeInTheDocument();
  });

  it('restores an archived article to planned', async () => {
    let sent: unknown;
    setup(
      articleHandler([articleDoc({ status: 'archived' })]),
      http.post(apiUrl('/api/articles/:articleId/transition'), async ({ request }) => {
        sent = await request.json();
        return HttpResponse.json(versioned(articleDoc({ status: 'planned' }), '"etag-2"'));
      }),
    );
    const { user } = renderApp('/studio/art-1/brief');
    const restore = await within(await waitForNextStep()).findByRole('button', { name: 'Restore' });
    await waitFor(() => {
      expect(restore).toBeEnabled();
    });
    await user.click(restore);
    expect(await screen.findByText('Restored. It’s planned again.')).toBeInTheDocument();
    expect(sent).toEqual({ to: 'planned', reason: 'Restored from the archive.' });
  });
});

async function waitForNextStep(): Promise<HTMLElement> {
  await waitFor(() => {
    expect(document.querySelector('[data-slot="next-step"]')).not.toBeNull();
  });
  return nextStep();
}
