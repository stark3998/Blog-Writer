import { Editor } from '@tiptap/core';
import { http, HttpResponse } from 'msw';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';

import { createTestAppData } from '@/test/app-data';
import { apiUrl, server } from '@/test/msw';

import { draftKeys } from '../api/queries';
import { createDraftExtensions } from '../editor/extensions';
import { ARTICLE_ID, articleFixture, versioned } from '../testing/fixtures';
import { createDraftServer, problemResponse } from '../testing/handlers';
import { installEditorLayout, testEnvironment } from '../testing/environment';

import { DraftSaver, metaFieldErrors } from './draft-saver';

let editor: Editor | undefined;

beforeAll(() => {
  installEditorLayout();
});

afterEach(() => {
  editor?.destroy();
  editor = undefined;
});

async function start(overrides: Parameters<typeof articleFixture>[0] = {}) {
  const draft = createDraftServer(articleFixture(overrides));
  server.use(...draft.handlers);
  const appData = createTestAppData();
  appData.queryClient.setQueryData(draftKeys.article(ARTICLE_ID), versioned(draft.article));
  const created = new Editor({
    element: document.createElement('div'),
    extensions: createDraftExtensions({ bus: null, slashKeys: null, dialect: null, parseTimeoutMs: 1000, renderDiagrams: false }),
    content: { type: 'doc', content: [{ type: 'paragraph' }] },
    injectCSS: false,
  });
  editor = created;
  const saver = new DraftSaver(
    { editor: created, api: appData.api, queryClient: appData.queryClient, articleId: ARTICLE_ID, env: testEnvironment() },
    draft.article.meta,
  );
  const conflicts: unknown[] = [];
  saver.connect({
    refetch: () => Promise.resolve({ data: { item: draft.article, etag: draft.etag } }),
    onConflict: (error) => conflicts.push(error),
  });
  await saver.load(draft.article);
  created.on('update', saver.noteEdit);
  return { draft, saver, editor: created, appData, conflicts };
}

describe('DraftSaver', () => {
  it('loads the article as its base, clean', async () => {
    const { saver, editor: target } = await start();
    expect(saver.getSnapshot()).toMatchObject({ loadedVersion: 7, dirty: false, loadProblem: null, status: { kind: 'idle' } });
    expect(target.getText()).toContain('Why agents need identity');
  });

  it('flushes edits at once, and sends nothing when nothing changed', async () => {
    const { saver, draft, editor: target, appData } = await start();
    await expect(saver.flush()).resolves.toBe(true);
    expect(draft.saves).toHaveLength(0);

    target.chain().focus('start').insertContent('Hello. ').run();
    expect(saver.getSnapshot().dirty).toBe(true);
    await expect(saver.flush()).resolves.toBe(true);
    expect(draft.saves).toHaveLength(1);
    expect(saver.getSnapshot()).toMatchObject({ dirty: false, loadedVersion: 8, status: { kind: 'saved' } });
    // The answer is in the cache, so the next save sends its etag.
    expect(appData.queryClient.getQueryData(draftKeys.article(ARTICLE_ID))).toMatchObject({ etag: draft.etag });
  });

  it('merges a stale etag when the version did not change (only background writers saved)', async () => {
    const { saver, draft, editor: target } = await start();
    draft.etag = '"etag-after-gates-report"';
    target.chain().focus('start').insertContent('Hello. ').run();
    await expect(saver.flush()).resolves.toBe(true);
    expect(draft.saves[0]?.ifMatch).toBe('"etag-7"');
    expect(draft.saves[0]?.body.baseVersion).toBe(7);
  });

  it('pauses on a 412, reports the conflict, and saves over it with "keep mine"', async () => {
    const { saver, draft, editor: target, conflicts } = await start();
    draft.bumpVersion('## Theirs\n', 'pipeline');
    target.chain().focus('start').insertContent('Mine. ').run();
    await expect(saver.flush()).resolves.toBe(false);
    expect(conflicts).toHaveLength(1);
    expect(saver.getSnapshot().status).toEqual({ kind: 'conflict' });
    expect(saver.getSnapshot().conflictMine).toContain('Mine.');
    // Paused: nothing more is sent until the owner decides.
    target.commands.insertContent('More. ');
    await expect(saver.flush()).resolves.toBe(false);
    expect(draft.saves).toHaveLength(1);

    await expect(saver.keepMine({ etag: draft.etag, version: draft.article.version })).resolves.toBe(true);
    expect(draft.saves.at(-1)?.body.baseVersion).toBe(8);
    expect(draft.article.markdown).toContain('Mine.');
  });

  it('takes theirs: drops local changes and loads the saved version', async () => {
    const { saver, draft, editor: target } = await start();
    draft.bumpVersion('## Theirs\n', 'owner');
    target.chain().focus('start').insertContent('Mine. ').run();
    await saver.flush();
    await saver.takeTheirs();
    expect(target.getText().trim()).toBe('Theirs');
    expect(saver.getSnapshot()).toMatchObject({ dirty: false, loadedVersion: 8, conflictMine: undefined });
  });

  it('does not reload over unsaved changes, and loads a newer version once clean', async () => {
    const { saver, draft, editor: target } = await start();
    target.chain().focus('start').insertContent('Local. ').run();
    saver.receive({ ...draft.article, version: 9, markdown: 'Newer\n' });
    await vi.waitFor(() => {
      expect(target.getText()).toContain('Local.');
    });
    await saver.flush();
    saver.receive({ ...draft.article, version: 99, markdown: 'Much newer\n' });
    await vi.waitFor(() => {
      expect(target.getText()).toBe('Much newer');
    });
  });

  it('turns field errors from the server into detail errors and waits for a fix', async () => {
    const { saver, editor: target } = await start();
    server.use(
      http.put(apiUrl(`/api/articles/${ARTICLE_ID}`), () =>
        problemResponse(422, 'validation_failed', {
          errors: [{ loc: ['body', 'meta', 'slug'], msg: 'String should match pattern', type: 'string_pattern_mismatch' }],
        }),
      ),
    );
    target.chain().focus('start').insertContent('Hello. ').run();
    await expect(saver.flush()).resolves.toBe(false);
    expect(saver.getSnapshot()).toMatchObject({ status: { kind: 'invalid' }, serverErrors: { slug: 'String should match pattern' } });
    expect(metaFieldErrors({ 'meta.series.part': 'Too small', 'meta.tags.2': 'Bad tag', markdown: 'x' })).toEqual({
      'series.part': 'Too small',
      tags: 'Bad tag',
    });
  });

  it('retries a server error, then gives up and waits for the owner', async () => {
    const { saver, editor: target } = await start();
    let calls = 0;
    server.use(
      http.put(apiUrl(`/api/articles/${ARTICLE_ID}`), () => {
        calls += 1;
        return problemResponse(503, 'store_unavailable');
      }),
    );
    target.chain().focus('start').insertContent('Hello. ').run();
    await saver.flush();
    await vi.waitFor(() => {
      expect(saver.getSnapshot().status.kind).toBe('failed');
    });
    expect(calls).toBe(2);
  });

  it('sends what is unsaved when the tab goes away', async () => {
    const { saver, draft, editor: target } = await start();
    target.chain().focus('start').insertContent('Last words. ').run();
    saver.leave();
    await vi.waitFor(() => {
      expect(draft.saves).toHaveLength(1);
    });
    expect(draft.saves[0]?.body.markdown).toContain('Last words.');
    expect(saver.hasUnsaved()).toBe(false);
  });

  it('refuses to save past 128 KB', async () => {
    const { saver, draft, editor: target } = await start();
    target.chain().focus('end').insertContent({ type: 'codeBlock', content: [{ type: 'text', text: 'x'.repeat(140 * 1024) }] }).run();
    await expect(saver.flush()).resolves.toBe(false);
    expect(saver.getSnapshot().status).toEqual({ kind: 'too-large' });
    expect(draft.saves).toHaveLength(0);
  });

  it('reports a body the editor cannot open', async () => {
    const draft = createDraftServer(articleFixture());
    server.use(...draft.handlers, http.get(apiUrl('/api/never'), () => HttpResponse.json({})));
    const appData = createTestAppData();
    const created = new Editor({
      element: document.createElement('div'),
      extensions: createDraftExtensions({ bus: null, slashKeys: null, dialect: null, parseTimeoutMs: 1000, renderDiagrams: false }),
      content: { type: 'doc', content: [{ type: 'paragraph' }] },
      injectCSS: false,
    });
    editor = created;
    const saver = new DraftSaver(
      { editor: created, api: appData.api, queryClient: appData.queryClient, articleId: ARTICLE_ID, env: testEnvironment() },
      undefined,
    );
    await saver.load({ ...draft.article, markdown: 'x'.repeat(130 * 1024) });
    expect(saver.getSnapshot().loadProblem).toEqual({ kind: 'too-large', bytes: 130 * 1024 });
  });
});
