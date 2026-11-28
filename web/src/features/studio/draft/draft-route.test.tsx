import { screen, within } from '@testing-library/react';
import { beforeAll, describe, expect, it } from 'vitest';

import { server } from '@/test/msw';
import { findPageHeading, renderApp } from '@/test/render-app';

import { ARTICLE_ID, articleFixture } from './testing/fixtures';
import { createDraftServer } from './testing/handlers';
import { installEditorLayout } from './testing/environment';

beforeAll(() => {
  installEditorLayout();
});

describe('/studio/:id/draft in the app', () => {
  it('loads in the piece’s Draft tab and parses through the worker, explaining when it can’t start', async () => {
    const draft = createDraftServer(articleFixture());
    server.use(...draft.handlers);
    renderApp(`/studio/${ARTICLE_ID}/draft`);
    await findPageHeading(/./);
    expect(screen.getByRole('tab', { name: 'Draft' })).toHaveAttribute('aria-selected', 'true');
    const panel = screen.getByRole('tabpanel', { name: 'Draft' });
    // jsdom has no Web Worker, and the editor never parses on the main thread instead.
    const alert = await within(panel).findByRole('alert');
    expect(within(alert).getByText('The editor couldn’t start in this browser.')).toBeInTheDocument();
    expect(within(alert).getByRole('button', { name: 'Preview as portfolio page' })).toBeInTheDocument();
    // The rest of the tab still works: status, actions and the gates.
    expect(within(panel).getByText('In review')).toBeInTheDocument();
    expect(within(panel).getByRole('button', { name: 'Approve…' })).toBeInTheDocument();
    expect(await within(panel).findByRole('heading', { level: 2, name: 'Gates' })).toBeInTheDocument();
    // One main landmark (the shell's) and no second h1.
    expect(screen.getAllByRole('main')).toHaveLength(1);
    expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1);
  });
});

describe('/studio/:id/draft for a planned article', () => {
  it('shows the honest empty state without needing the worker', async () => {
    const draft = createDraftServer(articleFixture({ status: 'planned', markdown: '', version: 0, briefId: null, pipelineJobId: null }));
    server.use(...draft.handlers);
    renderApp(`/studio/${ARTICLE_ID}/draft`);
    const empty = await screen.findByRole('heading', { level: 2, name: 'No draft yet.' });
    const section = empty.closest('section');
    if (!section) throw new Error('no section');
    expect(within(section).getByText('Your part')).toBeInTheDocument();
  });
});
