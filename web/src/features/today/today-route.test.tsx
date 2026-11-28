import { render, screen } from '@testing-library/react';
import { createMemoryRouter } from 'react-router';
import { RouterProvider } from 'react-router/dom';
import { describe, expect, it } from 'vitest';

import { routes } from '@/app/routes';
import { getBrowserAppData } from '@/lib/app-data';
import { server } from '@/test/msw';
import { findPageHeading } from '@/test/render-app';
import { ThemeProvider } from '@/theme/theme-provider';

import { todayHandlers } from './testing/handlers';

describe('the Today route', () => {
  it('brings its own data layer when the app does not provide one around the router', async () => {
    server.use(...todayHandlers());
    // As main.tsx renders it today: no DataProvider above the router.
    render(
      <ThemeProvider>
        <RouterProvider router={createMemoryRouter(routes, { initialEntries: ['/today'] })} />
      </ThemeProvider>,
    );
    expect(await findPageHeading('Today')).toBeInTheDocument();
    expect(await screen.findByText('Test Owner')).toBeInTheDocument();
    expect(await screen.findByText('0123456')).toBeInTheDocument();
    // It used the browser's one instance, which now holds the answers.
    expect(getBrowserAppData().queryClient.getQueryData(['me'])).toMatchObject({ name: 'Test Owner' });
  });
});
