import { screen, waitFor, within } from '@testing-library/react';
import type { RouteObject } from 'react-router';
import { describe, expect, it } from 'vitest';

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

  it('keeps the shell eager and gives pages a loading fallback and an error boundary', () => {
    const root = routes[0];
    expect(root?.Component).toBeTypeOf('function');
    expect(root?.ErrorBoundary).toBeTypeOf('function');
    const pages = root?.children?.find((route) => route.id === 'pages');
    expect(pages?.HydrateFallback).toBeTypeOf('function');
    expect(pages?.ErrorBoundary).toBeTypeOf('function');
  });
});

describe('lazy routes', () => {
  it('redirects / to Today, the PWA start page', async () => {
    const { router } = renderApp('/');
    expect(await findPageHeading('Today')).toBeInTheDocument();
    expect(router.state.location.pathname).toBe('/today');
  });

  it('shows the shell and a loading state while the first page loads', async () => {
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
    ['/library', 'Library', 'Three pillars.'],
    ['/library/voice', 'Library', 'No voice guide yet.'],
    ['/library/stories', 'Library', 'No stories or positions yet.'],
    ['/library/brand', 'Library', 'No brand kit yet.'],
    ['/library/formats', 'Library', 'Four article formats.'],
    ['/settings', 'Settings', 'Model routing is not connected yet.'],
    ['/settings/slots', 'Settings', 'Post slots.'],
    ['/settings/sources', 'Settings', 'No sources yet.'],
    ['/settings/integrations', 'Settings', 'Nothing connected yet.'],
    ['/settings/policy', 'Settings', 'No blocklist yet.'],
    ['/settings/insights', 'Settings', 'No insights yet.'],
    ['/settings/system', 'Settings', 'No jobs have run yet.'],
  ])('%s loads its page with an honest empty state', async (path, title, emptyTitle) => {
    renderApp(path);
    expect(await findPageHeading(title)).toBeInTheDocument();
    const empty = screen.getByRole('heading', { level: 2, name: emptyTitle });
    // Every empty state says what the owner does there.
    const section = empty.closest('section');
    if (!section) throw new Error(`"${emptyTitle}" is not inside a section`);
    expect(within(section).getByText('Your part')).toBeInTheDocument();
  });

  it('selects the tab named in the URL', async () => {
    renderApp('/settings/insights');
    await findPageHeading('Settings');
    expect(screen.getByRole('tab', { name: 'Insights' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tab', { name: 'Models' })).toHaveAttribute('aria-selected', 'false');
  });

  it('sets the document title per page and tab', async () => {
    renderApp('/settings/insights');
    await findPageHeading('Settings');
    await waitFor(() => {
      expect(document.title).toBe('Insights – Settings – Blog Writer');
    });
  });

  it.each(['/settings/unknown', '/library/nope', '/calendar/later', '/studio/piece-1/nope', '/no-such-page'])(
    '%s shows "Page not found" inside the shell',
    async (path) => {
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
