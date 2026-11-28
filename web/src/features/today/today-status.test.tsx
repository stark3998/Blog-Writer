import { act, screen, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { findPageHeading, renderApp } from '@/test/render-app';
import { apiUrl, problem, server, TEST_HEALTH, TEST_ME } from '@/test/msw';

import { todayHandlers } from './testing/handlers';
import { shortSha } from './today-status';

/** The definition list under Today's title. */
function statusList() {
  const term = screen.getByText('Signed in as');
  const list = term.closest('dl');
  if (!list) throw new Error('the session facts are not in a <dl>');
  return within(list);
}

describe('Today: session facts', () => {
  beforeEach(() => {
    // The rest of the page (inbox, notices, week) answers too, so only the facts are under test.
    server.use(...todayHandlers());
  });

  it('shows skeletons, then the signed-in name and the server build', async () => {
    renderApp('/today', { seed: false });
    await findPageHeading('Today');
    expect(statusList().getAllByText('Loading')).toHaveLength(2);

    expect(await statusList().findByText('Test Owner')).toBeInTheDocument();
    const sha = await statusList().findByText('0123456');
    expect(sha).toHaveAttribute('title', TEST_HEALTH.sha);
    expect(statusList().getByText('Server build')).toBeInTheDocument();
    expect(statusList().queryByText('Loading')).not.toBeInTheDocument();
  });

  it('says "the owner" when only the app cookie signed the request in (no name)', async () => {
    server.use(http.get(apiUrl('/api/me'), () => HttpResponse.json({ ...TEST_ME, name: null })));
    renderApp('/today', { seed: false });
    expect(await screen.findByText('the owner')).toBeInTheDocument();
  });

  it('shows an error with a retry when a query fails, and recovers', async () => {
    let healthy = false;
    server.use(http.get(apiUrl('/api/health'), () => (healthy ? HttpResponse.json(TEST_HEALTH) : problem(503))));
    const { user } = renderApp('/today', { seed: false });
    expect(await screen.findByText('Couldn’t load')).toBeInTheDocument();
    // The name still loads; only the failed fact shows the error.
    expect(screen.getByText('Test Owner')).toBeInTheDocument();

    healthy = true;
    await user.click(screen.getByRole('button', { name: 'Retry loading the server build' }));
    expect(await screen.findByText('0123456')).toBeInTheDocument();
    expect(screen.queryByText('Couldn’t load')).not.toBeInTheDocument();
  });

  it('refetches when the owner comes back to the app and the data is stale', async () => {
    const me = vi.fn(() => HttpResponse.json(TEST_ME));
    server.use(http.get(apiUrl('/api/me'), me));
    const { appData } = renderApp('/today', { seed: false });
    await screen.findByText('Test Owner');
    expect(me).toHaveBeenCalledTimes(1);

    // Coming back within the stale time does not refetch. TanStack handles focus after a
    // microtask, so let those run before checking that no request started (or finished).
    await act(async () => {
      window.dispatchEvent(new Event('visibilitychange'));
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(appData.queryClient.isFetching()).toBe(0);
    expect(me).toHaveBeenCalledTimes(1);

    // Six minutes later it does.
    const later = Date.now() + 6 * 60_000;
    vi.spyOn(Date, 'now').mockReturnValue(later);
    act(() => {
      window.dispatchEvent(new Event('visibilitychange'));
    });
    await vi.waitFor(() => {
      expect(me).toHaveBeenCalledTimes(2);
    });
  });
});

describe('shortSha', () => {
  it('shortens a full commit SHA to seven characters', () => {
    expect(shortSha(TEST_HEALTH.sha)).toBe('0123456');
  });

  it('keeps labels such as "dev" and "local" as they are', () => {
    expect(shortSha('dev')).toBe('dev');
    expect(shortSha('local')).toBe('local');
    expect(shortSha('v2.0.0-rc.1')).toBe('v2.0.0-rc.1');
  });
});
