import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { toast } from 'sonner';
import { describe, expect, it, vi } from 'vitest';

import { apiUrl, server } from '@/test/msw';
import { findPageHeading, renderApp } from '@/test/render-app';

import { PostsTab } from './posts-tab';
import {
  ARTICLE_ID,
  CAROUSEL_MEDIA,
  CAROUSEL_SPEC,
  DISPLAY_TEXT,
  HOOKS,
  makePost,
  POST_ID,
  SECOND_POST_ID,
  T0,
} from './testing/fixtures';
import { postsServer, problemWith } from './testing/handlers';
import { renderTab } from './testing/render';

const NATIVE = makePost({ lane: 'native', articleId: null });

function openPost(post = NATIVE, extra: Parameters<typeof postsServer>[1] = {}) {
  const api = postsServer([post], extra);
  server.use(...api.handlers);
  const view = renderTab(<PostsTab pieceId={post.id} />, `/studio/${post.id}/posts`);
  return { ...view, api };
}

async function textBox() {
  await screen.findByRole('heading', { level: 2, name: 'Hook and text' });
  return screen.getByRole<HTMLTextAreaElement>('textbox', { name: 'Post text' });
}

describe('the LinkedIn composer', () => {
  it('shows the post with its hooks, counter, checks and the feed preview', async () => {
    openPost();
    const text = await textBox();
    expect(text).toHaveValue(DISPLAY_TEXT);
    const hooks = screen.getByRole('radiogroup', { name: 'Hook' });
    expect(within(hooks).getAllByRole('radio')).toHaveLength(3);
    expect(within(hooks).getByRole('radio', { name: /Hook 1:/ })).toBeChecked();
    expect(screen.getByText('Result or metric')).toBeInTheDocument();
    expect(screen.getByText('Your own words')).toBeInTheDocument();
    expect(screen.getByRole('progressbar', { name: 'Length of the post' })).toBeInTheDocument();
    expect(await screen.findByText(`${DISPLAY_TEXT.length.toLocaleString()} of 3,000 characters`)).toBeInTheDocument();
    expect(screen.getByRole('figure', { name: 'LinkedIn preview, phone width' })).toBeInTheDocument();
    expect(screen.getByRole('group', { name: 'Lane, pillar and format' })).toHaveTextContent('Native');
    expect(screen.getByRole('button', { name: /Pillar: Identity modernization/ })).toBeInTheDocument();
    for (const name of ['Hook and text', 'Preview', 'Checks', 'Approval']) {
      expect(screen.getByRole('heading', { level: 2, name })).toBeInTheDocument();
    }
    expect(await screen.findByText('Nothing blocks approval.')).toBeInTheDocument();
  });

  it('replaces the opening when you pick another hook, and switches the preview width', async () => {
    const { user } = openPost();
    const text = await textBox();
    await user.click(screen.getByRole('radio', { name: /Hook 2:/ }));
    expect(text.value.startsWith(`${HOOKS[1]?.text ?? ''}\n\n`)).toBe(true);
    expect(text.value).toContain('Three things mattered most');
    expect(screen.getByRole('region', { name: 'Unsaved changes' })).toHaveTextContent('Unsaved changes.');

    await user.click(screen.getByRole('radio', { name: 'Desktop' }));
    expect(await screen.findByRole('figure', { name: 'LinkedIn preview, desktop width' })).toBeInTheDocument();
  });

  it('adds and removes the pillar’s approved hashtags in the text', async () => {
    const { user } = openPost();
    const text = await textBox();
    // The pillar's full list arrives with the pillars; until then the post's own tags show.
    await user.click(await screen.findByRole('button', { name: '#CIAM' }));
    const tags = screen.getByRole('toolbar', { name: 'Hashtags' });
    expect(text.value.endsWith('#IdentityModernization #Entra #CIAM')).toBe(true);
    await user.click(within(tags).getByRole('button', { name: '#Entra' }));
    expect(text.value.endsWith('#IdentityModernization #CIAM')).toBe(true);
  });

  it('lints as you type: a link blocks approval and “Show in the text” selects it', async () => {
    const { user } = openPost();
    const text = await textBox();
    const withLink = `${DISPLAY_TEXT}\n\nhttps://example.com/x`;
    fireEvent.change(text, { target: { value: withLink } });
    expect(await screen.findByText('1 problem blocks approval.')).toBeInTheDocument();
    expect(screen.getByText('Remove the link: posts carry no links.')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Show in the text' }));
    expect(text).toHaveFocus();
    expect(text.selectionStart).toBe(DISPLAY_TEXT.length + 2);
    expect(text.selectionEnd).toBe(withLink.length);
    expect(screen.getByRole('button', { name: 'Approve' })).toBeDisabled();
    expect(screen.getByRole('list', { name: 'Before you can approve' })).toHaveTextContent('Fix the problems that block approval.');
  });

  it('saves with the etag the edit started from, and a newer save opens the conflict dialog', async () => {
    const success = vi.spyOn(toast, 'success').mockReturnValue(1);
    const { user, api } = openPost();
    const text = await textBox();
    fireEvent.change(text, { target: { value: `${DISPLAY_TEXT}\n\nOne more line.` } });
    // Someone else saves first (another tab): the server's etag moves on.
    api.change(POST_ID, { displayText: 'The pipeline’s version.' });
    await user.click(screen.getByRole('button', { name: 'Save changes' }));

    const dialog = await screen.findByRole('alertdialog', { name: 'This post changed since you opened it' });
    expect(api.calls('PUT', `/api/posts/${POST_ID}`).map((call) => call.ifMatch)).toEqual(['"1"']);
    await waitFor(() => {
      expect(within(dialog).getByRole('group', { name: 'Your version compared with the saved one' })).toBeInTheDocument();
    });

    await user.click(within(dialog).getByRole('button', { name: 'Keep mine' }));
    await waitFor(() => {
      expect(api.calls('PUT', `/api/posts/${POST_ID}`).map((call) => call.ifMatch)).toEqual(['"1"', '"2"']);
    });
    await waitFor(() => {
      expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
    });
    expect(api.post(POST_ID)?.displayText).toBe(`${DISPLAY_TEXT}\n\nOne more line.`);
    expect(api.calls('PUT', `/api/posts/${POST_ID}`)[1]?.body).toMatchObject({
      displayText: `${DISPLAY_TEXT}\n\nOne more line.`,
      hashtags: ['IdentityModernization', 'Entra'],
    });
    expect(success).toHaveBeenCalledWith('Post saved.', {});
  });

  it('approves with If-Match and shows the SHA-256 the approval is bound to', async () => {
    vi.spyOn(toast, 'success').mockReturnValue(1);
    const { user, api } = openPost();
    await textBox();
    const approve = screen.getByRole('button', { name: 'Approve' });
    await waitFor(() => {
      expect(approve).toBeEnabled();
    });
    await user.click(approve);
    expect(await screen.findByText(/^Approved /)).toBeInTheDocument();
    expect(api.calls('POST', `/api/posts/${POST_ID}/approve`)).toEqual([
      expect.objectContaining({ ifMatch: '"1"', body: {} }),
    ]);
    expect(screen.getByText('9b74c989…c5de')).toBeInTheDocument();
    const goLive = screen.getByRole('link', { name: 'Open go-live' });
    expect(goLive).toHaveAttribute('href', `/studio/${POST_ID}/go-live?post=${POST_ID}`);
    await waitFor(() => {
      expect(goLive).toHaveFocus();
    });
    expect(screen.getByRole('button', { name: 'Withdraw approval' })).toBeInTheDocument();
  });

  it('explains a refused approval in place and runs the checks from there', async () => {
    const { user, api } = openPost();
    server.use(
      http.post(apiUrl('/api/posts/:postId/approve'), () => problemWith(409, 'checks_failing', { failing: ['derived_claims'] })),
    );
    await textBox();
    const approve = screen.getByRole('button', { name: 'Approve' });
    await waitFor(() => {
      expect(approve).toBeEnabled();
    });
    await user.click(approve);
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Some checks fail or are out of date.');
    expect(alert).toHaveTextContent('Run them on the current text: Claims match the sources.');
    expect(screen.getByText('Out of date: run again')).toBeInTheDocument();
    await user.click(within(alert).getByRole('button', { name: 'Run the checks' }));
    expect(await screen.findByRole('heading', { name: 'Running the checks' })).toBeInTheDocument();
    expect(api.calls('POST', `/api/posts/${POST_ID}/check`)).toHaveLength(1);
  });

  it('keeps a live post read-only and says where to change it', async () => {
    openPost(makePost({ lane: 'native', articleId: null, status: 'posted' }));
    const text = await textBox();
    expect(text).toHaveAttribute('readonly');
    expect(screen.getByText('It is live on LinkedIn. Edit the live post on the go-live page.')).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: /Hook 1:/ })).toBeDisabled();
  });

  it('asks the writer for a rewrite with an instruction', async () => {
    const { user, api } = openPost();
    await textBox();
    await user.click(screen.getByRole('button', { name: 'Write new hooks' }));
    const dialog = await screen.findByRole('dialog', { name: 'Rewrite with the writer' });
    await user.type(within(dialog).getByRole('textbox', { name: 'Instruction' }), 'Lead with the outage count');
    await user.click(within(dialog).getByRole('button', { name: 'Rewrite' }));
    expect(await screen.findByRole('heading', { name: 'Rewriting' })).toBeInTheDocument();
    expect(api.calls('POST', `/api/posts/${POST_ID}/regenerate`)).toEqual([
      expect.objectContaining({ ifMatch: '"1"', body: { what: 'hooks', instruction: 'Lead with the outage count' } }),
    ]);
  });
});

describe('the composer on a phone and its edges', () => {
  it('approves on a phone, one-handed', async () => {
    const api = postsServer([NATIVE]);
    server.use(...api.handlers);
    const { user } = renderTab(<PostsTab pieceId={POST_ID} />, `/studio/${POST_ID}/posts`, { viewport: 'mobile' });
    await textBox();
    const approve = screen.getByRole('button', { name: 'Approve' });
    expect(approve).toHaveClass('w-full');
    await waitFor(() => {
      expect(approve).toBeEnabled();
    });
    await user.click(approve);
    expect(await screen.findByRole('link', { name: 'Open go-live' })).toBeInTheDocument();
    expect(api.calls('POST', `/api/posts/${POST_ID}/approve`)).toHaveLength(1);
  });

  it('lets you pick the same hook again after editing the opening', async () => {
    const { user } = openPost();
    const text = await textBox();
    fireEvent.change(text, { target: { value: `My own opening.\n\n${DISPLAY_TEXT.split('\n\n').slice(1).join('\n\n')}` } });
    expect(screen.getByText(/You edited the opening, so it matches none of these/)).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: /Hook 1:/ })).not.toBeChecked();
    await user.click(screen.getByRole('radio', { name: /Hook 1:/ }));
    expect(text).toHaveValue(DISPLAY_TEXT);
  });

  it('shows a refused save in place, with what to fix', async () => {
    const { user } = openPost();
    server.use(
      http.put(apiUrl('/api/posts/:postId'), () =>
        problemWith(422, 'hashtag_not_approved', { hashtags: ['Other'] }, 'Use the pillar’s approved hashtags only.'),
      ),
    );
    const text = await textBox();
    fireEvent.change(text, { target: { value: `${DISPLAY_TEXT} more` } });
    await user.click(screen.getByRole('button', { name: 'Save changes' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Use the pillar’s approved hashtags only.');
    expect(screen.getByRole('button', { name: 'Save changes' })).toBeEnabled();
  });

  it('jumps from a slide finding to its slide', async () => {
    const finding = { code: 'carousel-slide-words', severity: 'block' as const, message: 'Too many words.', overridable: false, field: 'slides[3]' };
    const { user } = openPost(
      makePost({
        lane: 'native',
        articleId: null,
        format: 'carousel',
        carouselSpec: CAROUSEL_SPEC,
        media: CAROUSEL_MEDIA,
        status: 'posted',
        lint: { ranAt: '2026-11-27T13:00:00.000000Z', contentSha256: 'a', passed: false, findings: [finding], truncated: 0 },
      }),
    );
    await screen.findByRole('heading', { level: 2, name: 'Slides' });
    await user.click(screen.getByRole('button', { name: 'Go to slide 4' }));
    expect(screen.getByRole('listitem', { name: 'Slide 4: Steps' })).toHaveFocus();
  });
});

describe('the image card', () => {
  it('has one slide, no moving, and renders with your brand kit', async () => {
    const { user, api } = openPost(
      makePost({
        lane: 'native',
        articleId: null,
        format: 'image_card',
        carouselSpec: { title: 'Card', lang: 'en', slides: [{ type: 'statement', statement: 'Inventory first.' }] },
      }),
    );
    await screen.findByRole('heading', { level: 2, name: 'Image card' });
    expect(screen.getByRole('list', { name: 'The card' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Move slide/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Add a slide' })).not.toBeInTheDocument();
    expect(screen.getByText('Not rendered yet. Approval covers the exact files, so render them once the slides are final.')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Render the card' }));
    expect(await screen.findByRole('heading', { name: 'Rendering the card' })).toBeInTheDocument();
    expect(api.calls('POST', `/api/posts/${POST_ID}/render`)).toHaveLength(1);
    expect(screen.getByRole('list', { name: 'Before you can approve' })).toHaveTextContent('Render the slides first.');
  });
});

describe('the carousel builder', () => {
  const carousel = makePost({
    lane: 'native',
    articleId: null,
    format: 'carousel',
    carouselSpec: CAROUSEL_SPEC,
    documentTitle: '2,000 apps off Okta: the wave plan',
    media: CAROUSEL_MEDIA,
  });

  it('edits a slide and saves the slides, and previews the saved deck in a sandbox', async () => {
    const { user, api } = openPost(carousel);
    await screen.findByRole('heading', { level: 2, name: 'Slides' });
    const slides = screen.getByRole('list', { name: 'Slides (9)' });
    expect(within(slides).getAllByRole('listitem', { name: /^Slide \d+:/ })).toHaveLength(9);

    const frame = screen.getByTitle(/Preview of the saved slides/);
    expect(frame.tagName).toBe('IFRAME');
    expect(frame).toHaveAttribute('sandbox', '');
    expect(frame).toHaveAttribute('src', `/api/posts/${POST_ID}/carousel/preview`);
    expect(screen.getByRole('img', { name: 'Page 1: 2,000 apps, zero outages' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Download the PDF' })).toHaveAttribute('download', 'carousel.pdf');

    const second = within(slides).getByRole('listitem', { name: 'Slide 2: Statement' });
    const statement = within(second).getByRole('textbox', { name: /Statement/ });
    fireEvent.change(statement, { target: { value: 'Inventory first. The rest follows.' } });
    expect(screen.getByText('This shows the saved slides. Save to see your changes here.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Render again' })).toBeDisabled();

    await user.click(screen.getByRole('button', { name: 'Save changes' }));
    await waitFor(() => {
      expect(api.calls('PUT', `/api/posts/${POST_ID}/carousel`)).toHaveLength(1);
    });
    const sent = api.calls('PUT', `/api/posts/${POST_ID}/carousel`)[0];
    expect(sent?.ifMatch).toBe('"1"');
    const body = sent?.body as { spec: { title: string; slides: unknown[] } } | undefined;
    expect(body?.spec.title).toBe(CAROUSEL_SPEC.title);
    expect(body?.spec.slides).toContainEqual({ type: 'statement', statement: 'Inventory first. The rest follows.' });
    expect(api.calls('PUT', `/api/posts/${POST_ID}`)).toHaveLength(0);
  });

  it('moves a slide with the keyboard-reachable buttons and keeps focus on it', async () => {
    const { user } = openPost(carousel);
    await screen.findByRole('heading', { level: 2, name: 'Slides' });
    const down = screen.getByRole('button', { name: 'Move slide 2 down' });
    await user.click(down);
    const slides = screen.getByRole('list', { name: 'Slides (9)' });
    expect(within(slides).getByRole('listitem', { name: 'Slide 3: Statement' })).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Move slide 3 down' })).toHaveFocus();
    });
  });

  it('starts a deck for a carousel without slides', async () => {
    const { user } = openPost(makePost({ lane: 'native', articleId: null, format: 'carousel' }));
    await screen.findByRole('heading', { level: 2, name: 'Slides' });
    await user.click(screen.getByRole('button', { name: 'Start from a blank deck' }));
    const slides = screen.getByRole('list', { name: 'Slides (8)' });
    expect(within(slides).getByRole('listitem', { name: 'Slide 1: Cover' })).toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: /Hook/ })).toHaveValue(
      'We moved 2,000 apps off Okta in 14 months without',
    );
  });
});

describe('the posts of an article', () => {
  it('offers to draft posts once the article is approved or published', async () => {
    const api = postsServer([]);
    server.use(...api.handlers);
    const { user } = renderTab(<PostsTab pieceId={ARTICLE_ID} />, `/studio/${ARTICLE_ID}/posts`);
    const empty = await screen.findByRole('heading', { level: 2, name: 'No posts yet.' });
    const section = empty.closest('section');
    if (!section) throw new Error('the empty state is not a section');
    expect(within(section).getByText('Your part')).toBeInTheDocument();
    await user.click(await within(section).findByRole('button', { name: 'Draft posts from this article' }));
    expect(await screen.findByRole('heading', { name: 'Drafting the posts' })).toBeInTheDocument();
    expect(api.calls('POST', `/api/articles/${ARTICLE_ID}/posts/derive`)).toHaveLength(1);
  });

  it('sends you to the draft while the article is not approved', async () => {
    const api = postsServer([], { article: { status: 'in_review' } });
    server.use(...api.handlers);
    renderTab(<PostsTab pieceId={ARTICLE_ID} />, `/studio/${ARTICLE_ID}/posts`);
    expect(await screen.findByText(/Posts come from the article once it is approved/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Open the draft' })).toHaveAttribute('href', `/studio/${ARTICLE_ID}/draft`);
  });

  it('lists the posts and opens the one you pick', async () => {
    const second = makePost({ id: SECOND_POST_ID, displayText: 'A second post about risk tiers.\n\nBody.', hookVariants: [] });
    const api = postsServer([makePost(), second]);
    server.use(...api.handlers);
    const { user, router } = renderTab(<PostsTab pieceId={ARTICLE_ID} />, `/studio/${ARTICLE_ID}/posts`);
    const nav = await screen.findByRole('navigation', { name: 'Posts from this article' });
    const links = within(nav).getAllByRole('link');
    expect(links).toHaveLength(2);
    expect(links[0]).toHaveAttribute('aria-current', 'page');
    await user.click(within(nav).getByRole('link', { name: /A second post about risk tiers/ }));
    await waitFor(() => {
      expect(router.state.location.search).toBe(`?post=${SECOND_POST_ID}`);
    });
    expect(await screen.findByDisplayValue(/A second post about risk tiers/)).toBeInTheDocument();
  });

  it('asks before leaving unsaved changes for another post', async () => {
    const second = makePost({ id: SECOND_POST_ID, displayText: 'A second post.\n\nBody.' });
    const api = postsServer([makePost(), second]);
    server.use(...api.handlers);
    const { user, router } = renderTab(<PostsTab pieceId={ARTICLE_ID} />, `/studio/${ARTICLE_ID}/posts`);
    const text = await textBox();
    fireEvent.change(text, { target: { value: 'Changed.' } });
    const nav = screen.getByRole('navigation', { name: 'Posts from this article' });
    await user.click(within(nav).getByRole('link', { name: /A second post/ }));
    const dialog = await screen.findByRole('alertdialog', { name: 'Leave without saving?' });
    await user.click(within(dialog).getByRole('button', { name: 'Stay' }));
    expect(router.state.location.search).toBe('');
    expect(text).toHaveValue('Changed.');
  });
});

describe('a post that went out', () => {
  it('turns a derivative into a new idea from its menu (it has no header of its own)', async () => {
    const sent: unknown[] = [];
    const api = postsServer([makePost({ status: 'posted', postedAt: T0 })]);
    server.use(
      ...api.handlers,
      http.post(apiUrl('/api/ideas/repurpose'), async ({ request }) => {
        sent.push(await request.json());
        return HttpResponse.json(
          {
            item: {
              id: 'idea-repurpose-9',
              schemaVersion: 1,
              createdAt: T0,
              updatedAt: T0,
              status: 'new',
              statusChangedAt: T0,
              title: 'Re-cut: what broke first',
              origin: 'repurpose',
              repurposeOf: POST_ID,
            },
            etag: '"idea-etag"',
          },
          { status: 201 },
        );
      }),
    );
    const success = vi.spyOn(toast, 'success').mockReturnValue(1);
    const { user, router } = renderTab(<PostsTab pieceId={ARTICLE_ID} />, `/studio/${ARTICLE_ID}/posts`);
    await textBox();
    await user.click(screen.getByRole('button', { name: 'More actions for this post' }));
    await user.click(await screen.findByRole('menuitem', { name: 'Repurpose as a new post' }));
    await waitFor(() => {
      expect(sent).toEqual([{ postId: POST_ID, kind: 'recut' }]);
    });
    await waitFor(() => {
      expect(success).toHaveBeenCalledWith('A new angle on this post is in your ideas.', expect.anything());
    });
    // "Open it" shows the idea in the inbox, never the take sheet (no `take`).
    const options = success.mock.calls[0]?.[1] as { action?: { label: string; onClick: () => void } } | undefined;
    expect(options?.action?.label).toBe('Open it');
    act(() => {
      options?.action?.onClick();
    });
    await waitFor(() => {
      expect(`${router.state.location.pathname}${router.state.location.search}`).toBe('/calendar/ideas?idea=idea-repurpose-9');
    });
  });

  it('offers no repurpose before the post went out', async () => {
    const { user } = openPost(makePost());
    await textBox();
    await user.click(screen.getByRole('button', { name: 'More actions for this post' }));
    expect(await screen.findByRole('menuitem', { name: 'Run the checks' })).toBeInTheDocument();
    expect(screen.queryByRole('menuitem', { name: 'Repurpose as a new post' })).not.toBeInTheDocument();
    expect(screen.queryByRole('menuitem', { name: 'Turn it into an article idea' })).not.toBeInTheDocument();
  });
});

describe('in the app', () => {
  it('opens the composer for a standalone post inside the Studio', async () => {
    const api = postsServer([NATIVE]);
    server.use(...api.handlers);
    renderApp(`/studio/${POST_ID}/posts`);
    await findPageHeading(/./);
    expect(await screen.findByRole('heading', { level: 2, name: 'Hook and text' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Posts & carousel' })).toHaveAttribute('aria-selected', 'true');
  });
});
