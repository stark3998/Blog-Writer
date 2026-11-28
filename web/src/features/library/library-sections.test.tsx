import { screen, within } from '@testing-library/react';
import { http } from 'msw';
import { describe, expect, it } from 'vitest';

import { apiUrl, server } from '@/test/msw';
import { findPageHeading, renderApp } from '@/test/render-app';

import { problemWith } from './testing/handlers';

/** Every Library route answers 503, as while the database is unreachable. */
function serverDown() {
  server.use(
    http.get(apiUrl('/api/*'), ({ request }) => {
      const path = new URL(request.url).pathname;
      if (path === '/api/me' || path === '/api/health') return undefined;
      return problemWith(503, 'store_unavailable', { detail: 'The database is busy. Try again in a moment.' });
    }),
  );
}

describe('Library sections', () => {
  it.each([
    ['/library', 'Your pillars', 'Couldn’t load the pillars.'],
    ['/library/voice', 'Voice guide', 'Couldn’t load the voice guide.'],
    ['/library/stories', 'Stories and positions', 'Couldn’t load the stories.'],
    ['/library/brand', 'Brand kit', 'Couldn’t load the brand kit.'],
    ['/library/formats', 'Article formats', 'Couldn’t load the formats.'],
  ])('%s says what it is for and the owner’s part, and explains a failed load', async (path, title, failure) => {
    serverDown();
    renderApp(path);
    await findPageHeading('Library');
    const heading = screen.getByRole('heading', { level: 2, name: title });
    const section = heading.closest('section');
    if (!section) throw new Error(`"${title}" is not inside a section`);
    expect(screen.getByRole('region', { name: title })).toBe(section);
    expect(within(section).getByText('Your part')).toBeInTheDocument();
    expect(await within(section).findByText(failure, {}, { timeout: 3000 })).toBeInTheDocument();
    expect(within(section).getAllByRole('button', { name: /^Try (loading|again)/ }).length).toBeGreaterThan(0);
  });
});
