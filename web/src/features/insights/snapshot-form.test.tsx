import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { DataProvider } from '@/features/data/data-provider';
import { createTestAppData, seedSession } from '@/test/app-data';
import { apiUrl, problem } from '@/test/msw';

import { NOW_MS } from '../settings/testing/fixtures';
import { SnapshotForm } from './index';
import { post } from './testing/fixtures';
import { serveInsights } from './testing/handlers';

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(NOW_MS);
});

afterEach(() => {
  vi.useRealTimers();
});

function renderForm(props: Parameters<typeof SnapshotForm>[0]) {
  const appData = createTestAppData();
  seedSession(appData);
  const user = userEvent.setup();
  render(
    <DataProvider appData={appData}>
      <SnapshotForm {...props} />
    </DataProvider>,
  );
  return { user };
}

describe('SnapshotForm, as Today opens it from a reminder', () => {
  it('opens on the reminder’s post, even an older one, and reports back when saved', async () => {
    const onDone = vi.fn();
    serveInsights(
      http.get(apiUrl('/api/posts/:id'), ({ params }) =>
        HttpResponse.json(post({ id: String(params.id), postedAt: '2026-11-20T15:00:00.000000Z' })),
      ),
      http.post(apiUrl('/api/posts/:id/snapshots'), ({ params }) => HttpResponse.json(post({ id: String(params.id) }))),
    );
    const { user } = renderForm({ postId: 'post-9', onDone });
    const form = await screen.findByRole('form', { name: 'Snapshot numbers' });
    // Seven days after it went live: the +7 day reading.
    expect(within(form).getByRole('radio', { name: '+7 days' })).toBeChecked();
    expect(screen.queryByRole('combobox', { name: 'Post' })).not.toBeInTheDocument();
    expect(within(form).getByRole('link', { name: 'Open it on LinkedIn' })).toHaveAttribute('rel', 'noopener noreferrer');

    await user.type(within(form).getByRole('textbox', { name: /Impressions/ }), '5000');
    await user.click(within(form).getByRole('button', { name: 'Save the numbers' }));
    await waitFor(() => {
      expect(onDone).toHaveBeenCalledTimes(1);
    });
  });

  it('explains a refusal from the server in words', async () => {
    serveInsights(
      http.post(apiUrl('/api/posts/:id/snapshots'), () =>
        HttpResponse.json(
          {
            type: 'about:blank',
            title: 'Unprocessable',
            status: 422,
            code: 'snapshot_before_post',
            detail: 'The time is before the post went live.',
          },
          { status: 422, headers: { 'Content-Type': 'application/problem+json' } },
        ),
      ),
    );
    const { user } = renderForm({ postId: 'post-1' });
    const form = await screen.findByRole('form', { name: 'Snapshot numbers' });
    await user.type(within(form).getByRole('textbox', { name: /Impressions/ }), '10');
    await user.click(within(form).getByRole('button', { name: 'Save the numbers' }));
    expect(await within(form).findByText('The time is before the post went live.')).toBeInTheDocument();
  });

  it('says when the post is not live yet', async () => {
    serveInsights(http.get(apiUrl('/api/posts/:id'), () => HttpResponse.json(post({ status: 'approved', postedAt: null }))));
    renderForm({ postId: 'post-1' });
    expect(await screen.findByText(/isn’t live on LinkedIn/)).toBeInTheDocument();
  });

  it('shows the problem and a retry when the post cannot be read', async () => {
    serveInsights(http.get(apiUrl('/api/posts/:id'), () => problem(404, 'not_found', 'req-post')));
    renderForm({ postId: 'post-gone' });
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Couldn’t load the post.');
    expect(within(alert).getByRole('button', { name: 'Try loading the post again' })).toBeInTheDocument();
  });
});
