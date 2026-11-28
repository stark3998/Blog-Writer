import { screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';

import { apiUrl, server } from '@/test/msw';
import { findPageHeading, renderApp } from '@/test/render-app';

import { articleSummary, page, postSummary } from '../piece/testing/fixtures';
import { problemResponse, settingsHandlers } from '../piece/testing/handlers';

interface Seen {
  articles: URLSearchParams[];
  posts: URLSearchParams[];
}

function lists(
  articles: ReturnType<typeof articleSummary>[],
  posts: ReturnType<typeof postSummary>[],
  seen: Seen = { articles: [], posts: [] },
) {
  server.use(
    ...settingsHandlers(),
    http.get(apiUrl('/api/articles'), ({ request }) => {
      const query = new URL(request.url).searchParams;
      seen.articles.push(query);
      const pillar = query.get('pillar');
      return HttpResponse.json(page(articles.filter((item) => !pillar || item.meta.pillar === pillar)));
    }),
    http.get(apiUrl('/api/posts'), ({ request }) => {
      const query = new URL(request.url).searchParams;
      seen.posts.push(query);
      const pillar = query.get('pillar');
      return HttpResponse.json(page(posts.filter((item) => !pillar || item.pillar === pillar)));
    }),
  );
  return seen;
}

describe('the Studio list', () => {
  it('shows an honest empty state with New piece', async () => {
    lists([], []);
    renderApp('/studio');
    expect(await findPageHeading('Studio')).toBeInTheDocument();
    const empty = await screen.findByRole('heading', { level: 2, name: 'No pieces in progress.' });
    const section = empty.closest('section');
    if (!section) throw new Error('no section');
    expect(within(section).getByText('Your part')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'New piece' })).toBeInTheDocument();
  });

  it('groups pieces by what they need, you first, each row a link to its next step', async () => {
    const seen = lists(
      [
        articleSummary({ id: 'art-review', status: 'in_review', meta: { title: 'Agent identity, done right', slug: null, pillar: 'agents', format: 'framework' } }),
        articleSummary({ id: 'art-planned', status: 'planned', meta: { title: 'Risk tiers for agents', slug: null, pillar: 'governance', format: 'deep-explainer' } }),
      ],
      [postSummary({ id: 'post-draft', status: 'draft' }), postSummary({ id: 'post-writing', status: 'generating', hook: '' })],
    );
    renderApp('/studio');
    const needsYou = await screen.findByRole('region', { name: /^Needs you/ });
    const links = within(needsYou).getAllByRole('link');
    expect(links.map((link) => link.getAttribute('href'))).toEqual(
      expect.arrayContaining(['/studio/art-review/draft', '/studio/post-draft/posts']),
    );
    const review = within(needsYou).getByRole('link', { name: 'Agent identity, done right' });
    await waitFor(() => {
      expect(review).toHaveAccessibleDescription(/Framework \/ checklist article.*AI agents & security.*In review/);
    });
    expect(screen.getByRole('region', { name: /^Working on it/ })).toHaveTextContent('Untitled post');
    expect(screen.getByRole('region', { name: /^Planned/ })).toHaveTextContent('Risk tiers for agents');
    const headings = screen.getAllByRole('heading', { level: 2 }).map((heading) => heading.textContent);
    expect(headings).toEqual(['Needs you, 2 pieces', 'Working on it, 1 piece', 'Planned, 1 piece']);

    // The server filters: in-progress statuses, flagship articles, standalone lanes.
    expect(seen.articles[0]?.getAll('status')).toContain('in_review');
    expect(seen.articles[0]?.getAll('status')).not.toContain('archived');
    expect(seen.articles[0]?.get('purpose')).toBe('flagship');
    expect(seen.posts[0]?.getAll('lane')).toEqual(['native', 'quick_take', 'repurpose']);
    expect(seen.articles[0]?.get('limit')).toBe('20');
  });

  it('filters by pillar on the server and keeps the filter in the URL', async () => {
    const seen = lists(
      [articleSummary({ id: 'art-a', meta: { title: 'Agents piece', slug: null, pillar: 'agents', format: null } })],
      [postSummary({ id: 'post-i', pillar: 'identity', hook: 'Identity post' })],
    );
    const { user, router } = renderApp('/studio');
    await screen.findByText('Agents piece');
    const pillars = await screen.findByRole('radiogroup', { name: 'Pillar' });
    await user.click(within(pillars).getByRole('radio', { name: 'Identity modernization' }));
    await waitFor(() => {
      expect(router.state.location.search).toBe('?pillar=identity');
    });
    await waitFor(() => {
      expect(screen.queryByText('Agents piece')).not.toBeInTheDocument();
    });
    expect(screen.getByText('Identity post')).toBeInTheDocument();
    expect(seen.articles.at(-1)?.get('pillar')).toBe('identity');
  });

  it('searches the titles and says when nothing matches', async () => {
    lists(
      [articleSummary({ id: 'art-a', meta: { title: 'Passkeys in Entra', slug: null, pillar: 'identity', format: null } })],
      [postSummary({ id: 'post-b', hook: 'Prompt injection in MCP servers' })],
    );
    const { user } = renderApp('/studio');
    await screen.findByText('Passkeys in Entra');
    await user.type(screen.getByRole('searchbox', { name: 'Search pieces by title' }), 'entra');
    await waitFor(() => {
      expect(screen.queryByText('Prompt injection in MCP servers')).not.toBeInTheDocument();
    });
    expect(screen.getByText('Passkeys in Entra')).toBeInTheDocument();

    await user.type(screen.getByRole('searchbox', { name: 'Search pieces by title' }), ' okta');
    expect(await screen.findByText('No piece matches “entra okta”.')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Clear the search' }));
    expect(await screen.findByText('Prompt injection in MCP servers')).toBeInTheDocument();
  });

  it('shows the archived pieces when asked', async () => {
    const seen = lists([], []);
    const { user, router } = renderApp('/studio');
    await screen.findByRole('heading', { level: 2, name: 'No pieces in progress.' });
    const archived = screen.getByRole('radio', { name: 'Archived' });
    await user.click(archived);
    await waitFor(() => {
      expect(router.state.location.search).toBe('?show=archived');
    });
    await waitFor(() => {
      expect(seen.articles.at(-1)?.getAll('status')).toEqual(['archived', 'unpublished']);
    });
    expect(await screen.findByText('No pieces match these filters.')).toBeInTheDocument();
  });

  it('keeps the articles when the posts fail, with a retry', async () => {
    let postCalls = 0;
    server.use(
      ...settingsHandlers(),
      http.get(apiUrl('/api/articles'), () => HttpResponse.json(page([articleSummary()]))),
      http.get(apiUrl('/api/posts'), () => {
        postCalls += 1;
        return postCalls === 1 ? problemResponse(422, 'invalid_cursor') : HttpResponse.json(page([postSummary()]));
      }),
    );
    const { user } = renderApp('/studio');
    expect(await screen.findByText('The MCP threat model nobody ships')).toBeInTheDocument();
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Couldn’t load the posts.');
    expect(alert).toHaveTextContent('req-studio-1');
    await user.click(within(alert).getByRole('button', { name: 'Try loading the posts again' }));
    expect(await screen.findByText('Okta to Entra in 90 days: what broke first')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('loads more pieces with the next cursor', async () => {
    const cursors: (string | null)[] = [];
    server.use(
      ...settingsHandlers(),
      http.get(apiUrl('/api/articles'), ({ request }) => {
        const cursor = new URL(request.url).searchParams.get('cursor');
        cursors.push(cursor);
        return cursor
          ? HttpResponse.json(page([articleSummary({ id: 'art-2', meta: { title: 'Second page piece', slug: null, pillar: 'agents', format: null } })]))
          : HttpResponse.json(page([articleSummary()], 'cursor-2'));
      }),
      http.get(apiUrl('/api/posts'), () => HttpResponse.json(page([]))),
    );
    const { user } = renderApp('/studio');
    await screen.findByText('The MCP threat model nobody ships');
    await user.click(screen.getByRole('button', { name: 'Show more pieces' }));
    expect(await screen.findByText('Second page piece')).toBeInTheDocument();
    expect(cursors).toEqual([null, 'cursor-2']);
    expect(screen.queryByRole('button', { name: 'Show more pieces' })).not.toBeInTheDocument();
  });
});
