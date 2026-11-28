import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { beforeAll, describe, expect, it } from 'vitest';

import { apiUrl, server } from '@/test/msw';

import { DraftTab } from './draft-tab';
import { ARTICLE_ID, articleFixture, finding } from './testing/fixtures';
import { createDraftServer, problemResponse } from './testing/handlers';
import { findArticleEditor, installEditorLayout, renderDraft } from './testing/environment';

beforeAll(() => {
  installEditorLayout();
});

async function openEditable(overrides: Parameters<typeof articleFixture>[0] = {}) {
  const draft = createDraftServer(articleFixture(overrides));
  server.use(...draft.handlers);
  const view = renderDraft(<DraftTab pieceId={ARTICLE_ID} />);
  const editor = await findArticleEditor();
  await waitFor(() => {
    expect(editor.isEditable).toBe(true);
  });
  return { ...view, draft, editor };
}

describe('DraftTab: autosave', () => {
  it('saves a moment after an edit, with If-Match and the version it loaded', async () => {
    const { draft, editor } = await openEditable();
    act(() => {
      editor.chain().focus('end').insertContent(' Another sentence.').run();
    });
    await waitFor(() => {
      expect(draft.saves).toHaveLength(1);
    });
    const [save] = draft.saves;
    expect(save?.ifMatch).toBe('"etag-7"');
    expect(save?.body.baseVersion).toBe(7);
    expect(save?.body.markdown).toContain('Another sentence.');
    expect(save?.body.meta.title).toBe('Agent identity in practice');
    expect(await screen.findByText(/^Saved at/)).toBeInTheDocument();
    const answered = draft.etag;

    // The next save sends the etag and version the first one answered with.
    act(() => {
      editor.chain().focus('end').insertContent(' And one more.').run();
    });
    await waitFor(() => {
      expect(draft.saves).toHaveLength(2);
    });
    expect(draft.saves[1]?.ifMatch).toBe(answered);
    expect(draft.saves[1]?.body.baseVersion).toBe(8);
  });

  it('saves the article details with the text and checks them as you type', async () => {
    const { draft, user } = await openEditable();
    const notes = screen.getByRole('complementary', { name: 'Review notes' });
    await user.click(within(notes).getByRole('tab', { name: 'Details' }));
    const slug = await screen.findByLabelText('Slug');
    fireEvent.change(slug, { target: { value: 'Bad Slug' } });
    expect(await screen.findByText(/Use lowercase letters, digits and single hyphens/)).toBeInTheDocument();
    const title = screen.getByLabelText('Title');
    await user.clear(title);
    await user.type(title, 'Agent identity, in practice');
    await waitFor(() => {
      expect(draft.saves.at(-1)?.body.meta.title).toBe('Agent identity, in practice');
    });
    // The invalid slug is not sent: the last saved one is kept until it is fixed.
    expect(draft.saves.at(-1)?.body.meta.slug).toBe('agent-identity-in-practice');
  });

  it('opens the conflict dialog on a 412 and keeps the owner’s version on request', async () => {
    const { draft, editor, user } = await openEditable();
    // Another tab saves version 8 first.
    draft.bumpVersion('## Someone else wrote this\n', 'owner');
    act(() => {
      editor.chain().focus('end').insertContent(' My addition.').run();
    });
    const dialog = await screen.findByRole('alertdialog', { name: 'This draft changed since you opened it' });
    expect(within(dialog).getByText(/You saved a newer version in another tab/)).toBeInTheDocument();
    expect(screen.getByText('Not saved: changed elsewhere')).toBeInTheDocument();
    await waitFor(() => {
      expect(within(dialog).getByRole('group', { name: 'Your version compared with the saved one' })).toBeInTheDocument();
    });

    await user.click(within(dialog).getByRole('button', { name: 'Keep mine' }));
    await waitFor(() => {
      expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
    });
    const last = draft.saves.at(-1);
    expect(last?.body.baseVersion).toBe(8);
    expect(last?.body.markdown).toContain('My addition.');
    expect(draft.article.markdown).toContain('My addition.');
  });

  it('takes the saved version when the owner chooses theirs', async () => {
    const { draft, editor, user } = await openEditable();
    draft.bumpVersion('## Someone else wrote this\n', 'pipeline');
    act(() => {
      editor.chain().focus('end').insertContent(' Mine.').run();
    });
    const dialog = await screen.findByRole('alertdialog', { name: 'This draft changed since you opened it' });
    expect(within(dialog).getByText(/The pipeline saved a newer version/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole('button', { name: 'Take theirs' }));
    await waitFor(() => {
      expect(editor.getText()).toContain('Someone else wrote this');
    });
    expect(editor.getText()).not.toContain('Mine.');
  });

  it('stops saving when the article became read-only, and offers to copy the text', async () => {
    const { editor } = await openEditable();
    server.use(
      http.put(apiUrl(`/api/articles/${ARTICLE_ID}`), () =>
        problemResponse(409, 'read_only_state', { state: 'drafting', writer: 'pipeline' }),
      ),
    );
    act(() => {
      editor.chain().focus('end').insertContent(' Too late.').run();
    });
    expect(await screen.findByText('Your latest changes weren’t saved')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Copy my text' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Discard my changes' })).toBeInTheDocument();
    await waitFor(() => {
      expect(editor.isEditable).toBe(false);
    });
  });

  it('keeps changes while offline and says so', async () => {
    const { editor, unmount } = await openEditable();
    let puts = 0;
    server.use(
      http.put(apiUrl(`/api/articles/${ARTICLE_ID}`), () => {
        puts += 1;
        return HttpResponse.error();
      }),
    );
    act(() => {
      editor.chain().focus('end').insertContent(' Written on a train.').run();
    });
    expect(await screen.findByText('Offline: saves when you’re back')).toBeInTheDocument();
    expect(screen.getByText(/Your changes are kept in this tab/)).toBeInTheDocument();
    // Leaving sends the unsaved text once more: let it land here, not on the next test's server.
    const before = puts;
    unmount();
    await waitFor(() => {
      expect(puts).toBe(before + 1);
    });
  });
});

describe('DraftTab: writing tools', () => {
  it('opens the block menu on a slash and inserts the chosen block', async () => {
    const { editor } = await openEditable();
    let end = 0;
    editor.state.doc.descendants((node, pos) => {
      if (node.isTextblock && node.textContent.startsWith('Start with an inventory')) end = pos + node.nodeSize - 1;
      return true;
    });
    act(() => {
      editor.chain().setTextSelection(end).splitBlock().insertContent('/sub').run();
    });
    const menu = await screen.findByRole('listbox', { name: 'Insert a block' });
    const option = within(menu).getByRole('option', { name: /Subheading/ });
    expect(option).toHaveAttribute('aria-selected', 'true');
    const textbox = screen.getByRole('textbox', { name: 'Article text' });
    expect(textbox).toHaveAttribute('aria-controls', menu.id);
    expect(textbox).toHaveAttribute('aria-activedescendant', option.id);
    fireEvent.keyDown(textbox, { key: 'Enter' });
    await waitFor(() => {
      expect(screen.queryByRole('listbox', { name: 'Insert a block' })).not.toBeInTheDocument();
    });
    expect(editor.state.selection.$from.parent.type.name).toBe('heading');
    expect(editor.state.selection.$from.parent.attrs.level).toBe(3);
    expect(editor.getText()).not.toContain('/sub');
  });

  it('lists the live checks and marks them in the text', async () => {
    const draft = createDraftServer(articleFixture());
    draft.lintFindings = [
      finding({ code: 'citation-unknown-source', severity: 'block', message: 'A citation points at no brief source.', overridable: false, start: 30, end: 48 }),
      finding({ code: 'rhetorical-opener', severity: 'warn', message: 'The section opens with a rhetorical question.', start: 0, end: 10 }),
    ];
    server.use(...draft.handlers);
    renderDraft(<DraftTab pieceId={ARTICLE_ID} />);
    expect(await screen.findByText('1 blocker, 1 warning.')).toBeInTheDocument();
    expect(screen.getByText('A citation points at no brief source.')).toBeInTheDocument();
    const textbox = screen.getByRole('textbox', { name: 'Article text' });
    await waitFor(() => {
      expect(textbox.querySelectorAll('[data-finding]').length).toBeGreaterThan(0);
    });
  });

  it('rewrites a selection with AI: a diff first, and Accept replaces the passage', async () => {
    const { editor, user } = await openEditable();
    let sent: unknown;
    server.use(
      http.post(apiUrl(`/api/articles/${ARTICLE_ID}/selection`), async ({ request }) => {
        sent = await request.json();
        return HttpResponse.json({ proposal: 'Start by listing the agents you run today.', reason: null, reviewOnly: false });
      }),
    );
    // Select the second section's sentence.
    let from = 0;
    editor.state.doc.descendants((node, pos) => {
      if (node.isTextblock && node.textContent.startsWith('Start with an inventory')) from = pos + 1;
      return true;
    });
    const sentence = 'Start with an inventory of the agents you already run.';
    act(() => {
      editor.commands.setTextSelection({ from, to: from + sentence.length });
    });
    await user.click(screen.getByRole('button', { name: /Rewrite selection/ }));
    const instruction = await screen.findByLabelText('How should it change?');
    await user.type(instruction, 'Make it more direct');
    await user.click(screen.getByRole('button', { name: 'Propose a rewrite' }));
    expect(await screen.findByRole('group', { name: 'Proposed changes to the selection' })).toBeInTheDocument();
    expect(sent).toEqual({ text: sentence, instruction: 'Make it more direct' });
    await user.click(screen.getByRole('button', { name: 'Accept' }));
    await waitFor(() => {
      expect(editor.getText()).toContain('Start by listing the agents you run today.');
    });
    expect(editor.getText()).not.toContain('inventory');
    // One paragraph went into one paragraph: the section still has its heading before it.
    expect(editor.getText()).toContain('What to do first');
  });

  it('shows where a blocklisted term is when the selection AI refuses the instruction', async () => {
    const { editor, user } = await openEditable();
    server.use(
      http.post(apiUrl(`/api/articles/${ARTICLE_ID}/selection`), () =>
        problemResponse(422, 'confidential_text', {
          findings: [{ field: 'instruction', code: 'blocklist', severity: 'block', message: 'Blocklisted term.', start: 8, end: 15, ref: 'term-1' }],
        }),
      ),
    );
    act(() => {
      editor.commands.setTextSelection({ from: 2, to: 10 });
    });
    await user.click(screen.getByRole('button', { name: /Rewrite selection/ }));
    await user.type(await screen.findByLabelText('How should it change?'), 'Mention Contoso here');
    await user.click(screen.getByRole('button', { name: 'Propose a rewrite' }));
    expect(await screen.findByText('Part of this matches the blocklist. Rephrase it.')).toBeInTheDocument();
    expect(screen.getByText('Contoso')).toBeInTheDocument();
    expect(screen.getByText('Contoso').tagName).toBe('MARK');
  });
});

describe('DraftTab: inserting media and sources', () => {
  it('uploads an image with its alt text and puts it in its own paragraph', async () => {
    const { editor, user } = await openEditable();
    let upload: { type: string | null; role: string | null; size: number } | undefined;
    server.use(
      http.post(apiUrl('/api/media'), async ({ request }) => {
        const url = new URL(request.url);
        upload = { type: request.headers.get('Content-Type'), role: url.searchParams.get('role'), size: (await request.arrayBuffer()).byteLength };
        return HttpResponse.json(
          { role: 'image', container: 'media', name: 'uploads/2026-11/abc.png', sha256: 'd'.repeat(64), contentType: 'image/png', sizeBytes: 4, width: 1, height: 1 },
          { status: 201 },
        );
      }),
    );
    await user.click(screen.getByRole('button', { name: 'Insert' }));
    await user.click(await screen.findByRole('menuitem', { name: /Image/ }));
    const dialog = await screen.findByRole('dialog', { name: 'Add an image' });
    await user.click(within(dialog).getByRole('button', { name: 'Add the image' }));
    expect(within(dialog).getByText('Choose an image.')).toBeInTheDocument();
    expect(within(dialog).getByText('Describe the image: alt text is required.')).toBeInTheDocument();
    await user.upload(within(dialog).getByLabelText(/^Image/), new File([new Uint8Array([1, 2, 3, 4])], 'diagram.png', { type: 'image/png' }));
    await user.type(within(dialog).getByLabelText(/^Alt text/), 'The agent identity flow');
    await user.click(within(dialog).getByRole('button', { name: 'Add the image' }));
    await waitFor(() => {
      expect(screen.queryByRole('dialog', { name: 'Add an image' })).not.toBeInTheDocument();
    });
    expect(upload).toEqual({ type: 'image/png', role: 'image', size: 4 });
    expect(editor.getMarkdown()).toContain('![The agent identity flow](/api/media/uploads/2026-11/abc.png)');
  });

  it('cites a source from the brief with its definition', async () => {
    const { editor, user } = await openEditable();
    await user.click(screen.getByRole('button', { name: 'Insert' }));
    await user.click(await screen.findByRole('menuitem', { name: /Citation/ }));
    const picker = await screen.findByRole('dialog', { name: 'Cite a source' });
    await user.click(await within(picker).findByRole('option', { name: /OWASP Top 10 for LLM Applications/ }));
    await waitFor(() => {
      expect(editor.getMarkdown()).toContain('[^owasp-llm-top-10]: OWASP. *OWASP Top 10 for LLM Applications*.');
    });
    expect(await screen.findByLabelText('Citation: OWASP Top 10 for LLM Applications, owasp.org')).toBeInTheDocument();
  });
});
