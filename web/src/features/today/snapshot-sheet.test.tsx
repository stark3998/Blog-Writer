import { act, screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { toast } from 'sonner';
import { afterEach, describe, expect, it } from 'vitest';

import { problemResponse } from '@/features/engagement/testing/handlers';
import { apiUrl, server } from '@/test/msw';
import { renderApp } from '@/test/render-app';

import { offsetFor } from './snapshot-sheet';
import { postEntry } from './testing/fixtures';
import { todayHandlers } from './testing/handlers';

// Toasts live in a module-level store that replays them to the next test's toaster.
afterEach(() => {
  act(() => {
    toast.dismiss();
  });
});

interface Sent {
  ifMatch: string | null;
  body: Record<string, unknown>;
}

function setup({ answer }: { answer?: (sent: Sent, attempt: number) => Response } = {}) {
  const sent: Sent[] = [];
  server.use(
    ...todayHandlers(),
    http.get(apiUrl('/api/posts/:id'), () => HttpResponse.json(postEntry())),
    http.post(apiUrl('/api/posts/:id/snapshots'), async ({ request }) => {
      const call = { ifMatch: request.headers.get('If-Match'), body: (await request.json()) as Record<string, unknown> };
      sent.push(call);
      return answer?.(call, sent.length) ?? HttpResponse.json(postEntry({}, '"post-8-v2"'));
    }),
  );
  return sent;
}

async function openForm() {
  return screen.findByRole('dialog', { name: 'Log the numbers' });
}

describe('the snapshot form', () => {
  it('opens from the reminder link, cleans the URL and saves with the post’s If-Match', async () => {
    const sent = setup();
    const { user, router } = renderApp('/today?snapshot=post-8', { viewport: 'mobile' });
    const sheet = await openForm();
    await waitFor(() => {
      expect(router.state.location.search).toBe('');
    });
    expect(sheet).toHaveClass('bottom-0');
    expect(await within(sheet).findByText('“Agents need identities, not shared keys.”')).toBeInTheDocument();
    // Posted 47 hours ago: these are the 48-hour numbers.
    expect(within(sheet).getByRole('radio', { name: '48 hours' })).toBeChecked();

    await user.type(within(sheet).getByRole('textbox', { name: 'Impressions' }), '1200');
    await user.type(within(sheet).getByRole('textbox', { name: 'Reactions' }), '34');
    await user.click(within(sheet).getByRole('button', { name: 'Save the numbers' }));

    expect(await screen.findByText('Numbers saved.')).toBeInTheDocument();
    expect(sent).toEqual([
      {
        ifMatch: '"post-8-v1"',
        body: { offset: 'h48', impressions: 1200, reactions: 34, comments: null, reposts: null, saves: null, sends: null },
      },
    ]);
    await waitFor(() => {
      expect(screen.queryByRole('dialog', { name: 'Log the numbers' })).not.toBeInTheDocument();
    });
  });

  it('opens from the inbox card without leaving Today', async () => {
    setup();
    const { user, router } = renderApp('/today');
    await user.click(await screen.findByRole('button', { name: 'Log the numbers: Log the 48-hour numbers for your post' }));
    expect(await openForm()).toBeInTheDocument();
    expect(router.state.location.pathname).toBe('/today');
  });

  it('asks for at least one whole number before anything is sent', async () => {
    const sent = setup();
    const { user } = renderApp('/today?snapshot=post-8');
    const sheet = await openForm();
    await user.click(await within(sheet).findByRole('button', { name: 'Save the numbers' }));
    expect(within(sheet).getByText('Enter at least one number.')).toBeInTheDocument();

    const impressions = within(sheet).getByRole('textbox', { name: 'Impressions' });
    await user.type(impressions, '1,200');
    await user.click(within(sheet).getByRole('button', { name: 'Save the numbers' }));
    expect(within(sheet).getByText('Use a whole number, without commas.')).toBeInTheDocument();
    expect(impressions).toHaveAttribute('aria-invalid', 'true');
    expect(impressions).toHaveFocus();
    expect(sent).toHaveLength(0);
  });

  it('shows who changed the post on a 412 and saves over it with Keep mine', async () => {
    const sent = setup({
      answer: (_call, attempt) =>
        attempt === 1
          ? problemResponse(412, 'etag_mismatch', { current: { etag: '"post-8-v9"', updatedBy: 'system' } })
          : HttpResponse.json(postEntry({}, '"post-8-v10"')),
    });
    const { user } = renderApp('/today?snapshot=post-8');
    const sheet = await openForm();
    await user.type(await within(sheet).findByRole('textbox', { name: 'Comments' }), '5');
    await user.click(within(sheet).getByRole('button', { name: 'Save the numbers' }));

    const conflict = await screen.findByRole('alertdialog', { name: 'This post changed since you opened it' });
    expect(within(conflict).getByText(/The app saved a newer version/)).toBeInTheDocument();
    await user.click(within(conflict).getByRole('button', { name: 'Keep mine' }));

    expect(await screen.findByText('Numbers saved.')).toBeInTheDocument();
    expect(sent.map((call) => call.ifMatch)).toEqual(['"post-8-v1"', '"post-8-v9"']);
  });

  it('explains a refusal in the form', async () => {
    setup({ answer: () => problemResponse(409, 'not_posted', { detail: 'Add numbers once the post is live on LinkedIn.' }) });
    const { user } = renderApp('/today?snapshot=post-8');
    const sheet = await openForm();
    await user.type(await within(sheet).findByRole('textbox', { name: 'Saves' }), '2');
    await user.click(within(sheet).getByRole('button', { name: 'Save the numbers' }));
    expect(await within(sheet).findByText('Add numbers once the post is live on LinkedIn.')).toBeInTheDocument();
  });

  it('says so when the post is not live yet', async () => {
    setup();
    server.use(http.get(apiUrl('/api/posts/:id'), () => HttpResponse.json(postEntry({ status: 'approved', postedAt: null }))));
    renderApp('/today?snapshot=post-8');
    const sheet = await openForm();
    expect(await within(sheet).findByText('This post isn’t live')).toBeInTheDocument();
    expect(within(sheet).queryByRole('button', { name: 'Save the numbers' })).not.toBeInTheDocument();
  });
});

describe('offsetFor', () => {
  it('matches the server: 36-72 h is the 48-hour snapshot, 6-8 days the 7-day one', () => {
    const now = Date.parse('2026-11-27T12:00:00Z');
    expect(offsetFor('2026-11-25T12:00:00Z', now)).toBe('h48');
    expect(offsetFor('2026-11-20T12:00:00Z', now)).toBe('d7');
    expect(offsetFor('2026-11-27T00:00:00Z', now)).toBe('other');
    expect(offsetFor(null, now)).toBe('other');
  });
});
