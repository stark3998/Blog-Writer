import { screen, waitFor, within } from '@testing-library/react';
import type { RouteObject } from 'react-router';
import { describe, expect, it } from 'vitest';

import * as calendarRoute from '@/routes/calendar';
import * as libraryRoute from '@/routes/library';
import * as settingsRoute from '@/routes/settings';
import * as studioPieceRoute from '@/routes/studio-piece';
import { areaOf, firstInstallHandlers } from '@/test/first-install';
import { server } from '@/test/msw';
import { findPageHeading, renderApp } from '@/test/render-app';

import { routes } from './routes';

function pageRoutes(): RouteObject[] {
  const pages = routes[0]?.children?.find((route) => route.id === 'pages');
  if (!pages?.children) throw new Error('the pathless "pages" route is missing');
  return pages.children;
}

describe('route table', () => {
  it('lazy-loads every area so each page is its own chunk', () => {
    const areas = pageRoutes().filter((route) => route.path !== undefined && route.path !== '*');
    expect(areas.map((route) => route.path)).toEqual([
      'today',
      'calendar/:view?',
      'studio',
      'studio/:pieceId/:tab?',
      'library/:section?',
      'settings/:section?',
    ]);
    for (const route of areas) {
      expect(typeof route.lazy, route.path).toBe('function');
      expect(route.Component, route.path).toBeUndefined();
      expect(route.element, route.path).toBeUndefined();
    }
  });

  it('loads the open section’s code in the route loader, so a page or tab commits ready to show', () => {
    for (const route of [calendarRoute, libraryRoute, settingsRoute, studioPieceRoute]) {
      expect(route.loader).toBeTypeOf('function');
      expect(route.Component).toBeTypeOf('function');
    }
  });

  it('keeps the shell eager and gives pages a loading fallback and an error boundary', () => {
    const root = routes[0];
    expect(root?.Component).toBeTypeOf('function');
    expect(root?.ErrorBoundary).toBeTypeOf('function');
    const pages = root?.children?.find((route) => route.id === 'pages');
    expect(pages?.HydrateFallback).toBeTypeOf('function');
    expect(pages?.ErrorBoundary).toBeTypeOf('function');
  });
});

/** A first install's answers for the area `path` opens (no plan, no pieces, nothing connected). */
function firstInstall(path: string): void {
  const area = areaOf(path);
  if (!area) throw new Error(`no area for ${path}`);
  server.use(...firstInstallHandlers(area));
}

describe('lazy routes', () => {
  it('redirects / to Today, the PWA start page', async () => {
    firstInstall('/today');
    const { router } = renderApp('/');
    expect(await findPageHeading('Today')).toBeInTheDocument();
    expect(router.state.location.pathname).toBe('/today');
  });

  it('shows the shell and a loading state while the first page loads', async () => {
    firstInstall('/library');
    renderApp('/library');
    // Before the chunk resolves: the navigation is already there, the page is a skeleton.
    expect(screen.getByRole('navigation', { name: 'Main' })).toBeInTheDocument();
    expect(screen.getByText('Loading page')).toBeInTheDocument();
    expect(await findPageHeading('Library')).toBeInTheDocument();
    expect(screen.queryByText('Loading page')).not.toBeInTheDocument();
  });

  it.each([
    ['/today', 'Today', 'Nothing needs you right now.'],
    ['/calendar', 'Calendar', 'No plan for next week yet.'],
    ['/calendar/ideas', 'Calendar', 'No ideas yet.'],
    ['/calendar/availability', 'Calendar', 'Set how much you can give each week.'],
    ['/studio', 'Studio', 'No pieces in progress.'],
    ['/studio/piece-1', 'Untitled piece', 'No brief yet.'],
    ['/studio/piece-1/interview', 'Untitled piece', 'No questions yet.'],
    ['/studio/piece-1/draft', 'Untitled piece', 'No draft yet.'],
    ['/studio/piece-1/posts', 'Untitled piece', 'No posts yet.'],
    ['/studio/piece-1/go-live', 'Untitled piece', 'Nothing scheduled.'],
    ['/studio/art-1/draft', 'Untitled piece', 'No draft yet.'],
    ['/studio/post-1', 'Untitled post', 'No brief yet.'],
    ['/studio/post-1/go-live?post=post-1', 'Untitled post', 'Nothing scheduled.'],
    // Library sections always say what they are for and the owner's part, saved or not.
    ['/library', 'Library', 'Your pillars'],
    ['/library/voice', 'Library', 'Voice guide'],
    ['/library/stories', 'Library', 'Stories and positions'],
    ['/library/brand', 'Library', 'Brand kit'],
    ['/library/formats', 'Library', 'Article formats'],
    ['/settings', 'Settings', 'Model routing is not connected yet.'],
    ['/settings/sources', 'Settings', 'No sources yet.'],
    ['/settings/insights', 'Settings', 'No insights yet.'],
  ])('%s loads its page with an honest empty state', async (path, title, emptyTitle) => {
    firstInstall(path);
    renderApp(path);
    expect(await findPageHeading(title)).toBeInTheDocument();
    // The section's data loads after the page commits.
    const empty = await screen.findByRole('heading', { level: 2, name: emptyTitle });
    // Every empty state says what the owner does there.
    const section = empty.closest('section');
    if (!section) throw new Error(`"${emptyTitle}" is not inside a section`);
    expect(within(section).getByText('Your part')).toBeInTheDocument();
  });

  it.each([
    // Settings that are forms from the first day: the defaults, and plainly what is missing (in
    // the section, or for the cadence in the save bar under the whole form).
    ['/settings/slots', 'Post slots', 'The defaults apply until you save.', 'page'],
    ['/settings/integrations', 'LinkedIn', 'Not connected', 'section'],
    ['/settings/policy', 'Blocklist', 'No terms on the list', 'section'],
    ['/settings/system', 'Jobs', 'No jobs have run yet.', 'section'],
  ] as const)('%s opens on a first install and says what is missing', async (path, sectionTitle, missing, where) => {
    firstInstall(path);
    renderApp(path);
    expect(await findPageHeading('Settings')).toBeInTheDocument();
    const heading = await screen.findByRole('heading', { level: 2, name: sectionTitle });
    const scope = where === 'page' ? screen.getByRole('main') : heading.closest('section');
    if (!scope) throw new Error(`"${sectionTitle}" is not inside a section`);
    expect(await within(scope).findByText(missing)).toBeInTheDocument();
  });

  it('selects the tab named in the URL', async () => {
    firstInstall('/settings/insights');
    renderApp('/settings/insights');
    await findPageHeading('Settings');
    expect(screen.getByRole('tab', { name: 'Insights' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tab', { name: 'Models' })).toHaveAttribute('aria-selected', 'false');
  });

  it('sets the document title per page and tab', async () => {
    firstInstall('/settings/insights');
    renderApp('/settings/insights');
    await findPageHeading('Settings');
    await waitFor(() => {
      expect(document.title).toBe('Insights – Settings – Blog Writer');
    });
  });

  it('gives a standalone post every stage but Draft, which is for articles', async () => {
    firstInstall('/studio/post-1/posts');
    renderApp('/studio/post-1/posts');
    await findPageHeading('Untitled post');
    const tabs = within(screen.getByRole('tablist', { name: 'Piece stages' })).getAllByRole('tab');
    expect(tabs.map((tab) => tab.textContent)).toEqual(['Brief & research', 'Interview', 'Posts & carousel', 'Go-live']);
    expect(screen.getByRole('tab', { name: 'Posts & carousel' })).toHaveAttribute('aria-selected', 'true');
  });

  it('offers the Engage panel on Today', async () => {
    firstInstall('/today');
    const { user } = renderApp('/today', { viewport: 'mobile' });
    await findPageHeading('Today');
    await user.click(screen.getByRole('button', { name: 'Open the reply helper' }));
    const sheet = await screen.findByRole('dialog', { name: 'Reply helper' });
    expect(sheet).toHaveClass('bottom-0');
  });

  it.each([
    '/settings/unknown',
    '/library/nope',
    '/calendar/later',
    '/studio/piece-1/nope',
    '/studio/post-1/draft',
    '/no-such-page',
  ])(
    '%s shows "Page not found" inside the shell',
    async (path) => {
      const area = areaOf(path);
      if (area) server.use(...firstInstallHandlers(area));
      renderApp(path);
      expect(await findPageHeading('Page not found')).toBeInTheDocument();
      expect(screen.getByRole('navigation', { name: 'Main' })).toBeInTheDocument();
      expect(screen.getByRole('link', { name: 'Go to Today' })).toHaveAttribute('href', '/today');
    },
  );

  it('explains a failed chunk download and keeps the navigation', async () => {
    const [root] = routes;
    const pages = root?.children?.find((route) => route.id === 'pages');
    if (!root || !pages) throw new Error('missing root or pages route');
    const broken: RouteObject[] = [
      {
        id: 'root',
        path: '/',
        Component: root.Component,
        ErrorBoundary: root.ErrorBoundary,
        children: [
          {
            id: 'pages',
            ErrorBoundary: pages.ErrorBoundary,
            children: [
              {
                path: 'broken',
                lazy: () =>
                  Promise.reject(new TypeError('Failed to fetch dynamically imported module: /assets/broken.js')),
              },
            ],
          },
        ],
      },
    ];
    renderApp('/broken', { routes: broken });
    expect(await findPageHeading('This page didn’t load')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Reload' })).toBeInTheDocument();
    expect(screen.getByRole('navigation', { name: 'Main' })).toBeInTheDocument();
  });
});
