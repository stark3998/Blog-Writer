import { screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';

import { apiUrl, problem } from '@/test/msw';
import { findPageHeading, renderApp } from '@/test/render-app';

import { jobView, page, source } from './testing/fixtures';
import { serveSettings } from './testing/handlers';

async function open() {
  const view = renderApp('/settings/sources');
  await findPageHeading('Settings');
  return view;
}

describe('Settings > Sources', () => {
  it('lists feeds and scouting queries with their health', async () => {
    serveSettings();
    await open();
    const list = await screen.findByRole('list', { name: 'Sources' });
    const rows = within(list).getAllByRole('listitem');
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent('Identity weekly');
    expect(rows[0]).toHaveTextContent('Identity modernization');
    expect(rows[0]).toHaveTextContent('132 seen');
    expect(rows[1]).toHaveTextContent('Weekly scouting query');
    expect(rows[1]).toHaveTextContent('Failing');
    expect(rows[1]).toHaveTextContent('The feed’s address answered “not found”. 5 times in a row.');
    expect(await screen.findByText('Last crawl')).toBeInTheDocument();
    expect(await screen.findByText(/^Next crawl /)).toBeInTheDocument();
  });

  it('shows the empty state when nothing is followed yet', async () => {
    serveSettings(http.get(apiUrl('/api/sources'), () => HttpResponse.json(page([]))));
    await open();
    const heading = await screen.findByRole('heading', { level: 2, name: 'No sources yet.' });
    expect(heading.closest('section')).toHaveTextContent('Your part');
  });

  it('adds a feed found on a site', async () => {
    let created: unknown;
    let reads = 0;
    serveSettings(
      http.get(apiUrl('/api/sources'), () => {
        reads += 1;
        return HttpResponse.json(page(reads > 1 ? [source()] : []));
      }),
      http.post(apiUrl('/api/sources/discover'), () =>
        HttpResponse.json({ feeds: [{ url: 'https://blog.example.org/rss', title: 'Example blog' }] }),
      ),
      http.post(apiUrl('/api/sources'), async ({ request }) => {
        created = await request.json();
        return HttpResponse.json(source({ title: 'Example blog' }), { status: 201 });
      }),
    );
    const { user } = await open();
    await user.click(await screen.findByRole('button', { name: 'Add a source' }));
    const dialog = await screen.findByRole('dialog', { name: 'Add a source' });
    await user.type(within(dialog).getByRole('textbox', { name: /Find the feed on a site/ }), 'https://blog.example.org');
    await user.click(within(dialog).getByRole('button', { name: 'Find feeds' }));
    await user.click(await within(dialog).findByRole('button', { name: 'Use Example blog' }));
    expect(within(dialog).getByRole('textbox', { name: /Feed address/ })).toHaveValue('https://blog.example.org/rss');
    expect(within(dialog).getByRole('textbox', { name: /^Name/ })).toHaveValue('Example blog');

    await user.click(within(dialog).getByRole('button', { name: 'Add the source' }));
    await waitFor(() => {
      expect(created).toEqual({
        type: 'feed',
        title: 'Example blog',
        url: 'https://blog.example.org/rss',
        siteUrl: 'https://blog.example.org',
        query: null,
        pillar: null,
      });
    });
    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });
    expect(await screen.findByRole('list', { name: 'Sources' })).toBeInTheDocument();
  });

  it('refuses an address that is not https before sending it', async () => {
    let calls = 0;
    serveSettings(
      http.post(apiUrl('/api/sources'), () => {
        calls += 1;
        return HttpResponse.json(source(), { status: 201 });
      }),
    );
    const { user } = await open();
    await user.click(await screen.findByRole('button', { name: 'Add a source' }));
    const dialog = await screen.findByRole('dialog', { name: 'Add a source' });
    await user.type(within(dialog).getByRole('textbox', { name: /Feed address/ }), 'http://insecure.example.com/feed');
    await user.type(within(dialog).getByRole('textbox', { name: /^Name/ }), 'Insecure');
    await user.click(within(dialog).getByRole('button', { name: 'Add the source' }));
    const address = within(dialog).getByRole('textbox', { name: /Feed address/ });
    expect(address).toHaveAttribute('aria-invalid', 'true');
    expect(address).toHaveFocus();
    expect(calls).toBe(0);
  });

  it('shows a duplicate and a blocklisted query on their fields', async () => {
    serveSettings(
      http.post(apiUrl('/api/sources'), async ({ request }) => {
        const body = (await request.json()) as { type: string };
        if (body.type === 'feed') return problem(409, 'duplicate_source');
        return HttpResponse.json(
          {
            type: 'about:blank',
            title: 'Unprocessable',
            status: 422,
            code: 'confidential_text',
            findings: [{ field: 'query', code: 'blocklist', severity: 'block', message: 'Blocked term', ref: 't-1' }],
          },
          { status: 422, headers: { 'Content-Type': 'application/problem+json' } },
        );
      }),
    );
    const { user } = await open();
    await user.click(await screen.findByRole('button', { name: 'Add a source' }));
    const dialog = await screen.findByRole('dialog', { name: 'Add a source' });
    await user.type(within(dialog).getByRole('textbox', { name: /Feed address/ }), 'https://news.example.com/feed.xml');
    await user.type(within(dialog).getByRole('textbox', { name: /^Name/ }), 'Again');
    await user.click(within(dialog).getByRole('button', { name: 'Add the source' }));
    expect(await within(dialog).findByText('You already follow this feed.')).toBeInTheDocument();

    await user.click(within(dialog).getByRole('radio', { name: 'A weekly scouting query' }));
    await user.type(within(dialog).getByRole('textbox', { name: /Search query/ }), 'secret client migration');
    await user.click(within(dialog).getByRole('button', { name: 'Add the source' }));
    expect(await within(dialog).findByText('This query names something on your blocklist. Rephrase it.')).toBeInTheDocument();
  });

  it('pauses a source with its version, from the row’s menu', async () => {
    let ifMatch: string | null = null;
    serveSettings(
      http.post(apiUrl('/api/sources/:id/pause'), ({ request }) => {
        ifMatch = request.headers.get('If-Match');
        return HttpResponse.json(source({ status: 'paused' }));
      }),
    );
    const { user } = await open();
    await user.click(await screen.findByRole('button', { name: 'Actions for “Identity weekly”' }));
    await user.click(await screen.findByRole('menuitem', { name: 'Pause' }));
    await waitFor(() => {
      expect(ifMatch).toBe('"feed-0001-1"');
    });
  });

  it('asks before it stops following a source', async () => {
    let deleted: string | null = null;
    serveSettings(
      http.delete(apiUrl('/api/sources/:id'), ({ request, params }) => {
        deleted = `${String(params.id)} ${request.headers.get('If-Match') ?? ''}`;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const { user } = await open();
    await user.click(await screen.findByRole('button', { name: 'Actions for “Identity weekly”' }));
    await user.click(await screen.findByRole('menuitem', { name: 'Stop following' }));
    const confirm = await screen.findByRole('alertdialog', { name: 'Stop following “Identity weekly”?' });
    expect(within(confirm).getByRole('button', { name: 'Keep it' })).toHaveFocus();
    await user.click(within(confirm).getByRole('button', { name: 'Stop following' }));
    await waitFor(() => {
      expect(deleted).toBe('feed-0001 "feed-0001-1"');
    });
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Add a source' })).toHaveFocus();
    });
  });

  it('starts a crawl and follows its steps', async () => {
    serveSettings(
      http.post(apiUrl('/api/sources/crawl'), () => HttpResponse.json({ jobId: 'job-crawl-9', status: 'queued' }, { status: 202 })),
      http.get(apiUrl('/api/jobs/:id'), () =>
        HttpResponse.json(
          jobView({
            jobId: 'job-crawl-9',
            status: 'running',
            finishedAt: null,
            steps: [
              { name: 'fetch', status: 'running', runs: 1, startedAt: '2026-11-27T14:00:00.000000Z', finishedAt: null },
              { name: 'triage', status: 'pending', runs: 0, startedAt: null, finishedAt: null },
            ],
          }),
        ),
      ),
    );
    const { user } = await open();
    await user.click(await screen.findByRole('button', { name: 'Crawl now' }));
    const timeline = await screen.findByRole('region', { name: 'Crawling the feeds' });
    expect(await within(timeline).findByText('Read the feeds')).toBeInTheDocument();
    expect(within(timeline).getByText('Sort new items into ideas')).toBeInTheDocument();
  });
});
