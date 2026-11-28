import { onlineManager } from '@tanstack/react-query';
import { screen, waitFor, within } from '@testing-library/react';
import { delay, http, HttpResponse } from 'msw';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { apiUrl, problem } from '@/test/msw';
import { findPageHeading, renderApp } from '@/test/render-app';

import { modelsView, systemStatus } from './testing/fixtures';
import { serveSettings } from './testing/handlers';

async function open() {
  const view = renderApp('/settings/models');
  await findPageHeading('Settings');
  return view;
}

afterEach(() => {
  onlineManager.setOnline(true);
});

describe('Settings > Models', () => {
  it('shows today’s spend against the brake, the fallback rate, the routing and the prices', async () => {
    serveSettings();
    await open();

    const meter = await screen.findByRole('progressbar', { name: 'Today’s AI spend against the brake' });
    expect(meter).toHaveAttribute('aria-valuetext', '$4.12 of $20.00');
    expect(await screen.findByText('Fallback rate, 7 days')).toBeInTheDocument();
    expect(screen.getByText('4.2%')).toBeInTheDocument();

    const routing = await screen.findByRole('table', { name: /first model, then its fallbacks/ });
    const draft = within(routing).getByRole('row', { name: /Drafting articles and posts/ });
    expect(draft).toHaveTextContent('claude-opus-5-5');
    expect(draft).toHaveTextContent('Falls back to gpt-6.2-sol');
    expect(within(routing).getByRole('row', { name: /Web research/ })).toHaveTextContent('Public only');

    const prices = screen.getByRole('table', { name: /US dollars per million tokens/ });
    expect(within(prices).getByRole('row', { name: /claude-opus-5-5/ })).toHaveTextContent('$75.00');
    expect(prices).toHaveAccessibleName(/Web search costs \$14\.00 per thousand searches/);
  });

  it('says so when the brake is on', async () => {
    serveSettings(
      http.get(apiUrl('/api/system/status'), () =>
        HttpResponse.json(systemStatus({ brake: { spendTodayUsd: 21, thresholdUsd: 20, engaged: true } })),
      ),
    );
    await open();
    expect(await screen.findByText('The brake is on.')).toBeInTheDocument();
  });

  it('shows an honest empty state without model routing', async () => {
    serveSettings(http.get(apiUrl('/api/kb/models'), () => HttpResponse.json(modelsView({ routes: [] }))));
    await open();
    const heading = await screen.findByRole('heading', { level: 2, name: 'Model routing is not connected yet.' });
    expect(heading.closest('section')).toHaveTextContent('Your part');
  });

  it('saves the preferences with the version it read', async () => {
    let sent: { ifMatch: string | null; body: unknown } | undefined;
    serveSettings(
      http.put(apiUrl('/api/kb/models'), async ({ request }) => {
        sent = { ifMatch: request.headers.get('If-Match'), body: await request.json() };
        const preferences = modelsView().preferences;
        return HttpResponse.json({ item: { ...preferences.item, noveltyMaxSimilarity: 0.85 }, etag: '"prefs-2"' });
      }),
    );
    const { user } = await open();

    const novelty = await screen.findByRole('spinbutton', { name: /Novelty limit for articles/ });
    await user.clear(novelty);
    await user.type(novelty, '0.85');
    expect(screen.getByText('Unsaved changes')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Save preferences' }));

    await waitFor(() => {
      expect(sent).toEqual({
        ifMatch: '"prefs-1"',
        body: { imagesEnabled: false, noveltyMaxSimilarity: 0.85, nearDuplicateMaxSimilarity: 0.5 },
      });
    });
    expect(await screen.findByText('Saved')).toBeInTheDocument();
  });

  it('refuses a limit outside 0 to 1 before sending anything', async () => {
    let calls = 0;
    serveSettings(
      http.put(apiUrl('/api/kb/models'), () => {
        calls += 1;
        return HttpResponse.json(modelsView().preferences);
      }),
    );
    const { user } = await open();
    const nearDuplicate = await screen.findByRole('spinbutton', { name: /Near-duplicate limit for posts/ });
    await user.clear(nearDuplicate);
    await user.type(nearDuplicate, '1.5');
    expect(nearDuplicate).toHaveAttribute('aria-invalid', 'true');
    expect(screen.getByText('Use a number above 0 and at most 1.')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Save preferences' }));
    expect(calls).toBe(0);
  });

  it('opens the conflict dialog on a stale version and keeps the owner’s value on “Keep mine”', async () => {
    const sent: (string | null)[] = [];
    serveSettings(
      http.put(apiUrl('/api/kb/models'), ({ request }) => {
        sent.push(request.headers.get('If-Match'));
        if (sent.length === 1) {
          return HttpResponse.json(
            {
              type: 'about:blank',
              title: 'Precondition Failed',
              status: 412,
              code: 'etag_mismatch',
              current: { etag: '"prefs-9"', updatedAt: '2026-11-27T14:00:00.000000Z', updatedBy: 'owner' },
            },
            { status: 412, headers: { 'Content-Type': 'application/problem+json' } },
          );
        }
        return HttpResponse.json({ ...modelsView().preferences, etag: '"prefs-10"' });
      }),
    );
    const { user } = await open();
    const novelty = await screen.findByRole('spinbutton', { name: /Novelty limit for articles/ });
    await user.clear(novelty);
    await user.type(novelty, '0.8');
    await user.click(screen.getByRole('button', { name: 'Save preferences' }));

    const dialog = await screen.findByRole('alertdialog', { name: 'The model preferences changed since you opened it' });
    await user.click(within(dialog).getByRole('button', { name: 'Keep mine' }));
    await waitFor(() => {
      expect(sent).toEqual(['"prefs-1"', '"prefs-9"']);
    });
    await waitFor(() => {
      expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
    });
  });

  it('shows the problem and a retry when the routing cannot be read', async () => {
    serveSettings(http.get(apiUrl('/api/kb/models'), () => problem(404, 'not_found', 'req-models')));
    await open();
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Couldn’t load the model routing.');
    expect(alert).toHaveTextContent('req-models');
    expect(within(alert).getByRole('button', { name: 'Try loading the model routing again' })).toBeInTheDocument();
  });

  it('says it is offline instead of loading forever', async () => {
    vi.spyOn(window.navigator, 'onLine', 'get').mockReturnValue(false);
    serveSettings(
      http.get(apiUrl('/api/kb/models'), async () => {
        await delay('infinite');
        return HttpResponse.json(modelsView());
      }),
    );
    await open();
    expect(await screen.findAllByText('You’re offline. This loads when the connection is back.')).not.toHaveLength(0);
  });

  it('holds a save made offline and says it waits for the connection', async () => {
    let sent = 0;
    serveSettings(
      http.put(apiUrl('/api/kb/models'), () => {
        sent += 1;
        return HttpResponse.json(modelsView().preferences);
      }),
    );
    const { user } = await open();
    const novelty = await screen.findByRole('spinbutton', { name: /Novelty limit for articles/ });
    await user.clear(novelty);
    await user.type(novelty, '0.9');
    onlineManager.setOnline(false);
    await user.click(screen.getByRole('button', { name: 'Save preferences' }));
    expect(await screen.findByRole('button', { name: 'Waiting for a connection…' })).toBeDisabled();
    expect(screen.getByText('Offline: it saves when the connection is back')).toBeInTheDocument();
    expect(sent).toBe(0);
    onlineManager.setOnline(true);
    await waitFor(() => {
      expect(sent).toBe(1);
    });
  });
});
