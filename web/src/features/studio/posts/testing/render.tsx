import { render } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement } from 'react';
import { createMemoryRouter } from 'react-router';
import { RouterProvider } from 'react-router/dom';

import { DataProvider } from '@/features/data/data-provider';
import { createTestAppData, seedSession } from '@/test/app-data';
import { setMedia } from '@/test/media';

/**
 * Renders one Studio tab at `path` inside a data router (search params, links and the leave
 * guard work) with the real data layer, without the shell or the piece header: the tab's own
 * requests are the only ones, so each test answers exactly those.
 */
export function renderTab(ui: ReactElement, path: string, { viewport = 'desktop' }: { viewport?: 'desktop' | 'mobile' } = {}) {
  setMedia({ desktop: viewport === 'desktop' });
  const appData = createTestAppData();
  seedSession(appData);
  const router = createMemoryRouter(
    [
      { path: '/studio/:pieceId/:tab', element: ui },
      { path: '*', element: <p>Somewhere else</p> },
    ],
    { initialEntries: [path] },
  );
  const user = userEvent.setup();
  const view = render(
    <DataProvider appData={appData}>
      <RouterProvider router={router} useTransitions={false} />
    </DataProvider>,
  );
  return { ...view, user, router, appData };
}
