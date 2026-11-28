import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { Toaster } from '@/components/ui/sonner';
import { DataProvider } from '@/features/data/data-provider';
import type { components } from '@/lib/api/schema';
import { createTestAppData } from '@/test/app-data';
import { apiUrl, server } from '@/test/msw';
import { ThemeProvider } from '@/theme/theme-provider';

import { page, versioned } from '../piece/testing/fixtures';
import { problemResponse } from '../piece/testing/handlers';

import { QuickSearchPanel } from './quick-search-panel';
import { CITATION_A, CITATION_B, quickSearchDoc } from './testing/fixtures';

type QuickSearch = components['schemas']['QuickSearch'];

function renderPanel(searches: QuickSearch[] = []) {
  server.use(http.get(apiUrl('/api/research/quick'), () => HttpResponse.json(page(searches))));
  const user = userEvent.setup();
  render(
    <ThemeProvider>
      <DataProvider appData={createTestAppData()}>
        <QuickSearchPanel scope={{ articleId: 'art-1' }} pillar="identity" />
        <Toaster />
      </DataProvider>
    </ThemeProvider>,
  );
  return { user };
}

function setOnline(online: boolean) {
  vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(online);
  act(() => {
    window.dispatchEvent(new Event(online ? 'online' : 'offline'));
  });
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe('Quick search', () => {
  it('asks one question for the piece and shows the answer with numbered sources', async () => {
    const searches: QuickSearch[] = [];
    let sent: unknown;
    server.use(
      http.post(apiUrl('/api/research/quick'), async ({ request }) => {
        sent = await request.json();
        const created = quickSearchDoc();
        searches.unshift(created);
        return HttpResponse.json(versioned(created), { status: 201 });
      }),
    );
    const { user } = renderPanel(searches);
    expect(await screen.findByText('No searches yet.')).toBeInTheDocument();

    await user.type(screen.getByRole('textbox', { name: 'Your question' }), 'Does Entra support passkey sync?');
    await user.click(screen.getByRole('button', { name: 'Search' }));

    const question = await screen.findByRole('button', { name: 'Does Entra support passkey sync?' });
    expect(question).toHaveAttribute('aria-expanded', 'true');
    expect(sent).toEqual({ query: 'Does Entra support passkey sync?', articleId: 'art-1', pillar: 'identity' });
    expect(screen.getByRole('textbox', { name: 'Your question' })).toHaveValue('');

    // The answer's markers are footnote links to the numbered sources.
    expect(screen.getByRole('link', { name: `Source 1: ${CITATION_A.title} (opens in a new tab)` })).toHaveAttribute('href', CITATION_A.url);
    expect(screen.getByRole('link', { name: `Source 2: ${CITATION_B.title} (opens in a new tab)` })).toHaveTextContent('2');
    expect(screen.getByText('Sync arrived in 2025.')).toBeInTheDocument();
    const sources = screen.getAllByRole('list').find((list) => list.tagName === 'OL');
    if (!sources) throw new Error('no numbered sources');
    expect(within(sources).getAllByRole('listitem')).toHaveLength(2);
  });

  it('copies a claim with its source', async () => {
    const writeText = vi.fn(() => Promise.resolve());
    const { user } = renderPanel([quickSearchDoc()]);
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    await user.click(await screen.findByRole('button', { name: 'Copy “Sync arrived in 2025.” with its source' }));
    expect(writeText).toHaveBeenCalledWith(`Sync arrived in 2025. (${CITATION_A.title}, ${CITATION_A.url})`);
    expect(await screen.findByText('Copied with its source.')).toBeInTheDocument();
  });

  it('keeps earlier searches folded, newest open', async () => {
    renderPanel([quickSearchDoc({ id: 'qs-2', query: 'Newest question' }), quickSearchDoc({ id: 'qs-1', query: 'Older question' })]);
    expect(await screen.findByRole('button', { name: 'Newest question' })).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByRole('button', { name: 'Older question' })).toHaveAttribute('aria-expanded', 'false');
  });

  it('checks the question before sending it', async () => {
    const post = vi.fn();
    server.use(
      http.post(apiUrl('/api/research/quick'), () => {
        post();
        return HttpResponse.json(versioned(quickSearchDoc()), { status: 201 });
      }),
    );
    const { user } = renderPanel();
    await user.type(screen.getByRole('textbox', { name: 'Your question' }), 'ab');
    await user.click(screen.getByRole('button', { name: 'Search' }));
    expect(screen.getByRole('textbox', { name: 'Your question' })).toHaveAccessibleDescription(
      'Ask a question of at least a few words.',
    );
    expect(post).not.toHaveBeenCalled();
  });

  it('puts a blocklist hit on the question', async () => {
    server.use(
      http.post(apiUrl('/api/research/quick'), () =>
        problemResponse(422, 'confidential_text', {
          findings: [{ field: 'query', code: 'blocklist', severity: 'block', message: 'Blocked.', ref: 'term-2' }],
        }),
      ),
    );
    const { user } = renderPanel();
    await user.type(screen.getByRole('textbox', { name: 'Your question' }), 'What does Contoso run?');
    await user.click(screen.getByRole('button', { name: 'Search' }));
    await waitFor(() => {
      expect(screen.getByRole('textbox', { name: 'Your question' })).toHaveAccessibleDescription(
        'This matches a term on your blocklist. Rephrase it.',
      );
    });
  });

  it('explains a search that took too long', async () => {
    server.use(
      http.post(apiUrl('/api/research/quick'), () =>
        problemResponse(504, 'timeout', { detail: 'The quick search took too long. Try again.' }),
      ),
    );
    const { user } = renderPanel();
    await user.type(screen.getByRole('textbox', { name: 'Your question' }), 'What changed in NIST 800-63-4?');
    await user.click(screen.getByRole('button', { name: 'Search' }));
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('The search didn’t finish.');
    expect(alert).toHaveTextContent('The quick search took too long. Try again.');
    expect(alert).toHaveTextContent('req-studio-1');
  });

  it('waits for a connection', async () => {
    const { user } = renderPanel();
    await screen.findByText('No searches yet.');
    setOnline(false);
    expect(screen.getByRole('button', { name: 'Search' })).toBeDisabled();
    expect(screen.getByText('You’re offline. Search needs a connection.')).toBeInTheDocument();
    setOnline(true);
    await user.type(screen.getByRole('textbox', { name: 'Your question' }), 'x');
    expect(screen.getByRole('button', { name: 'Search' })).toBeEnabled();
  });
});
