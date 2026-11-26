import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createMemoryRouter, type RouteObject } from 'react-router';
import { RouterProvider } from 'react-router/dom';

import { routes as appRoutes } from '@/app/routes';
import { DataProvider } from '@/features/data/data-provider';
import { ThemeProvider } from '@/theme/theme-provider';

import { createTestAppData, seedSession, type TestAppData } from './app-data';
import { setMedia } from './media';

export interface RenderAppOptions {
  viewport?: 'desktop' | 'mobile';
  routes?: RouteObject[];
  /** The data layer; a fresh test instance by default. */
  appData?: TestAppData;
  /**
   * Start with the signed-in owner and server build cached (default), so pages render without
   * waiting for the network. Turn off to exercise loading and error states.
   */
  seed?: boolean;
}

/** Renders the real app (routes, shell, theme, data layer) at `path` in a memory router. */
export function renderApp(
  path = '/today',
  { viewport = 'desktop', routes = appRoutes, appData = createTestAppData(), seed = true }: RenderAppOptions = {},
) {
  setMedia({ desktop: viewport === 'desktop' });
  if (seed) seedSession(appData);
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  const user = userEvent.setup();
  // Navigations render without React transitions here. A transition render is time-sliced, so
  // under a busy test run it can commit after the router state has already changed, and a test
  // that checks the DOM right after `router.state` changes reads stale markup. The app itself
  // keeps the default (transitions); only the timing of renders differs, not what they show.
  const view = render(
    <ThemeProvider>
      <DataProvider appData={appData}>
        <RouterProvider router={router} useTransitions={false} />
      </DataProvider>
    </ThemeProvider>,
  );
  return { ...view, router, user, appData };
}

/** Waits for the lazy page to load and returns its h1. */
export async function findPageHeading(name: string | RegExp) {
  return screen.findByRole('heading', { level: 1, name });
}
