import { screen, waitFor, within } from '@testing-library/react';
import type { UserEvent } from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { Outlet, useLocation, type RouteObject } from 'react-router';
import { describe, expect, it } from 'vitest';

import { Toaster } from '@/components/ui/sonner';
import { apiUrl, server } from '@/test/msw';
import { renderApp } from '@/test/render-app';

import { articleDoc, ideaDoc, page, postDoc, povDoc, versioned } from '../piece/testing/fixtures';
import { problemResponse, settingsHandlers } from '../piece/testing/handlers';

import { StudioListPage } from './studio-list-page';

/** Where a create lands, without loading the piece's tabs (other areas' code). */
function Landing() {
  const location = useLocation();
  const state = location.state as { jobId?: string } | null;
  return (
    <p>
      {`Landed on ${location.pathname}`}
      {state?.jobId ? ` with job ${state.jobId}` : ''}
    </p>
  );
}

const routes: RouteObject[] = [
  {
    path: '/',
    element: (
      <>
        <Outlet />
        <Toaster />
      </>
    ),
    children: [
      { path: 'studio', element: <StudioListPage /> },
      { path: 'studio/:pieceId/:tab', element: <Landing /> },
    ],
  },
];

function setup(ideas = [ideaDoc()], povs = [povDoc()]) {
  server.use(
    ...settingsHandlers(),
    http.get(apiUrl('/api/articles'), () => HttpResponse.json(page([]))),
    http.get(apiUrl('/api/posts'), () => HttpResponse.json(page([]))),
    http.get(apiUrl('/api/ideas'), () => HttpResponse.json(page(ideas))),
    http.get(apiUrl('/api/povs'), () => HttpResponse.json(page(povs))),
  );
}

async function openDialog(user: UserEvent): Promise<HTMLElement> {
  await user.click(await screen.findByRole('button', { name: 'New piece' }));
  return screen.findByRole('dialog', { name: 'New piece' });
}

/** Picks an option of a Radix select from the keyboard (jsdom has no layout for pointer picks). */
async function choose(user: UserEvent, dialog: HTMLElement, label: string, option: string) {
  const trigger = within(dialog).getByRole('combobox', { name: label });
  await waitFor(() => {
    expect(trigger).toBeEnabled();
  });
  trigger.focus();
  await user.keyboard('{Enter}');
  const listbox = await screen.findByRole('listbox');
  const names = within(listbox)
    .getAllByRole('option')
    .map((item) => item.textContent);
  const index = names.indexOf(option);
  if (index < 0) throw new Error(`no option ${option} in ${names.join(', ')}`);
  await user.keyboard(`${'{ArrowDown}'.repeat(index)}{Enter}`);
  await waitFor(() => {
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  });
}

describe('New piece', () => {
  it('turns an idea into an article with its title, pillar and format, then opens it', async () => {
    setup();
    let sent: unknown;
    server.use(
      http.post(apiUrl('/api/articles'), async ({ request }) => {
        sent = await request.json();
        return HttpResponse.json(versioned(articleDoc({ id: 'art-new' })), { status: 201 });
      }),
    );
    const { user, router } = renderApp('/studio', { routes });
    const dialog = await openDialog(user);

    await user.click(await within(dialog).findByRole('radio', { name: 'Entra adds passkey sync for workforce accounts' }));
    expect(within(dialog).getByRole('textbox', { name: /Working title/ })).toHaveValue('Entra adds passkey sync for workforce accounts');
    await waitFor(() => {
      expect(within(dialog).getByRole('combobox', { name: /Pillar/ })).toHaveTextContent('Identity modernization');
    });
    expect(within(dialog).getByRole('combobox', { name: /Format/ })).toHaveTextContent('News analysis');
    await user.click(within(dialog).getByRole('button', { name: 'Create article' }));

    await waitFor(() => {
      expect(router.state.location.pathname).toBe('/studio/art-new/brief');
    });
    expect(sent).toEqual({
      meta: { title: 'Entra adds passkey sync for workforce accounts', pillar: 'identity', format: 'news-analysis' },
      ideaId: 'idea-1',
    });
    expect(await screen.findByText('Article created.')).toBeInTheDocument();
  });

  it('starts a quick take from an idea with one of your cleared takes', async () => {
    setup([ideaDoc({ suggestedLane: 'quick_take', suggestedFormat: 'text' })], [
      povDoc(),
      povDoc({ id: 'pov-2', statement: 'Not yet cleared.', classifier: { passed: null } }),
    ]);
    let sent: unknown;
    server.use(
      http.post(apiUrl('/api/posts'), async ({ request }) => {
        sent = await request.json();
        return HttpResponse.json({ post: versioned(postDoc({ id: 'post-new', status: 'generating' })), jobId: 'job-gen-1' }, { status: 202 });
      }),
    );
    const { user, router } = renderApp('/studio', { routes });
    const dialog = await openDialog(user);
    await user.click(await within(dialog).findByRole('radio', { name: 'Entra adds passkey sync for workforce accounts' }));
    // The idea suggests a quick take.
    expect(within(dialog).getByRole('radio', { name: 'A quick take' })).toBeChecked();
    const take = await within(dialog).findByRole('radio', { name: /Passkey sync is a convenience feature/ });
    expect(within(dialog).queryByRole('radio', { name: 'Not yet cleared.' })).not.toBeInTheDocument();
    await user.click(take);
    await user.click(within(dialog).getByRole('button', { name: 'Start the quick take' }));

    await waitFor(() => {
      expect(router.state.location.pathname).toBe('/studio/post-new/posts');
    });
    expect(screen.getByText('Landed on /studio/post-new/posts with job job-gen-1')).toBeInTheDocument();
    expect(sent).toEqual({ lane: 'quick_take', pillar: 'identity', format: 'text', ideaId: 'idea-1', povIds: ['pov-1'] });
    expect(await screen.findByText('Quick take started. It’s being written now.')).toBeInTheDocument();
  });

  it('explains a quick take needs your take first', async () => {
    setup([ideaDoc({ suggestedLane: 'quick_take' })], [povDoc({ classifier: { passed: null } })]);
    const { user } = renderApp('/studio', { routes });
    const dialog = await openDialog(user);
    await user.click(await within(dialog).findByRole('radio', { name: 'Entra adds passkey sync for workforce accounts' }));
    expect(await within(dialog).findByText('No cleared take on this pillar yet.')).toBeInTheDocument();
    expect(within(dialog).getByText(/1 of your takes is still waiting for the confidentiality check/)).toBeInTheDocument();
    expect(within(dialog).getByRole('link', { name: 'Open Ideas' })).toHaveAttribute('href', '/calendar/ideas');
  });

  it('starts a native post from a blank page', async () => {
    setup();
    let sent: unknown;
    server.use(
      http.post(apiUrl('/api/posts'), async ({ request }) => {
        sent = await request.json();
        return HttpResponse.json({ post: versioned(postDoc({ id: 'post-native', lane: 'native' })), jobId: 'job-gen-2' }, { status: 202 });
      }),
    );
    const { user, router } = renderApp('/studio', { routes });
    const dialog = await openDialog(user);
    await user.click(within(dialog).getByRole('radio', { name: 'A blank page' }));
    await user.click(within(dialog).getByRole('radio', { name: 'A LinkedIn post' }));
    await choose(user, dialog, 'Pillar', 'AI governance');
    await user.click(within(dialog).getByRole('radio', { name: 'Carousel' }));
    await user.click(within(dialog).getByRole('button', { name: 'Start the post' }));

    await waitFor(() => {
      expect(router.state.location.pathname).toBe('/studio/post-native/posts');
    });
    expect(sent).toEqual({ lane: 'native', pillar: 'governance', format: 'carousel' });
  });

  it('checks the form before sending and focuses the first problem', async () => {
    setup();
    const { user } = renderApp('/studio', { routes });
    const dialog = await openDialog(user);
    await user.click(within(dialog).getByRole('radio', { name: 'A blank page' }));
    await user.click(within(dialog).getByRole('button', { name: 'Create article' }));

    const title = within(dialog).getByRole('textbox', { name: /Working title/ });
    expect(title).toHaveAttribute('aria-invalid', 'true');
    expect(title).toHaveAccessibleDescription(/Give it a working title\./);
    expect(title).toHaveFocus();
    expect(within(dialog).getByText('Choose a pillar.')).toBeInTheDocument();
    expect(within(dialog).getByText('Choose a format.')).toBeInTheDocument();
  });

  it('shows a refused create in place, with where to fix it', async () => {
    setup();
    server.use(
      http.post(apiUrl('/api/posts'), () =>
        problemResponse(409, 'ai_use_not_confirmed', { detail: 'Confirm your employer’s AI-use policy first.' }),
      ),
    );
    const { user } = renderApp('/studio', { routes });
    const dialog = await openDialog(user);
    await user.click(within(dialog).getByRole('radio', { name: 'A blank page' }));
    await user.click(within(dialog).getByRole('radio', { name: 'A LinkedIn post' }));
    await choose(user, dialog, 'Pillar', 'AI agents & security');
    await user.click(within(dialog).getByRole('button', { name: 'Start the post' }));

    const alert = await within(dialog).findByRole('alert');
    expect(alert).toHaveTextContent('The post wasn’t started.');
    expect(alert).toHaveTextContent('Confirm your employer’s AI-use policy first.');
    expect(within(dialog).getByRole('link', { name: 'Open Blocklist & policy' })).toHaveAttribute('href', '/settings/policy');
    // The dialog stays open with what was entered.
    expect(within(dialog).getByRole('combobox', { name: /Pillar/ })).toHaveTextContent('AI agents & security');
  });

  it('says when another piece took the idea first, and reloads the inbox', async () => {
    let ideaReads = 0;
    setup();
    server.use(
      http.get(apiUrl('/api/ideas'), () => {
        ideaReads += 1;
        return HttpResponse.json(page(ideaReads === 1 ? [ideaDoc()] : []));
      }),
      http.post(apiUrl('/api/articles'), () =>
        problemResponse(409, 'invalid_transition', { machine: 'idea', from: 'used', to: 'used', allowed: ['accepted'] }),
      ),
    );
    const { user } = renderApp('/studio', { routes });
    const dialog = await openDialog(user);
    await user.click(await within(dialog).findByRole('radio', { name: 'Entra adds passkey sync for workforce accounts' }));
    await waitFor(() => {
      expect(within(dialog).getByRole('combobox', { name: /Format/ })).toHaveTextContent('News analysis');
    });
    await user.click(within(dialog).getByRole('button', { name: 'Create article' }));
    const alert = await within(dialog).findByRole('alert');
    expect(alert).toHaveTextContent('This idea already has a piece');
    await waitFor(() => {
      expect(ideaReads).toBeGreaterThan(1);
    });
  });

  it('offers a blank page when the ideas inbox is empty', async () => {
    setup([]);
    const { user } = renderApp('/studio', { routes });
    const dialog = await openDialog(user);
    expect(await within(dialog).findByText('Your ideas inbox is empty.')).toBeInTheDocument();
    await user.click(within(dialog).getByRole('button', { name: 'Start from a blank page' }));
    expect(within(dialog).getByRole('radio', { name: 'A blank page' })).toBeChecked();
    expect(within(dialog).getByRole('textbox', { name: /Working title/ })).toBeInTheDocument();
  });
});
