import { redirect, type RouteObject } from 'react-router';

import { AppShell } from '@/components/shell/app-shell';
import { NotFound } from '@/components/shell/not-found';
import { PageError, RootError } from '@/components/shell/route-error';
import { RouteFallback } from '@/components/shell/route-fallback';

/**
 * Route table (React Router 7, data mode). Every area is its own lazy chunk.
 *
 * The pathless "pages" route holds the error boundary and the loading
 * fallback, so the shell stays on screen while a page loads or fails.
 */
export const routes: RouteObject[] = [
  {
    id: 'root',
    path: '/',
    Component: AppShell,
    ErrorBoundary: RootError,
    children: [
      {
        id: 'pages',
        ErrorBoundary: PageError,
        HydrateFallback: RouteFallback,
        children: [
          { id: 'index', index: true, loader: () => redirect('/today') },
          { id: 'today', path: 'today', lazy: () => import('@/routes/today') },
          { id: 'calendar', path: 'calendar/:view?', lazy: () => import('@/routes/calendar') },
          { id: 'studio', path: 'studio', lazy: () => import('@/routes/studio-list') },
          { id: 'studio-piece', path: 'studio/:pieceId/:tab?', lazy: () => import('@/routes/studio-piece') },
          { id: 'library', path: 'library/:section?', lazy: () => import('@/routes/library') },
          { id: 'settings', path: 'settings/:section?', lazy: () => import('@/routes/settings') },
          { id: 'not-found', path: '*', Component: NotFound },
        ],
      },
    ],
  },
];
