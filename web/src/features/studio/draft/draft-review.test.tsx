import { act, screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';

import { parseStreamEvent, type StreamEvent, type StreamEventType } from '@/lib/events/events';
import { apiUrl, server } from '@/test/msw';
import { deltaPayload, jobEventPayload, snapshotPayload, stepPayload } from '@/test/job-events';

import { DraftTab } from './draft-tab';
import { ARTICLE_ID, articleFixture, articleVersion, gatesViewFixture, SAMPLE_MARKDOWN, versionSummary } from './testing/fixtures';
import { createDraftServer, problemResponse } from './testing/handlers';
import { findArticleEditor, installEditorLayout, renderDraft } from './testing/environment';

beforeAll(() => {
  installEditorLayout();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function ev(type: StreamEventType, payload: unknown): StreamEvent {
  const event = parseStreamEvent(type, JSON.stringify(payload));
  if (!event) throw new Error(`not a ${type} event`);
  return event;
}

function setup(overrides: Parameters<typeof articleFixture>[0] = {}) {
  const draft = createDraftServer(articleFixture(overrides));
  server.use(...draft.handlers);
  return draft;
}

async function loaded() {
  const editor = await findArticleEditor();
  await waitFor(() => {
    expect(editor.getText()).toContain('Why agents need identity');
  });
  return editor;
}

describe('DraftTab: review actions', () => {
  it('approves an article whose gates are clear, with If-Match', async () => {
    const draft = setup();
    let sent: { ifMatch: string | null; body: unknown } | undefined;
    server.use(
      http.post(apiUrl(`/api/articles/${ARTICLE_ID}/approve`), async ({ request }) => {
        sent = { ifMatch: request.headers.get('If-Match'), body: await request.json() };
        const article = { ...draft.article, status: 'approved' as const, publish: { live: false, auto: false, publishAt: '2026-11-30T14:00:00.000000Z' } };
        return HttpResponse.json({ article: { item: article, etag: '"etag-approved"' }, jobId: 'publish-job-1' });
      }),
    );
    const { user } = renderDraft(<DraftTab pieceId={ARTICLE_ID} />);
    await loaded();
    await user.click(screen.getByRole('button', { name: 'Approve…' }));
    const dialog = await screen.findByRole('dialog', { name: 'Approve for publishing' });
    const checklist = within(dialog).getByRole('list', { name: 'Before approving' });
    expect(within(checklist).getByText('Every gate passed or was overridden.')).toBeInTheDocument();
    expect(within(checklist).getByText('The article details are complete.')).toBeInTheDocument();
    await user.click(within(dialog).getByRole('button', { name: 'Approve' }));
    await waitFor(() => {
      expect(sent).toEqual({ ifMatch: '"etag-7"', body: {} });
    });
    expect(await screen.findByText('Approved. It publishes at the time you chose.')).toBeInTheDocument();
    expect(await screen.findByText(/^Publishes /)).toBeInTheDocument();
    // The Approve button is gone, so focus lands on the article's status, not the page body.
    await waitFor(() => {
      expect(document.activeElement).toHaveTextContent('Status: Approved');
    });
  });

  it('explains a refused approval in the dialog: the gates that block it', async () => {
    setup();
    server.use(
      http.post(apiUrl(`/api/articles/${ARTICLE_ID}/approve`), () =>
        problemResponse(409, 'gates_blocking', { blocking: ['fact_check', 'outbound'] }),
      ),
    );
    const { user } = renderDraft(<DraftTab pieceId={ARTICLE_ID} />);
    await loaded();
    await user.click(screen.getByRole('button', { name: 'Approve…' }));
    const dialog = await screen.findByRole('dialog', { name: 'Approve for publishing' });
    await user.click(within(dialog).getByRole('button', { name: 'Approve' }));
    const alert = await within(dialog).findByRole('alert');
    expect(within(alert).getByText('Couldn’t approve it.')).toBeInTheDocument();
    expect(within(alert).getByText('Blocking: Facts, Outbound links.')).toBeInTheDocument();
    expect(within(alert).getByText('req-draft-1')).toBeInTheDocument();
  });

  it('does not offer approval while the gates report is for an older version', async () => {
    const draft = setup();
    draft.gates = gatesViewFixture([], { stale: true, manualPublishReady: false, autoPublishReady: false });
    const { user } = renderDraft(<DraftTab pieceId={ARTICLE_ID} />);
    await loaded();
    expect(await screen.findByText('The text changed since the gates ran.')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Approve…' }));
    const dialog = await screen.findByRole('dialog', { name: 'Approve for publishing' });
    expect(within(dialog).getByText('The text changed since the gates ran. Run them again.')).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: 'Approve' })).toBeDisabled();
  });

  it('requests a revision and goes read-only while the pipeline revises', async () => {
    const draft = setup();
    let sent: unknown;
    server.use(
      http.post(apiUrl(`/api/articles/${ARTICLE_ID}/revise`), async ({ request }) => {
        sent = await request.json();
        draft.article = { ...draft.article, status: 'drafting', pipelineJobId: 'revise-job-1' };
        draft.etag = '"etag-revising"';
        return HttpResponse.json({ article: { item: draft.article, etag: draft.etag }, jobId: 'revise-job-1' }, { status: 202 });
      }),
    );
    const { user } = renderDraft(<DraftTab pieceId={ARTICLE_ID} />);
    const editor = await loaded();
    await user.click(screen.getByRole('button', { name: 'Request a revision' }));
    const dialog = await screen.findByRole('dialog', { name: 'Request a revision' });
    await user.type(within(dialog).getByLabelText(/What should change/), 'Cut the second section.');
    await user.click(within(dialog).getByRole('button', { name: 'Revise the draft' }));
    await waitFor(() => {
      expect(sent).toEqual({ instruction: 'Cut the second section.' });
    });
    expect(await screen.findByText('Drafting')).toBeInTheDocument();
    await waitFor(() => {
      expect(editor.isEditable).toBe(false);
    });
    expect(screen.getByRole('button', { name: 'Stop the run' })).toBeInTheDocument();
  });

  it('starts the pipeline for a planned article from research', async () => {
    const draft = setup({ status: 'planned', markdown: '', version: 0, briefId: null, pipelineJobId: null });
    let sent: { ifMatch: string | null; body: unknown } | undefined;
    server.use(
      http.post(apiUrl(`/api/articles/${ARTICLE_ID}/pipeline`), async ({ request }) => {
        sent = { ifMatch: request.headers.get('If-Match'), body: await request.json() };
        draft.article = { ...draft.article, status: 'researching', pipelineJobId: 'pipeline-job-2' };
        return HttpResponse.json({ article: { item: draft.article, etag: '"etag-started"' }, jobId: 'pipeline-job-2' }, { status: 202 });
      }),
    );
    const { user } = renderDraft(<DraftTab pieceId={ARTICLE_ID} />);
    await screen.findByRole('heading', { level: 2, name: 'No draft yet.' });
    const [start] = await screen.findAllByRole('button', { name: 'Start the pipeline' });
    if (!start) throw new Error('no start button');
    await user.click(start);
    const dialog = await screen.findByRole('dialog', { name: 'Start the pipeline' });
    expect(within(dialog).getByRole('radio', { name: 'From the interview' })).toBeDisabled();
    await user.click(within(dialog).getByRole('button', { name: 'Start' }));
    await waitFor(() => {
      expect(sent).toEqual({ ifMatch: '"etag-7"', body: { startFrom: 'research' } });
    });
    expect(await screen.findByText('Researching')).toBeInTheDocument();
  });

  it('asks before vetoing an auto-publish, then vetoes it', async () => {
    const draft = setup({ status: 'approved', publish: { live: false, auto: true, publishAt: '2026-11-30T14:00:00.000000Z' } });
    server.use(
      http.get(apiUrl(`/api/articles/${ARTICLE_ID}/veto`), () =>
        HttpResponse.json({ publishAt: '2026-11-30T14:00:00.000000Z', auto: true, notice: { sentAt: '2026-11-27T17:00:00.000000Z', delivered: true, receiptAt: '2026-11-27T17:00:02.000000Z' } }),
      ),
    );
    let vetoed = false;
    server.use(
      http.post(apiUrl(`/api/articles/${ARTICLE_ID}/veto`), () => {
        vetoed = true;
        draft.article = { ...draft.article, status: 'in_review' };
        return HttpResponse.json({ item: draft.article, etag: '"etag-vetoed"' });
      }),
    );
    const { user } = renderDraft(<DraftTab pieceId={ARTICLE_ID} />);
    expect(await screen.findByText(/The veto notice reached your phone/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Veto' }));
    const dialog = await screen.findByRole('alertdialog', { name: 'Veto this auto-publish?' });
    expect(within(dialog).getByRole('button', { name: 'Let it publish' })).toHaveFocus();
    await user.click(within(dialog).getByRole('button', { name: 'Veto' }));
    await waitFor(() => {
      expect(vetoed).toBe(true);
    });
    expect(await screen.findByText('In review')).toBeInTheDocument();
  });

  it('shows where publishing stands: live with a link, or failed with the reason', async () => {
    setup({ status: 'published', publish: { live: true, auto: false, url: 'https://example.com/blog/agent-identity-in-practice', publishedAt: '2026-11-20T14:00:00.000000Z', lastPublishedVersion: 7 } });
    renderDraft(<DraftTab pieceId={ARTICLE_ID} />);
    const link = await screen.findByRole('link', { name: /Open the live page/ });
    expect(link).toHaveAttribute('href', 'https://example.com/blog/agent-identity-in-practice');
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', 'noopener noreferrer');
    expect(await screen.findByRole('button', { name: 'Start a correction' })).toBeInTheDocument();
  });

  it('explains a failed publish and offers the retry', async () => {
    setup({ status: 'publish_failed', publish: { live: false, auto: false, lastOperation: 'publish', lastError: 'build_failed' } });
    renderDraft(<DraftTab pieceId={ARTICLE_ID} />);
    expect(await screen.findByText('Publishing failed')).toBeInTheDocument();
    expect(screen.getByText(/The portfolio build failed/)).toBeInTheDocument();
    expect(await screen.findByRole('button', { name: 'Retry publishing' })).toBeInTheDocument();
    // The owner may also give up on the run and edit again (publish_failed -> in_review).
    expect(screen.getByRole('button', { name: 'Back to review' })).toBeInTheDocument();
  });

  it('sends a failed publish back to review', async () => {
    const draft = setup({ status: 'publish_failed', publish: { live: false, auto: false, lastOperation: 'publish', lastError: 'build_failed' } });
    const { user } = renderDraft(<DraftTab pieceId={ARTICLE_ID} />);
    await user.click(await screen.findByRole('button', { name: 'Back to review' }));
    await waitFor(() => {
      expect(draft.writes).toEqual([
        { path: 'transition', ifMatch: '"etag-7"', body: { to: 'in_review', reason: 'Back to review after a failed publish.' } },
      ]);
    });
    expect(await screen.findByText('Back in review. Change what you need, then approve it again.')).toBeInTheDocument();
  });

  it('says a publishing run still owns the article and where to stop it', async () => {
    setup({ status: 'publish_failed', publish: { live: false, auto: false, lastOperation: 'publish', lastError: 'build_failed' } });
    server.use(
      http.post(apiUrl(`/api/articles/${ARTICLE_ID}/publish-now`), () =>
        problemResponse(409, 'publish_in_progress', {
          detail: 'A publish run is still working on this article. Wait for it or cancel it first.',
        }),
      ),
    );
    const { user } = renderDraft(<DraftTab pieceId={ARTICLE_ID} />);
    await user.click(await screen.findByRole('button', { name: 'Retry publishing' }));
    const dialog = await screen.findByRole('alertdialog', { name: 'Publish it now?' });
    await user.click(within(dialog).getByRole('button', { name: 'Retry publishing' }));
    const problem = await within(dialog).findByRole('alert');
    expect(problem).toHaveTextContent('A publish run is still working on this article.');
    expect(within(problem).getByRole('link', { name: 'See the running jobs in System' })).toHaveAttribute('href', '/settings/system');
  });

  it('keeps the text read-only and offers nothing until the article’s rules load', async () => {
    setup({ status: 'in_review' });
    server.use(http.get(apiUrl('/api/meta/machines'), () => problemResponse(422, 'validation_failed')));
    renderDraft(<DraftTab pieceId={ARTICLE_ID} />);
    const editor = await findArticleEditor();
    expect(await screen.findByText(/Couldn’t load what can be done with this article/)).toBeInTheDocument();
    expect(editor.isEditable).toBe(false);
    expect(screen.queryByRole('button', { name: 'Approve…' })).not.toBeInTheDocument();
  });
});

describe('DraftTab: live pipeline text', () => {
  it('shows the draft read-only as it streams, and the stored article after a reset', async () => {
    setup({ status: 'drafting', pipelineJobId: 'pipeline-job-1' });
    const { appData } = renderDraft(<DraftTab pieceId={ARTICLE_ID} />);
    await loaded();
    act(() => {
      appData.jobs.apply(
        ev(
          'snapshot',
          snapshotPayload({
            jobId: 'pipeline-job-1',
            type: 'pipeline.article',
            steps: [stepPayload('outline', 'succeeded'), stepPayload('draft', 'running'), stepPayload('normalize')],
            events: [],
          }),
        ),
      );
      appData.jobs.apply(ev('delta', deltaPayload('## A fresh start\n\nThe pipeline is writing', { step: 'draft', jobId: 'pipeline-job-1' })));
    });
    const live = await findArticleEditor(/as it is written/);
    await waitFor(() => {
      expect(live.getText()).toContain('A fresh start');
    });
    expect(live.isEditable).toBe(false);
    expect(screen.getByText('Writing the draft…')).toBeInTheDocument();

    act(() => {
      appData.jobs.apply(ev('job', jobEventPayload(10, 'draft.reset', { step: 'draft', jobId: 'pipeline-job-1' })));
    });
    await waitFor(() => {
      expect(screen.queryByText('Writing the draft…')).not.toBeInTheDocument();
    });
    expect(screen.getByRole('textbox', { name: 'Article text' })).toHaveAttribute('aria-readonly', 'true');
  });
});

describe('DraftTab: versions, preview and export', () => {
  it('compares a version with the current text and restores it', async () => {
    const draft = setup();
    draft.versions = [versionSummary(7, { pinned: true, source: 'pipeline' }), versionSummary(6, { source: 'autosave', author: 'owner' })];
    let restored: string | null = null;
    server.use(
      http.get(apiUrl(`/api/articles/${ARTICLE_ID}/versions/6`), () =>
        HttpResponse.json({ item: articleVersion(6, '## Why agents need identity\n\nAn older paragraph.\n'), etag: '"v6"' }),
      ),
      http.get(apiUrl(`/api/articles/${ARTICLE_ID}/versions/7`), () => HttpResponse.json({ item: articleVersion(7, SAMPLE_MARKDOWN), etag: '"v7"' })),
      http.post(apiUrl(`/api/articles/${ARTICLE_ID}/versions/6/restore`), ({ request }) => {
        restored = request.headers.get('If-Match');
        draft.article = { ...draft.article, markdown: '## Why agents need identity\n\nAn older paragraph.\n', version: 8 };
        draft.etag = '"etag-restored"';
        return HttpResponse.json({ item: draft.article, etag: draft.etag });
      }),
    );
    const { user } = renderDraft(<DraftTab pieceId={ARTICLE_ID} />);
    const editor = await loaded();
    await user.click(screen.getByRole('button', { name: 'More actions' }));
    await user.click(await screen.findByRole('menuitem', { name: 'Versions' }));
    const sheet = await screen.findByRole('dialog', { name: 'Versions' });
    const list = await within(sheet).findByRole('list', { name: 'Saved versions' });
    expect(within(list).getByText('Kept')).toBeInTheDocument();
    await user.click(within(list).getByRole('button', { name: /Version 6/ }));
    expect(await within(sheet).findByRole('group', { name: 'Version 6 compared with the current text' })).toBeInTheDocument();
    await user.click(within(sheet).getByRole('button', { name: 'Restore this version' }));
    const confirm = await screen.findByRole('alertdialog', { name: 'Restore version 6?' });
    await user.click(within(confirm).getByRole('button', { name: 'Restore' }));
    await waitFor(() => {
      expect(restored).toBe('"etag-7"');
    });
    await waitFor(() => {
      expect(editor.getText()).toContain('An older paragraph.');
    });
  });

  it('previews the portfolio page in a sandboxed frame', async () => {
    setup();
    const { user } = renderDraft(<DraftTab pieceId={ARTICLE_ID} />);
    await loaded();
    await user.click(screen.getByRole('button', { name: 'More actions' }));
    await user.click(await screen.findByRole('menuitem', { name: 'Preview as portfolio page' }));
    const dialog = await screen.findByRole('dialog', { name: 'Preview as portfolio page' });
    const frame = within(dialog).getByTitle('Portfolio preview of “Agent identity in practice”');
    expect(frame.tagName).toBe('IFRAME');
    expect(frame).toHaveAttribute('sandbox', '');
    expect(frame).toHaveAttribute('src', `/api/articles/${ARTICLE_ID}/preview`);
    await user.click(within(dialog).getByRole('radio', { name: 'Phone' }));
    expect(within(dialog).getByTitle(/Portfolio preview/)).toHaveClass('w-[24.375rem]');
  });

  it('exports the Markdown file, and explains when details are missing', async () => {
    setup();
    const createObjectURL = vi.fn(() => 'blob:export');
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL, revokeObjectURL: vi.fn() }));
    server.use(
      http.get(apiUrl(`/api/articles/${ARTICLE_ID}/export.md`), () =>
        new HttpResponse('---\ntitle: "Agent identity in practice"\n---\n\nBody\n', {
          headers: { 'Content-Type': 'text/markdown; charset=utf-8', 'Content-Disposition': 'attachment; filename="agent-identity-in-practice.md"' },
        }),
      ),
    );
    const { user } = renderDraft(<DraftTab pieceId={ARTICLE_ID} />);
    await loaded();
    await user.click(screen.getByRole('button', { name: 'More actions' }));
    await user.click(await screen.findByRole('menuitem', { name: 'Export Markdown' }));
    expect(await screen.findByText('Exported the Markdown file.')).toBeInTheDocument();
    expect(createObjectURL).toHaveBeenCalledTimes(1);

    server.use(http.get(apiUrl(`/api/articles/${ARTICLE_ID}/export.md`), () => problemResponse(409, 'meta_incomplete', { missing: ['excerpt'] })));
    await user.click(screen.getByRole('button', { name: 'More actions' }));
    await user.click(await screen.findByRole('menuitem', { name: 'Export Markdown' }));
    expect(await screen.findByText('Couldn’t export: missing excerpt.')).toBeInTheDocument();
  });
});

const SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><text>é</text></svg>';

describe('DraftTab: dialect nodes', () => {
  it('names citations by their brief source and renders diagrams through the server', async () => {
    setup({ markdown: `${SAMPLE_MARKDOWN.split('\n\n[^')[0] ?? ''}\n\n\`\`\`mermaid\nflowchart LR\n  a --> b\n\`\`\`\n\n[^nist-ai-rmf]: NIST. *AI Risk Management Framework*. [nist.gov](https://www.nist.gov/)\n` });
    let rendered: unknown;
    server.use(
      http.post(apiUrl('/api/render/mermaid'), async ({ request }) => {
        rendered = await request.json();
        return HttpResponse.json({ svg: SVG, width: 10, height: 10 });
      }),
    );
    renderDraft(<DraftTab pieceId={ARTICLE_ID} />);
    await loaded();
    expect(await screen.findByLabelText('Citation: AI Risk Management Framework, nist.gov')).toHaveTextContent('NIST');
    const diagram = await screen.findByRole('img', { name: 'Rendered preview of the diagram' });
    // An image source, never markup in the page: the SVG cannot run script or reach the DOM.
    const src = diagram.getAttribute('src') ?? '';
    expect(src.startsWith('data:image/svg+xml;base64,')).toBe(true);
    const bytes = Uint8Array.from(atob(src.slice('data:image/svg+xml;base64,'.length)), (char) => char.charCodeAt(0));
    expect(new TextDecoder().decode(bytes)).toBe(SVG);
    expect(document.querySelector('[data-type="mermaid"] svg text')).toBeNull(); // only icons are inline SVG
    expect(rendered).toEqual({ source: 'flowchart LR\n  a --> b' });
  });

  it('says so when this server cannot render diagrams', async () => {
    setup({ markdown: '## Diagram\n\n```mermaid\nflowchart LR\n  a --> b\n```\n' });
    server.use(http.post(apiUrl('/api/render/mermaid'), () => problemResponse(503, 'render_unavailable')));
    renderDraft(<DraftTab pieceId={ARTICLE_ID} />);
    expect(await screen.findByText(/Live diagram previews aren’t available on this server right now/)).toBeInTheDocument();
  });

  it('shows why a diagram did not render', async () => {
    setup({ markdown: '## Diagram\n\n```mermaid\nflowchart LR\n  a --> \n```\n' });
    server.use(
      http.post(apiUrl('/api/render/mermaid'), () =>
        problemResponse(422, 'mermaid_error', { detail: 'A diagram could not be drawn. Check its Mermaid syntax.' }),
      ),
    );
    renderDraft(<DraftTab pieceId={ARTICLE_ID} />);
    expect(await screen.findByRole('alert')).toHaveTextContent('The diagram didn’t render: A diagram could not be drawn.');
  });
});

describe('DraftTab: other state changes', () => {
  it('stops a running pipeline after asking', async () => {
    const draft = setup({ status: 'drafting', pipelineJobId: 'pipeline-job-1' });
    let cancelled: string | null = null;
    server.use(
      http.post(apiUrl(`/api/articles/${ARTICLE_ID}/pipeline/cancel`), ({ request }) => {
        cancelled = request.headers.get('If-Match');
        draft.article = { ...draft.article, status: 'needs_attention', lastError: 'cancelled' };
        return HttpResponse.json({ item: draft.article, etag: '"etag-cancelled"' });
      }),
    );
    const { user } = renderDraft(<DraftTab pieceId={ARTICLE_ID} />);
    await user.click(await screen.findByRole('button', { name: 'Stop the run' }));
    const dialog = await screen.findByRole('alertdialog', { name: 'Stop the pipeline run?' });
    await user.click(within(dialog).getByRole('button', { name: 'Stop the run' }));
    await waitFor(() => {
      expect(cancelled).toBe('"etag-7"');
    });
    expect(await screen.findByText('The pipeline run was cancelled.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Back to review' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Restart the pipeline' })).toBeInTheDocument();
  });

  it('moves an article that needs you back to review', async () => {
    const draft = setup({ status: 'needs_attention', lastError: 'refused' });
    const { user } = renderDraft(<DraftTab pieceId={ARTICLE_ID} />);
    expect(await screen.findByText(/The model declined to write this draft/)).toBeInTheDocument();
    await user.click(await screen.findByRole('button', { name: 'Back to review' }));
    await waitFor(() => {
      expect(draft.writes).toEqual([{ path: 'transition', ifMatch: '"etag-7"', body: { to: 'in_review', reason: null } }]);
    });
    expect(await screen.findByText('Back in review.')).toBeInTheDocument();
  });

  it('unpublishes a live article only after asking', async () => {
    setup({ status: 'published', publish: { live: true, auto: false, url: 'https://example.com/blog/a', publishedAt: '2026-11-20T14:00:00.000000Z' } });
    let unpublished: string | null = null;
    server.use(
      http.post(apiUrl(`/api/articles/${ARTICLE_ID}/unpublish`), ({ request }) => {
        unpublished = request.headers.get('If-Match');
        return HttpResponse.json({ jobId: 'unpublish-job-1', status: 'queued' }, { status: 202 });
      }),
    );
    const { user } = renderDraft(<DraftTab pieceId={ARTICLE_ID} />);
    await screen.findByRole('link', { name: /Open the live page/ });
    await user.click(screen.getByRole('button', { name: 'More actions' }));
    await user.click(await screen.findByRole('menuitem', { name: 'Unpublish…' }));
    const dialog = await screen.findByRole('alertdialog', { name: 'Unpublish this article?' });
    expect(within(dialog).getByRole('button', { name: 'Keep it live' })).toHaveFocus();
    await user.click(within(dialog).getByRole('button', { name: 'Unpublish' }));
    await waitFor(() => {
      expect(unpublished).toBe('"etag-7"');
    });
    expect(await screen.findByText('Unpublishing.')).toBeInTheDocument();
  });
});
