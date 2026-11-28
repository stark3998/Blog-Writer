import { screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { beforeAll, describe, expect, it } from 'vitest';

import { DialectError } from '@/features/editor';
import { apiUrl, server } from '@/test/msw';

import { DraftTab } from './draft-tab';
import { ARTICLE_ID, articleFixture, finding, gateResult, gatesViewFixture } from './testing/fixtures';
import { createDraftServer, problemResponse } from './testing/handlers';
import { findArticleEditor, installEditorLayout, renderDraft, testDialect, testEnvironment } from './testing/environment';

beforeAll(() => {
  installEditorLayout();
});

function setup(overrides: Parameters<typeof articleFixture>[0] = {}) {
  const draft = createDraftServer(articleFixture(overrides));
  server.use(...draft.handlers);
  return draft;
}

describe('DraftTab: loading', () => {
  it('opens an article in review in an editable editor, with its status, outline and length', async () => {
    setup();
    const { user } = renderDraft(<DraftTab pieceId={ARTICLE_ID} />);
    const editor = await findArticleEditor();
    await waitFor(() => {
      expect(editor.isEditable).toBe(true);
    });
    expect(editor.getText()).toContain('Why agents need identity');
    expect(screen.getByRole('textbox', { name: 'Article text' })).toHaveAttribute('aria-readonly', 'false');
    expect(screen.getByText('In review')).toBeInTheDocument();
    expect(screen.getByRole('toolbar', { name: 'Formatting' })).toBeInTheDocument();
    expect(screen.getByText(/words$/)).toBeInTheDocument();

    const notes = screen.getByRole('complementary', { name: 'Review notes' });
    await user.click(within(notes).getByRole('tab', { name: 'Outline' }));
    const outline = await screen.findByRole('navigation', { name: 'Outline' });
    expect(within(outline).getByRole('button', { name: 'Why agents need identity' })).toBeInTheDocument();
    expect(within(outline).getByRole('button', { name: 'What to do first' })).toBeInTheDocument();
  });

  it('explains a missing article', async () => {
    server.use(http.get(apiUrl(`/api/articles/${ARTICLE_ID}`), () => problemResponse(404, 'not_found')));
    renderDraft(<DraftTab pieceId={ARTICLE_ID} />);
    expect(await screen.findByText('There’s no article here.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Try loading/ })).not.toBeInTheDocument();
  });

  it('offers a retry when the article could not be loaded', async () => {
    server.use(http.get(apiUrl(`/api/articles/${ARTICLE_ID}`), () => problemResponse(500, 'internal_error')));
    renderDraft(<DraftTab pieceId={ARTICLE_ID} />);
    expect(await screen.findByText('Couldn’t load the draft.', {}, { timeout: 3000 })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Try loading the draft again' })).toBeInTheDocument();
  });

  it('refuses an article over 128 KB and offers the preview and the export', async () => {
    setup({ markdown: `${'word '.repeat(27_000)}\n` });
    renderDraft(<DraftTab pieceId={ARTICLE_ID} />);
    expect(await screen.findByText('This article is too large for the editor.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Preview as portfolio page' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Export Markdown' })).toBeInTheDocument();
    expect(screen.queryByRole('toolbar', { name: 'Formatting' })).not.toBeInTheDocument();
  });

  it('reports a parse that timed out in the worker, and tries again', async () => {
    setup();
    let fail = true;
    const working = testDialect();
    const env = testEnvironment({
      dialect: {
        parse: (markdown, options) =>
          fail ? Promise.reject(new DialectError('timeout', 'Reading the article took longer than 8000 ms, so it was stopped.')) : working.parse(markdown, options),
      },
    });
    const { user } = renderDraft(<DraftTab pieceId={ARTICLE_ID} />, { env });
    expect(await screen.findByText('The editor took too long to open this article.')).toBeInTheDocument();
    fail = false;
    await user.click(screen.getByRole('button', { name: 'Try again' }));
    const editor = await findArticleEditor();
    await waitFor(() => {
      expect(editor.getText()).toContain('Why agents need identity');
    });
  });

  it('shows a planned article with no draft as an honest empty state', async () => {
    setup({ status: 'planned', markdown: '', version: 0, briefId: null, pipelineJobId: null });
    renderDraft(<DraftTab pieceId={ARTICLE_ID} />);
    const heading = await screen.findByRole('heading', { level: 2, name: 'No draft yet.' });
    const section = heading.closest('section');
    if (!section) throw new Error('no section');
    expect(within(section).getByText('Your part')).toBeInTheDocument();
    expect(await within(section).findByRole('button', { name: 'Start the pipeline' })).toBeInTheDocument();
    expect(await within(section).findByRole('button', { name: 'Write it yourself' })).toBeInTheDocument();
  });
});

describe('DraftTab: who writes', () => {
  it('keeps the text read-only while approved and says why', async () => {
    setup({ status: 'approved', publish: { live: false, auto: false, publishAt: '2026-11-30T14:00:00.000000Z' } });
    renderDraft(<DraftTab pieceId={ARTICLE_ID} />);
    const editor = await findArticleEditor();
    await waitFor(() => {
      expect(editor.getText()).toContain('Why agents need identity');
    });
    expect(editor.isEditable).toBe(false);
    expect(screen.getByRole('textbox', { name: 'Article text' })).toHaveAttribute('aria-readonly', 'true');
    expect(screen.getByText(/Read-only while approved/)).toBeInTheDocument();
    expect(screen.queryByRole('toolbar', { name: 'Formatting' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Pull back to review' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Publish now' })).toBeInTheDocument();
  });
});

describe('DraftTab: gates', () => {
  it('lists the seven gates and offers an override only where the server allows one', async () => {
    const draft = setup();
    draft.gates = gatesViewFixture(
      [
        gateResult('originality', {
          verdict: 'fail',
          findings: [finding({ code: 'slop-over-threshold', severity: 'block', message: 'Too many slop patterns.', overridable: true, start: 0, end: 10 })],
        }),
        gateResult('lint', {
          verdict: 'fail',
          findings: [finding({ code: 'image-alt', severity: 'block', message: 'An image has no alt text.', overridable: false })],
          truncated: 3,
        }),
      ],
      { manualPublishReady: false, autoPublishReady: false, blocking: ['originality', 'lint'] },
    );
    const { user } = renderDraft(<DraftTab pieceId={ARTICLE_ID} />);
    expect(await screen.findByText('2 gates block approval.')).toBeInTheDocument();
    for (const name of ['Quality', 'Facts', 'Confidentiality', 'Originality', 'Formatting', 'Your signal', 'Outbound links']) {
      expect(screen.getByRole('button', { name: new RegExp(`^${name}`) })).toBeInTheDocument();
    }
    // Blocking gates open by default.
    expect(await screen.findByLabelText('Override Originality')).toBeInTheDocument();
    expect(screen.getByText(/This gate can’t be overridden/)).toBeInTheDocument();
    expect(screen.getByText('And 3 more not kept with the report. The live checks list every one.')).toBeInTheDocument();

    let sent: { ifMatch: string | null; body: unknown } | undefined;
    server.use(
      http.post(apiUrl(`/api/articles/${ARTICLE_ID}/gates/originality/override`), async ({ request }) => {
        sent = { ifMatch: request.headers.get('If-Match'), body: await request.json() };
        return HttpResponse.json({ item: draft.article, etag: '"etag-after-override"' });
      }),
    );
    await user.type(screen.getByLabelText('Override Originality'), 'The list is a deliberate summary.');
    await user.click(screen.getByRole('button', { name: 'Record the override' }));
    await waitFor(() => {
      expect(sent).toEqual({ ifMatch: '"etag-7"', body: { reason: 'The list is a deliberate summary.' } });
    });
  });
});
