import { screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { toast } from 'sonner';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { apiUrl, problem } from '@/test/msw';
import { findPageHeading, renderApp } from '@/test/render-app';

import { NOW_MS } from '../settings/testing/fixtures';
import { experiment, overview, post, suggestion } from './testing/fixtures';
import { serveInsights } from './testing/handlers';

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(NOW_MS);
});

afterEach(() => {
  vi.useRealTimers();
});

async function open(options: { viewport?: 'desktop' | 'mobile' } = {}) {
  const view = renderApp('/settings/insights', options);
  await findPageHeading('Settings');
  return view;
}

const region = (name: string) => screen.getByRole('region', { name });

describe('Insights: rates against the median', () => {
  it('compares each pillar with the owner’s median, and switches dimension', async () => {
    serveInsights();
    const { user } = await open();
    const block = region('How your posts do');
    const chart = await within(block).findByRole('figure', { name: 'All engagement by pillar, last 12 weeks' });
    const rows = within(chart).getAllByRole('listitem');
    expect(rows[0]).toHaveTextContent('AI agents & security');
    expect(rows[0]).toHaveTextContent('5 posts');
    expect(rows[0]).toHaveTextContent('25% above your median');
    expect(rows[1]).toHaveTextContent('20% below your median');
    expect(rows[2]).toHaveTextContent('too few to read much into');

    await user.click(within(block).getByRole('radio', { name: 'Format' }));
    const formats = await within(block).findByRole('figure', { name: 'All engagement by format, last 12 weeks' });
    expect(within(formats).getAllByRole('listitem')[0]).toHaveTextContent('Carousel');
  });

  it('offers the same numbers as a table', async () => {
    serveInsights();
    const { user } = await open();
    const block = region('How your posts do');
    await user.click(await within(block).findByRole('button', { name: 'Show as a table' }));
    const table = within(block).getByRole('table', { name: /All engagement by pillar/ });
    expect(within(table).getByRole('row', { name: /AI agents & security/ })).toHaveTextContent('+25%');
    expect(within(block).getByRole('button', { name: 'Show the chart' })).toHaveAttribute('aria-pressed', 'true');
  });

  it('asks for snapshots before there is anything to compare', async () => {
    serveInsights(http.get(apiUrl('/api/analytics/overview'), () => HttpResponse.json(overview({ posts: 0, rows: [] }))));
    await open();
    const heading = await screen.findByRole('heading', { level: 2, name: 'No insights yet.' });
    expect(heading.closest('section')).toHaveTextContent('Your part');
  });

  it('reads a different window from the server', async () => {
    const windows: (string | null)[] = [];
    serveInsights(
      http.get(apiUrl('/api/analytics/overview'), ({ request }) => {
        windows.push(new URL(request.url).searchParams.get('weeks'));
        return HttpResponse.json(overview());
      }),
    );
    const { user } = await open();
    const block = region('How your posts do');
    await within(block).findByRole('figure', { name: /by pillar/ });
    await user.click(within(block).getByRole('radio', { name: '26 weeks' }));
    await waitFor(() => {
      expect(windows).toEqual(['12', '26']);
    });
  });
});

describe('Insights: the snapshot form', () => {
  it('offers the post that is due first and saves its +48 h numbers with its version', async () => {
    let sent: { ifMatch: string | null; body: unknown } | undefined;
    serveInsights(
      http.post(apiUrl('/api/posts/:id/snapshots'), async ({ request }) => {
        sent = { ifMatch: request.headers.get('If-Match'), body: await request.json() };
        return HttpResponse.json(post({}, '"post-1-v4"'));
      }),
    );
    const success = vi.spyOn(toast, 'success');
    const { user } = await open({ viewport: 'mobile' });
    const form = await screen.findByRole('form', { name: 'Snapshot numbers' });
    expect(screen.getByRole('combobox', { name: 'Post' })).toHaveTextContent('We shipped an MCP server without a token store');
    expect(within(form).getByRole('radio', { name: '+48 h' })).toBeChecked();

    await user.type(within(form).getByRole('textbox', { name: /Impressions/ }), '2,400');
    await user.type(within(form).getByRole('textbox', { name: 'Reactions' }), '48');
    await user.type(within(form).getByRole('textbox', { name: 'Comments' }), '9');
    await user.click(within(form).getByRole('button', { name: 'Save the numbers' }));

    await waitFor(() => {
      expect(sent).toEqual({
        ifMatch: '"post-1-v3"',
        body: { offset: 'h48', impressions: 2400, reactions: 48, comments: 9, reposts: null, saves: null, sends: null },
      });
    });
    await waitFor(() => {
      expect(success).toHaveBeenCalledWith('Numbers saved.', expect.anything());
    });
  });

  it('needs at least one number, and only whole numbers', async () => {
    let calls = 0;
    serveInsights(
      http.post(apiUrl('/api/posts/:id/snapshots'), () => {
        calls += 1;
        return HttpResponse.json(post());
      }),
    );
    const { user } = await open();
    const form = await screen.findByRole('form', { name: 'Snapshot numbers' });
    await user.click(within(form).getByRole('button', { name: 'Save the numbers' }));
    expect(within(form).getByRole('alert')).toHaveTextContent('Enter at least one number.');

    const saves = within(form).getByRole('textbox', { name: 'Saves' });
    await user.type(saves, '1.5');
    await user.click(within(form).getByRole('button', { name: 'Save the numbers' }));
    expect(saves).toHaveAttribute('aria-invalid', 'true');
    expect(saves).toHaveFocus();
    expect(calls).toBe(0);
  });

  it('prefills a logged snapshot so saving corrects it', async () => {
    serveInsights(
      http.get(apiUrl('/api/posts/:id'), () =>
        HttpResponse.json(
          post({
            snapshots: [
              { id: 'form-h48', takenAt: '2026-11-27T10:00:00.000000Z', offset: 'h48', impressions: 1800, reactions: 30, source: 'form' },
            ],
          }),
        ),
      ),
    );
    await open();
    const form = await screen.findByRole('form', { name: 'Snapshot numbers' });
    expect(within(form).getByRole('textbox', { name: /Impressions/ })).toHaveValue('1800');
    expect(within(form).getByText(/Saving again corrects it/)).toBeInTheDocument();
  });

  it('keeps the numbers and explains when the post changed meanwhile', async () => {
    serveInsights(http.post(apiUrl('/api/posts/:id/snapshots'), () => problem(412, 'etag_mismatch')));
    const { user } = await open();
    const form = await screen.findByRole('form', { name: 'Snapshot numbers' });
    await user.type(within(form).getByRole('textbox', { name: /Impressions/ }), '900');
    await user.click(within(form).getByRole('button', { name: 'Save the numbers' }));
    expect(await within(form).findByText('This post changed since you opened it.')).toBeInTheDocument();
    expect(within(form).getByRole('textbox', { name: /Impressions/ })).toHaveValue('900');
  });
});

describe('Insights: suggestions and experiments', () => {
  it('records the owner’s decision on a suggestion', async () => {
    let sent: { ifMatch: string | null; body: unknown } | undefined;
    serveInsights(
      http.post(apiUrl('/api/analytics/suggestions/:id/decide'), async ({ request }) => {
        sent = { ifMatch: request.headers.get('If-Match'), body: await request.json() };
        return HttpResponse.json(suggestion({ status: 'accepted' }));
      }),
    );
    const { user } = await open();
    const block = region('Suggestions');
    expect(await within(block).findByText('Format: Carousel')).toBeInTheDocument();
    await user.click(within(block).getByRole('button', { name: /^Accept: Try one more carousel/ }));
    await waitFor(() => {
      expect(sent).toEqual({ ifMatch: '"sug-1-1"', body: { decision: 'accept' } });
    });
  });

  it('writes down a new experiment before it runs', async () => {
    let created: unknown;
    serveInsights(
      http.get(apiUrl('/api/analytics/experiments'), () => HttpResponse.json({ items: [], nextCursor: null })),
      http.post(apiUrl('/api/analytics/experiments'), async ({ request }) => {
        created = await request.json();
        return HttpResponse.json(experiment({ status: 'draft' }), { status: 201 });
      }),
    );
    const { user } = await open();
    const block = region('Experiments');
    await user.click(await within(block).findByRole('button', { name: 'Write down an experiment' }));
    const dialog = await screen.findByRole('dialog', { name: 'Write down an experiment' });
    await user.click(within(dialog).getByRole('radio', { name: 'Carousel against image card' }));
    await user.type(within(dialog).getByRole('textbox', { name: /What you expect/ }), 'Carousels earn more saves.');
    await user.click(within(dialog).getByRole('button', { name: 'Write it down' }));
    await waitFor(() => {
      expect(created).toEqual({
        question: 'carousel_vs_image_card',
        hypothesis: 'Carousels earn more saves.',
        metric: 'engagement_rate',
        minPostsPerArm: 8,
        arms: [
          { id: 'carousel', label: 'Carousel', postIds: [] },
          { id: 'image-card', label: 'Image card', postIds: [] },
        ],
      });
    });
  });

  it('shows how far each arm is when it is too early to conclude', async () => {
    serveInsights(
      http.post(apiUrl('/api/analytics/experiments/:id/transition'), () =>
        HttpResponse.json(
          {
            type: 'about:blank',
            title: 'Conflict',
            status: 409,
            code: 'too_few_posts',
            detail: 'Every arm needs 8 measured posts to conclude.',
            required: 8,
            arms: [
              { id: 'no-link', posts: 2 },
              { id: 'one-link', posts: 1 },
            ],
          },
          { status: 409, headers: { 'Content-Type': 'application/problem+json' } },
        ),
      ),
    );
    const { user } = await open();
    const block = region('Experiments');
    await user.click(await within(block).findByRole('button', { name: 'Conclude' }));
    const dialog = await screen.findByRole('dialog', { name: 'Conclude this experiment?' });
    await user.click(within(dialog).getByRole('button', { name: 'Conclude it' }));
    expect(await within(dialog).findByText('Measured posts so far: No link: 2, One link: 1.')).toBeInTheDocument();
  });
});

describe('Insights: spend', () => {
  it('shows today against the brake and steps through the days from the keyboard', async () => {
    serveInsights();
    const { user } = await open();
    const block = region('Spend');
    expect(await within(block).findByText('The brake engages at $20.00.')).toBeInTheDocument();
    const days = within(block).getByRole('list', { name: 'Spend per day' });
    const items = within(days).getAllByRole('listitem');
    expect(items).toHaveLength(3);
    expect(items[2]).toHaveTextContent('(today): $4.12');
    expect(items[2]).toHaveAttribute('tabindex', '0');

    items[2]?.focus();
    await user.keyboard('{ArrowLeft}');
    expect(items[1]).toHaveFocus();
    expect(within(block).getByRole('figure')).toHaveTextContent('$3.10 on Thu, Nov 26');
  });

  it('sends the export file itself as the raw body, with its type and name', async () => {
    let sent: { type: string | null; filename: string | null; body: string } | undefined;
    serveInsights(
      http.post(apiUrl('/api/analytics/imports'), async ({ request }) => {
        sent = {
          type: request.headers.get('Content-Type'),
          filename: new URL(request.url).searchParams.get('filename'),
          body: await request.text(),
        };
        return HttpResponse.json(
          {
            item: {
              id: 'imp-1',
              schemaVersion: 1,
              createdAt: '2026-11-27T12:00:00.000000Z',
              updatedAt: '2026-11-27T12:00:00.000000Z',
              filename: 'export.csv',
              sha256: 'b'.repeat(64),
              rows: 2,
              matched: 1,
              unmatched: 1,
              importedAt: '2026-11-27T12:00:00.000000Z',
            },
            etag: '"imp-1"',
          },
          { status: 201 },
        );
      }),
    );
    const { user } = await open();
    const block = region('LinkedIn’s export');
    const input = block.querySelector<HTMLInputElement>('input[type="file"]');
    if (!input) throw new Error('no file input');
    await user.upload(input, new File(['Date,Impressions\n2026-11-20,1200'], 'export.csv', { type: 'text/csv' }));
    expect(await screen.findByText('Imported: 1 of 2 rows matched a post.')).toBeInTheDocument();
    expect(sent).toEqual({ type: 'text/csv', filename: 'export.csv', body: 'Date,Impressions\n2026-11-20,1200' });
  });

  it('says when the export import is switched off', async () => {
    serveInsights(http.post(apiUrl('/api/analytics/imports'), () => problem(503, 'import_unavailable')));
    const { user } = await open();
    const block = region('LinkedIn’s export');
    const input = block.querySelector<HTMLInputElement>('input[type="file"]');
    if (!input) throw new Error('no file input');
    await user.upload(input, new File(['a,b'], 'export.csv', { type: 'text/csv' }));
    expect(await within(block).findByText('Export import is switched off on the server.')).toBeInTheDocument();
  });
});
