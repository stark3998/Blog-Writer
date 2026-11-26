import { screen } from '@testing-library/react';
import { http } from 'msw';
import { describe, expect, it } from 'vitest';

import { createRedirectGuard } from '@/lib/auth/redirect-guard';
import { createTestAppData } from '@/test/app-data';
import { apiUrl, problem, server } from '@/test/msw';
import { renderApp } from '@/test/render-app';

describe('AuthGate', () => {
  it('shows "not authorised" for a 403, with a way to sign out and the request id, and never redirects', async () => {
    server.use(http.get(apiUrl('/api/me'), () => problem(403, 'not_owner', 'req-42')));
    const { appData } = renderApp('/today', { seed: false });
    expect(await screen.findByRole('heading', { level: 1, name: 'Not authorised' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Sign out' })).toHaveAttribute(
      'href',
      '/.auth/logout?post_logout_redirect_uri=%2F',
    );
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
    expect(screen.getByText('req-42')).toBeInTheDocument();
    expect(appData.navigate).not.toHaveBeenCalled();
    // The page itself is gone: nothing else to act on with the wrong account.
    expect(screen.queryByRole('heading', { name: 'Today' })).not.toBeInTheDocument();
  });

  it('starts the sign-in redirect on a 401 and says so while the page changes', async () => {
    server.use(http.get(apiUrl('/api/me'), () => problem(401, 'unauthenticated')));
    const appData = createTestAppData({ currentPath: () => '/today' });
    renderApp('/today', { seed: false, appData });
    const message = await screen.findByText('Your session has ended. Taking you to sign in…');
    expect(message).toHaveAttribute('role', 'status');
    expect(appData.navigate).toHaveBeenCalledExactlyOnceWith('/.auth/login/aad?post_login_redirect_uri=%2Ftoday');
  });

  it('stops at a "sign in" screen instead of looping, and signs in only when asked', async () => {
    server.use(http.get(apiUrl('/api/me'), () => problem(401)));
    // Three earlier round trips through the sign-in page in this tab.
    const earlier = createRedirectGuard({ storage: window.sessionStorage });
    for (let i = 0; i < 3; i += 1) earlier.attempt();

    const { appData, user } = renderApp('/today', { seed: false });
    expect(await screen.findByRole('heading', { level: 1, name: 'Sign-in didn’t work' })).toBeInTheDocument();
    expect(screen.getByText(/kept sending you back here/)).toBeInTheDocument();
    expect(appData.navigate).not.toHaveBeenCalled();

    await user.click(screen.getByRole('button', { name: 'Sign in' }));
    expect(appData.navigate).toHaveBeenCalledTimes(1);
    expect(appData.navigate.mock.calls[0]?.[0]).toMatch(/^\/\.auth\/login\/aad\?post_login_redirect_uri=/);
  });

  it('explains when the browser blocks the storage that guards against loops', async () => {
    server.use(http.get(apiUrl('/api/me'), () => problem(401)));
    const appData = createTestAppData({ storage: null });
    renderApp('/today', { seed: false, appData });
    expect(await screen.findByText(/blocks the storage that keeps automatic sign-in safe/)).toBeInTheDocument();
    expect(appData.navigate).not.toHaveBeenCalled();
  });
});
