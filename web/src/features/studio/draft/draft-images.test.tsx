import { act, screen, waitFor, within } from '@testing-library/react';
import type { UserEvent } from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { toast } from 'sonner';
import { afterEach, beforeAll, describe, expect, it } from 'vitest';

import { MockEventSource } from '@/test/event-source';
import { jobEventPayload } from '@/test/job-events';
import { apiUrl, server } from '@/test/msw';

import { DraftTab } from './draft-tab';
import type { Article } from './model/types';
import {
  ARTICLE_ID,
  articleFixture,
  ILLUSTRATION_JOB_ID,
  illustrationFixture,
  mediaFixture,
  modelsViewFixture,
  titleCardMedia,
} from './testing/fixtures';
import { createDraftServer, jobView, problemResponse } from './testing/handlers';
import { findArticleEditor, installEditorLayout, renderDraft } from './testing/environment';

beforeAll(() => {
  installEditorLayout();
});

afterEach(() => {
  // Sonner replays toasts that are still showing to the next test's toaster.
  toast.dismiss();
});

const ONE = illustrationFixture('1');
const TWO = illustrationFixture('2');
const THREE = illustrationFixture('3');

function setup(overrides: Partial<Article> = {}) {
  const draft = createDraftServer(articleFixture({ media: titleCardMedia(), ...overrides }));
  server.use(...draft.handlers);
  return draft;
}

function withCandidates(overrides: Partial<Article> = {}) {
  return setup({ illustrations: [ONE, TWO, THREE], ...overrides });
}

/** Renders the Draft tab, waits for the text, then opens the margin's Images tab. */
async function openImages() {
  const view = renderDraft(<DraftTab pieceId={ARTICLE_ID} />);
  const editor = await findArticleEditor();
  await waitFor(() => {
    expect(editor.getText()).toContain('Why agents need identity');
  });
  await view.user.click(screen.getByRole('tab', { name: 'Images' }));
  const panel = await screen.findByRole('region', { name: 'Images' });
  return { ...view, editor, panel };
}

function candidate(name: string): HTMLElement {
  return screen.getByRole('article', { name });
}

function heroRegion(): HTMLElement {
  return screen.getByRole('region', { name: 'Hero image' });
}

function writesTo(draft: ReturnType<typeof setup>, path: string) {
  return draft.writes.filter((write) => write.path === path);
}

/** Picks an option of a Radix select from the keyboard (jsdom has no layout for pointer picks). */
async function choose(user: UserEvent, trigger: HTMLElement, option: string) {
  trigger.focus();
  await user.keyboard('{Enter}');
  const listbox = await screen.findByRole('listbox');
  const names = within(listbox)
    .getAllByRole('option')
    .map((item) => item.textContent);
  const selected = names.findIndex((name) => name === trigger.textContent);
  const index = names.indexOf(option);
  if (index < 0) throw new Error(`no option ${option} in ${names.join(', ')}`);
  const moves = index - Math.max(selected, 0);
  await user.keyboard(`${(moves >= 0 ? '{ArrowDown}' : '{ArrowUp}').repeat(Math.abs(moves))}{Enter}`);
  await waitFor(() => {
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  });
}

describe('Draft > Images: the candidates', () => {
  it('lists the candidates with their cost and alt text, newest first', async () => {
    withCandidates();
    await openImages();
    const list = screen.getByRole('list', { name: 'Candidates (3)' });
    const cards = within(list).getAllByRole('article');
    expect(cards.map((card) => within(card).getByRole('heading').textContent)).toEqual([
      'A key handed from a person to an agent',
      'Every tool behind its own lock',
      'An inventory of the agents you run',
    ]);
    const hero = candidate('A key handed from a person to an agent');
    const image = within(hero).getByRole('img', { name: ONE.altText });
    expect(image).toHaveAttribute('src', `/api/media/articles/${ARTICLE_ID}/${'1'.repeat(64)}.png`);
    expect(within(hero).getByText('Planned as the hero')).toBeInTheDocument();
    expect(within(hero).getByText('Cost $0.27')).toBeInTheDocument();
    expect(within(hero).getByText(ONE.altText, { selector: 'p' })).toBeInTheDocument();
    expect(within(candidate('Every tool behind its own lock')).getByText('Planned for the text')).toBeInTheDocument();
  });

  it('uses a candidate as the hero, then goes back to the title card', async () => {
    const draft = withCandidates();
    const { user } = await openImages();
    expect(within(heroRegion()).getByRole('img', { name: 'The title card: Agent identity in practice' })).toBeInTheDocument();

    await user.click(within(candidate('A key handed from a person to an agent')).getByRole('button', { name: 'Use as hero' }));
    expect(await screen.findByText('Hero image set. The published page opens with it.')).toBeInTheDocument();
    expect(writesTo(draft, 'hero')[0]?.ifMatch).toBe('"etag-7"');
    expect(within(heroRegion()).getByRole('img', { name: ONE.altText })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Hero image' })).toHaveFocus();
    const chosen = candidate('A key handed from a person to an agent');
    expect(within(chosen).getByText('Hero image')).toBeInTheDocument();
    expect(within(chosen).queryByRole('button', { name: 'Use as hero' })).not.toBeInTheDocument();
    expect(within(chosen).queryByRole('button', { name: 'Discard' })).not.toBeInTheDocument();
    expect(within(chosen).getByText(/Used as the hero, so it can’t be discarded/)).toBeInTheDocument();

    const heroEtag = draft.etag;
    await user.click(within(heroRegion()).getByRole('button', { name: 'Use the title card instead' }));
    expect(await screen.findByText('The title card is the hero again.')).toBeInTheDocument();
    expect(writesTo(draft, 'clear-hero')[0]?.ifMatch).toBe(heroEtag);
    expect(within(heroRegion()).getByRole('img', { name: 'The title card: Agent identity in practice' })).toBeInTheDocument();
    expect(within(candidate('A key handed from a person to an agent')).getByRole('button', { name: 'Use as hero' })).toBeInTheDocument();
  });

  it('inserts a candidate after the heading the plan suggested: a new version in the editor', async () => {
    const draft = withCandidates();
    const { user, editor } = await openImages();
    const card = candidate('Every tool behind its own lock');
    await user.click(within(card).getByRole('button', { name: 'Insert after a heading' }));
    const picker = within(card).getByRole('combobox', { name: 'After this heading' });
    expect(picker).toHaveTextContent('Why agents need identity');
    await user.click(within(card).getByRole('button', { name: 'Insert' }));

    expect(await screen.findByText(/^Inserted after “Why agents need identity”/)).toBeInTheDocument();
    expect(writesTo(draft, 'insert')[0]).toEqual({ path: 'insert', ifMatch: '"etag-7"', body: { heading: 'Why agents need identity' } });
    expect(draft.article.version).toBe(8);
    // The editor shows the new version, and takes edits again once it does.
    await waitFor(() => {
      expect(JSON.stringify(editor.getJSON())).toContain(`/api/media/${TWO.media.name}`);
    });
    await waitFor(() => {
      expect(editor.isEditable).toBe(true);
    });
    expect(within(candidate('Every tool behind its own lock')).getByText('In the draft')).toBeInTheDocument();
    expect(within(candidate('Every tool behind its own lock')).queryByRole('button', { name: 'Insert after a heading' })).not.toBeInTheDocument();
    expect(candidate('Every tool behind its own lock').querySelector('h4')).toHaveFocus();
  });

  it('keeps the editor still until the inserted image is in it', async () => {
    const draft = withCandidates();
    let release: () => void = () => undefined;
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    server.use(
      // Waits, then falls through to the draft server's own handler.
      http.post(apiUrl(`/api/articles/${ARTICLE_ID}/images/:id/insert`), async () => {
        await held;
      }),
    );
    const { user, editor } = await openImages();
    expect(editor.isEditable).toBe(true);
    const card = candidate('An inventory of the agents you run');
    await user.click(within(card).getByRole('button', { name: 'Insert after a heading' }));
    await user.click(within(card).getByRole('button', { name: 'Insert' }));
    expect(await within(card).findByRole('button', { name: 'Inserting…' })).toBeDisabled();
    expect(editor.isEditable).toBe(false);
    expect(screen.getByRole('textbox', { name: 'Article text' })).toHaveAttribute('aria-readonly', 'true');
    release();
    await waitFor(() => {
      expect(JSON.stringify(editor.getJSON())).toContain(`/api/media/${THREE.media.name}`);
    });
    await waitFor(() => {
      expect(editor.isEditable).toBe(true);
    });
    expect(draft.article.version).toBe(8);
  });

  it('inserts after a heading the owner picks, and explains a heading that went away', async () => {
    const draft = withCandidates();
    const { user } = await openImages();
    const card = candidate('A key handed from a person to an agent');
    await user.click(within(card).getByRole('button', { name: 'Insert after a heading' }));
    const picker = within(card).getByRole('combobox', { name: 'After this heading' });
    expect(picker).toHaveTextContent('Choose a heading');
    await user.click(within(card).getByRole('button', { name: 'Insert' }));
    expect(within(card).getByText('Choose the heading the image goes after.')).toBeInTheDocument();

    server.use(
      http.post(apiUrl(`/api/articles/${ARTICLE_ID}/images/:id/insert`), () =>
        problemResponse(422, 'heading_not_found', { detail: 'No section heading has that text. Pick one from the draft.' }),
      ),
    );
    await choose(user, picker, 'What to do first');
    await user.click(within(card).getByRole('button', { name: 'Insert' }));
    expect(await within(card).findByText('No section heading has that text. Pick one from the draft.')).toBeInTheDocument();
    expect(draft.article.version).toBe(7);

    server.resetHandlers(...draft.handlers);
    await user.click(within(card).getByRole('button', { name: 'Insert' }));
    expect(await screen.findByText(/^Inserted after “What to do first”/)).toBeInTheDocument();
    expect(draft.article.markdown).toContain(`## What to do first\n\n![${ONE.altText}](/api/media/${ONE.media.name})`);
  });

  it('edits the alt text, checking it before it is sent', async () => {
    const draft = withCandidates();
    const { user } = await openImages();
    const card = candidate('An inventory of the agents you run');
    await user.click(within(card).getByRole('button', { name: 'Edit alt text' }));
    const field = within(card).getByRole('textbox', { name: 'Alt text' });
    expect(field).toHaveValue(THREE.altText);
    await user.clear(field);
    await user.click(within(card).getByRole('button', { name: 'Save alt text' }));
    expect(field).toHaveAccessibleDescription(/Describe the image: alt text is required/);
    expect(writesTo(draft, 'alt')).toHaveLength(0);

    server.use(
      http.put(apiUrl(`/api/articles/${ARTICLE_ID}/images/:id/alt`), () =>
        problemResponse(422, 'confidential_text', { findings: [{ field: 'altText', code: 'blocklist', severity: 'block', message: 'Blocked.' }] }),
      ),
    );
    await user.type(field, 'A clipboard for the Acme agents');
    await user.click(within(card).getByRole('button', { name: 'Save alt text' }));
    expect(await within(card).findByText('Part of this matches your blocklist. Rephrase it.')).toBeInTheDocument();

    server.resetHandlers(...draft.handlers);
    await user.clear(field);
    await user.type(field, 'A checklist beside a row of sleeping robots.');
    await user.click(within(card).getByRole('button', { name: 'Save alt text' }));
    expect(await screen.findByText('Alt text saved.')).toBeInTheDocument();
    expect(writesTo(draft, 'alt').at(-1)).toEqual({ path: 'alt', ifMatch: '"etag-7"', body: { altText: 'A checklist beside a row of sleeping robots.' } });
    expect(within(candidate('An inventory of the agents you run')).getByText('A checklist beside a row of sleeping robots.', { selector: 'p' })).toBeInTheDocument();
    expect(within(candidate('An inventory of the agents you run')).getByRole('button', { name: 'Edit alt text' })).toHaveFocus();
  });

  it('discards an unused candidate after asking', async () => {
    const draft = withCandidates();
    const { user } = await openImages();
    await user.click(within(candidate('An inventory of the agents you run')).getByRole('button', { name: 'Discard' }));
    const confirm = await screen.findByRole('alertdialog', { name: 'Discard this illustration?' });
    expect(within(confirm).getByRole('button', { name: 'Keep it' })).toHaveFocus();
    await user.click(within(confirm).getByRole('button', { name: 'Discard' }));
    expect(await screen.findByText('Illustration discarded.')).toBeInTheDocument();
    expect(writesTo(draft, 'discard')[0]?.ifMatch).toBe('"etag-7"');
    await waitFor(() => {
      expect(screen.queryByRole('article', { name: 'An inventory of the agents you run' })).not.toBeInTheDocument();
    });
    expect(screen.getByRole('heading', { name: 'Candidates (2)' })).toHaveFocus();
  });

  it('shows a refused discard in its dialog', async () => {
    withCandidates();
    server.use(
      http.delete(apiUrl(`/api/articles/${ARTICLE_ID}/images/:id`), () =>
        problemResponse(409, 'illustration_in_use', { detail: 'It is the hero image or in the draft. Clear the hero or remove it first.' }),
      ),
    );
    const { user } = await openImages();
    await user.click(within(candidate('An inventory of the agents you run')).getByRole('button', { name: 'Discard' }));
    const confirm = await screen.findByRole('alertdialog', { name: 'Discard this illustration?' });
    await user.click(within(confirm).getByRole('button', { name: 'Discard' }));
    const alert = await within(confirm).findByRole('alert');
    expect(within(alert).getByText('Couldn’t discard.')).toBeInTheDocument();
    expect(within(alert).getByText('It is the hero image or in the draft. Clear the hero or remove it first.')).toBeInTheDocument();
  });

  it('says why a hero was refused', async () => {
    withCandidates();
    server.use(
      http.post(apiUrl(`/api/articles/${ARTICLE_ID}/images/:id/hero`), () =>
        problemResponse(409, 'too_many_media', { detail: 'This article holds too many images already.' }, 'req-hero-1'),
      ),
    );
    const { user } = await openImages();
    await user.click(within(candidate('A key handed from a person to an agent')).getByRole('button', { name: 'Use as hero' }));
    expect(await screen.findByText('Couldn’t set the hero image.')).toBeInTheDocument();
    expect(screen.getByText('This article holds too many images already. Request ID req-hero-1.')).toBeInTheDocument();
    expect(within(heroRegion()).getByRole('img', { name: 'The title card: Agent identity in practice' })).toBeInTheDocument();
  });

  it('sends nothing while the latest edits aren’t saved', async () => {
    const draft = withCandidates();
    server.use(http.put(apiUrl(`/api/articles/${ARTICLE_ID}`), () => problemResponse(422, 'validation_failed', { errors: [] })));
    const { user, editor } = await openImages();
    act(() => {
      editor.chain().focus('end').insertContent(' Unsaved words.').run();
    });
    expect(await screen.findByText('Not saved: some article details aren’t valid')).toBeInTheDocument();
    await user.click(within(candidate('A key handed from a person to an agent')).getByRole('button', { name: 'Use as hero' }));
    expect(await screen.findByText('Couldn’t set the hero image.')).toBeInTheDocument();
    expect(screen.getByText('Your latest changes aren’t saved yet. Resolve the save, then try again.')).toBeInTheDocument();
    expect(writesTo(draft, 'hero')).toHaveLength(0);
  });

  it('opens the conflict dialog when the article changed, and Keep mine sends the change again', async () => {
    const draft = withCandidates();
    const { user } = await openImages();
    // The gates job wrote the article meanwhile: the screen's etag is stale.
    draft.touch();
    const current = draft.etag;
    await user.click(within(candidate('Every tool behind its own lock')).getByRole('button', { name: 'Use as hero' }));
    const dialog = await screen.findByRole('alertdialog', { name: 'This article changed since you opened it' });
    expect(writesTo(draft, 'hero').map((write) => write.ifMatch)).toEqual(['"etag-7"']);
    await user.click(within(dialog).getByRole('button', { name: 'Keep mine' }));
    expect(await screen.findByText('Hero image set. The published page opens with it.')).toBeInTheDocument();
    expect(writesTo(draft, 'hero').map((write) => write.ifMatch)).toEqual(['"etag-7"', current]);
    await waitFor(() => {
      expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
    });
    expect(within(heroRegion()).getByRole('img', { name: TWO.altText })).toBeInTheDocument();
  });

  it('takes theirs: reloads the article and sends nothing again', async () => {
    const draft = withCandidates();
    const { user } = await openImages();
    draft.touch({ illustrations: [ONE, TWO] });
    await user.click(within(candidate('An inventory of the agents you run')).getByRole('button', { name: 'Use as hero' }));
    const dialog = await screen.findByRole('alertdialog', { name: 'This article changed since you opened it' });
    await user.click(within(dialog).getByRole('button', { name: 'Take theirs' }));
    await waitFor(() => {
      expect(screen.queryByRole('article', { name: 'An inventory of the agents you run' })).not.toBeInTheDocument();
    });
    expect(writesTo(draft, 'hero')).toHaveLength(1);
  });

  it('keeps the images as they are while the article is approved', async () => {
    withCandidates({ status: 'approved', publish: { live: false, auto: false } });
    await openImages();
    expect(
      screen.getByText('Approved: your approval covers the text and its images. Pull it back to review to change them.'),
    ).toBeInTheDocument();
    const card = candidate('A key handed from a person to an agent');
    expect(within(card).queryByRole('button', { name: 'Use as hero' })).not.toBeInTheDocument();
    expect(within(card).queryByRole('button', { name: 'Insert after a heading' })).not.toBeInTheDocument();
    expect(within(card).queryByRole('button', { name: 'Edit alt text' })).not.toBeInTheDocument();
    expect(await screen.findByRole('button', { name: 'Make illustrations' })).toBeDisabled();
  });
});

describe('Draft > Images: making illustrations', () => {
  it('starts a run with the count and a direction, follows it live, then shows the new candidates', async () => {
    const draft = setup();
    const { user } = await openImages();
    const form = screen.getByRole('form', { name: 'Make illustrations' });
    expect(await within(form).findByText('About $0.25, a little more if an image needs a second try.')).toBeInTheDocument();
    expect(screen.getByText(/None yet\. Only images that pass their checks/)).toBeInTheDocument();
    await user.click(within(form).getByRole('radio', { name: '3' }));
    expect(within(form).getByText('About $0.59, a little more if an image needs a second try.')).toBeInTheDocument();
    await user.type(within(form).getByRole('textbox', { name: 'Art direction (optional)' }), '  Warmer   light ');
    await user.click(within(form).getByRole('button', { name: 'Make illustrations' }));

    expect(await screen.findByText('Making illustrations. They show here once they pass their checks.')).toBeInTheDocument();
    expect(writesTo(draft, 'images')[0]).toEqual({ path: 'images', ifMatch: '"etag-7"', body: { count: 3, direction: 'Warmer light' } });
    const jobId = draft.article.illustrationJobId ?? '';
    const progress = await screen.findByRole('region', { name: 'Making illustrations' });
    expect(within(progress).getByText('Queued. It starts in a moment.')).toBeInTheDocument();
    expect(within(form).getByRole('button', { name: 'Make illustrations' })).toBeDisabled();

    const stream = MockEventSource.latest();
    act(() => {
      stream.open();
      stream.emit('job', jobEventPayload(2, 'step.started', { jobId, step: 'plan' }), `${jobId}:2`);
    });
    expect(await within(progress).findByText(/^Planning the images/)).toBeInTheDocument();
    act(() => {
      stream.emit('job', jobEventPayload(3, 'step.succeeded', { jobId, step: 'plan' }), `${jobId}:3`);
      stream.emit('job', jobEventPayload(4, 'step.started', { jobId, step: 'generate' }), `${jobId}:4`);
      stream.emit('job', jobEventPayload(5, 'progress', { jobId, step: 'generate', data: { done: 1, total: 3, label: 'images made' } }), `${jobId}:5`);
    });
    const made = await within(progress).findByRole('progressbar', { name: 'Images made' });
    expect(made).toHaveAttribute('aria-valuetext', '1 of 3');
    expect(within(progress).getByText('1 of 3 made')).toBeInTheDocument();
    expect(within(progress).getAllByRole('listitem').map((item) => item.textContent)).toEqual([
      'Plan the images: done',
      'Make them: in progress',
      'Check each one against the article: not started',
      'Keep the ones that pass: not started',
      'Add them here: not started',
    ]);

    // Checking counts only the images whose check finished.
    act(() => {
      stream.emit('job', jobEventPayload(6, 'progress', { jobId, step: 'generate', data: { done: 3, total: 3, label: 'images made' } }), `${jobId}:6`);
      stream.emit('job', jobEventPayload(7, 'step.succeeded', { jobId, step: 'generate' }), `${jobId}:7`);
      stream.emit('job', jobEventPayload(8, 'step.started', { jobId, step: 'check' }), `${jobId}:8`);
      stream.emit('job', jobEventPayload(9, 'progress', { jobId, step: 'check', data: { done: 0, total: 3, label: 'images checked' } }), `${jobId}:9`);
    });
    const checked = await within(progress).findByRole('progressbar', { name: 'Images checked' });
    expect(checked).toHaveAttribute('aria-valuetext', '0 of 3');
    expect(within(progress).queryByRole('progressbar', { name: 'Images made' })).not.toBeInTheDocument();
    expect(within(progress).getByText(/^Checking that each image shows the idea/)).toBeInTheDocument();
    act(() => {
      stream.emit('job', jobEventPayload(10, 'progress', { jobId, step: 'check', data: { done: 2, total: 3, label: 'images checked' } }), `${jobId}:10`);
    });
    await waitFor(() => {
      expect(within(progress).getByRole('progressbar', { name: 'Images checked' })).toHaveAttribute('aria-valuetext', '2 of 3');
    });
    expect(within(progress).getByText('2 of 3 checked')).toBeInTheDocument();

    // The job attaches the candidates and ends.
    draft.touch({ illustrations: [ONE, TWO, THREE] });
    draft.jobs.set(jobId, jobView(jobId, { status: 'succeeded', seq: 20 }));
    act(() => {
      stream.emit('job', jobEventPayload(19, 'entity.changed', { jobId, step: 'attach', data: { entity: 'article', id: ARTICLE_ID } }), `${jobId}:19`);
      stream.emit('job', jobEventPayload(20, 'status', { jobId, status: 'succeeded', data: { status: 'succeeded' } }), `${jobId}:20`);
    });
    expect(await screen.findByText('The illustrations are ready.')).toBeInTheDocument();
    expect(await screen.findByRole('list', { name: 'Candidates (3)' })).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.queryByRole('region', { name: 'Making illustrations' })).not.toBeInTheDocument();
    });
    expect(within(form).getByRole('button', { name: 'Make illustrations' })).toBeEnabled();
  });

  it('says when a run ends while another margin tab is open, and shows the candidates on request', async () => {
    const draft = setup();
    const { user } = await openImages();
    await user.click(await screen.findByRole('button', { name: 'Make illustrations' }));
    await screen.findByRole('region', { name: 'Making illustrations' });
    const jobId = draft.article.illustrationJobId ?? '';
    await user.click(screen.getByRole('tab', { name: 'Checks' }));
    expect(screen.queryByRole('region', { name: 'Images' })).not.toBeInTheDocument();

    draft.touch({ illustrations: [ONE] });
    draft.jobs.set(jobId, jobView(jobId, { status: 'succeeded', seq: 9 }));
    const stream = MockEventSource.latest();
    act(() => {
      stream.open();
      stream.emit('job', jobEventPayload(9, 'status', { jobId, status: 'succeeded', data: { status: 'succeeded' } }), `${jobId}:9`);
    });
    expect(await screen.findByText('The illustrations are ready.')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Show them' }));
    expect(screen.getByRole('tab', { name: 'Images' })).toHaveAttribute('aria-selected', 'true');
    expect(await screen.findByRole('article', { name: 'A key handed from a person to an agent' })).toBeInTheDocument();
  });

  it('says how many images a run kept when some failed their checks', async () => {
    const draft = setup();
    const { user } = await openImages();
    await user.click(await screen.findByRole('button', { name: 'Make illustrations' }));
    await screen.findByRole('region', { name: 'Making illustrations' });
    const jobId = draft.article.illustrationJobId ?? '';
    const kept = {
      seq: 18,
      at: '2026-11-26T16:04:00.000000Z',
      type: 'progress',
      step: 'attach',
      data: { done: 2, total: 3, label: 'images kept', reasons: ['legible_text', 'content_filter'] },
    };
    draft.touch({ illustrations: [ONE, TWO] });
    draft.jobs.set(jobId, jobView(jobId, { status: 'succeeded', seq: 20, events: [kept] }));
    const stream = MockEventSource.latest();
    act(() => {
      stream.open();
      stream.emit('job', jobEventPayload(18, 'progress', { jobId, step: 'attach', data: kept.data }), `${jobId}:18`);
      stream.emit('job', jobEventPayload(20, 'status', { jobId, status: 'succeeded', data: { status: 'succeeded' } }), `${jobId}:20`);
    });
    const sentence = 'Kept 2 of 3: one didn’t pass the checks (text in the image, the image model’s safety filter).';
    expect(await screen.findByText('The illustrations are ready.')).toBeInTheDocument();
    expect(screen.getByText(`${sentence} Use one as the hero or place it in the text, under Images.`)).toBeInTheDocument();
    expect(await screen.findByText(`The last run: ${sentence}`)).toBeInTheDocument();
    expect(await screen.findByRole('list', { name: 'Candidates (2)' })).toBeInTheDocument();
  });

  it('says when a run it followed stopped, and why', async () => {
    const draft = setup();
    const { user } = await openImages();
    await user.click(await screen.findByRole('button', { name: 'Make illustrations' }));
    await screen.findByRole('region', { name: 'Making illustrations' });
    const jobId = draft.article.illustrationJobId ?? '';
    const kept = { done: 0, total: 1, label: 'images kept', reasons: ['legible_text', 'legible_text', 'person'] };
    draft.jobs.set(
      jobId,
      jobView(jobId, {
        status: 'failed',
        seq: 9,
        error: { code: 'no_illustrations', step: 'attach', detail: 'No illustration passed its checks. Try another direction.', at: '2026-11-26T16:05:00.000000Z' },
        events: [{ seq: 8, at: '2026-11-26T16:04:00.000000Z', type: 'progress', step: 'attach', data: kept }],
      }),
    );
    const stream = MockEventSource.latest();
    act(() => {
      stream.open();
      stream.emit('job', jobEventPayload(8, 'progress', { jobId, step: 'attach', data: kept }), `${jobId}:8`);
      stream.emit('job', jobEventPayload(9, 'status', { jobId, status: 'failed', data: { status: 'failed' } }), `${jobId}:9`);
    });
    expect(await screen.findByText('No new illustrations.')).toBeInTheDocument();
    expect(
      await screen.findByText(/stopped: No illustration passed its checks\. Try another direction\. Why: text in the image, a recognisable person\.$/),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Make illustrations' })).toBeEnabled();
  });

  it('says how a run ended without candidates', async () => {
    const draft = setup({ illustrationJobId: ILLUSTRATION_JOB_ID });
    draft.jobs.set(
      ILLUSTRATION_JOB_ID,
      jobView(ILLUSTRATION_JOB_ID, {
        status: 'failed',
        error: { code: 'no_illustrations', step: 'attach', detail: 'No illustration passed its checks. Try another direction.', at: '2026-11-26T16:05:00.000000Z' },
      }),
    );
    await openImages();
    const alert = await screen.findByText(/stopped: No illustration passed its checks\. Try another direction\.$/);
    expect(alert.closest('[data-slot="alert"]')).toHaveTextContent('No new illustrations');
  });

  it('follows a run another tab started', async () => {
    const draft = setup();
    server.use(
      http.post(apiUrl(`/api/articles/${ARTICLE_ID}/images`), () => {
        draft.jobs.set('illustrations-other', jobView('illustrations-other', { status: 'running' }));
        draft.touch({ illustrationJobId: 'illustrations-other' });
        return problemResponse(409, 'images_in_progress', {
          detail: 'Illustrations are being made for this article. Wait for them.',
          jobId: 'illustrations-other',
        });
      }),
    );
    const { user } = await openImages();
    await user.click(await screen.findByRole('button', { name: 'Make illustrations' }));
    expect(await screen.findByText('Illustrations are already being made for this article. Follow them here.')).toBeInTheDocument();
    expect(await screen.findByRole('region', { name: 'Making illustrations' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Make illustrations' })).toBeDisabled();
  });

  it('explains a server without an image model, and reads the settings again', async () => {
    const draft = setup();
    let reads = 0;
    server.use(
      http.get(apiUrl('/api/kb/models'), () => {
        reads += 1;
        return HttpResponse.json(draft.models);
      }),
      http.post(apiUrl(`/api/articles/${ARTICLE_ID}/images`), () => {
        draft.models = modelsViewFixture({ imageRoute: false });
        return problemResponse(503, 'images_unavailable', { detail: 'Illustrations need an image model, and none is configured.' });
      }),
    );
    const { user } = await openImages();
    await user.click(await screen.findByRole('button', { name: 'Make illustrations' }));
    const form = screen.getByRole('form', { name: 'Make illustrations' });
    const alert = await within(form).findByRole('alert');
    expect(within(alert).getByText('Illustrations need an image model, and none is configured.')).toBeInTheDocument();
    expect(await screen.findByText('No image model on this server')).toBeInTheDocument();
    expect(reads).toBe(2);
    expect(within(form).getByRole('button', { name: 'Make illustrations' })).toBeDisabled();
  });

  it('names the missing details when the server refuses for them', async () => {
    setup();
    server.use(
      http.post(apiUrl(`/api/articles/${ARTICLE_ID}/images`), () =>
        problemResponse(409, 'meta_incomplete', { detail: 'Fill in the missing article details first.', missing: ['title'] }),
      ),
    );
    const { user } = await openImages();
    await user.click(await screen.findByRole('button', { name: 'Make illustrations' }));
    const alert = await within(screen.getByRole('form', { name: 'Make illustrations' })).findByRole('alert');
    expect(within(alert).getByText('Missing details: title.')).toBeInTheDocument();
  });

  it('puts a blocklist hit in the direction on its field, and other hits above the button', async () => {
    setup();
    server.use(
      http.post(apiUrl(`/api/articles/${ARTICLE_ID}/images`), () =>
        problemResponse(422, 'confidential_text', {
          findings: [
            { field: 'direction', code: 'blocklist', severity: 'block', message: 'Blocked.' },
            { field: 'headings[1]', code: 'blocklist', severity: 'block', message: 'Blocked.' },
          ],
        }),
      ),
    );
    const { user } = await openImages();
    const form = screen.getByRole('form', { name: 'Make illustrations' });
    const direction = within(form).getByRole('textbox', { name: 'Art direction (optional)' });
    await user.type(direction, 'The Acme logo');
    await user.click(await within(form).findByRole('button', { name: 'Make illustrations' }));
    await waitFor(() => {
      expect(direction).toHaveAccessibleDescription(/Part of this matches your blocklist\. Rephrase it\./);
    });
    expect(within(form).getByText(/Your blocklist matches a section heading\./)).toBeInTheDocument();
  });

  it('shows the server’s refusal with a link to the setting', async () => {
    setup();
    server.use(
      http.post(apiUrl(`/api/articles/${ARTICLE_ID}/images`), () =>
        problemResponse(409, 'images_disabled', { detail: 'Switch illustrations on in Settings > Models first.' }),
      ),
    );
    const { user } = await openImages();
    await user.click(await screen.findByRole('button', { name: 'Make illustrations' }));
    const form = screen.getByRole('form', { name: 'Make illustrations' });
    const alert = await within(form).findByRole('alert');
    expect(within(alert).getByText('Couldn’t start the illustrations.')).toBeInTheDocument();
    expect(within(alert).getByText('Switch illustrations on in Settings > Models first.')).toBeInTheDocument();
    expect(within(alert).getByRole('link', { name: 'Open Settings > Models' })).toHaveAttribute('href', '/settings/models');
  });
});

describe('Draft > Images: when they can’t be made', () => {
  it('says illustrations are off, with a link to the setting', async () => {
    const draft = setup();
    draft.models = modelsViewFixture({ imagesEnabled: false });
    await openImages();
    const region = screen.getByRole('region', { name: 'Make illustrations' });
    expect(await within(region).findByText('Illustrations are off')).toBeInTheDocument();
    expect(within(region).getByRole('link', { name: 'Open Settings > Models' })).toHaveAttribute('href', '/settings/models');
    expect(within(region).getByRole('button', { name: 'Make illustrations' })).toBeDisabled();
    expect(within(region).getByText('Switch illustrations on in Settings > Models first.')).toBeInTheDocument();
  });

  it('says when no image model is set up', async () => {
    const draft = setup({ illustrations: [ONE] });
    draft.models = modelsViewFixture({ imageRoute: false });
    await openImages();
    const region = screen.getByRole('region', { name: 'Make illustrations' });
    expect(await within(region).findByText('No image model on this server')).toBeInTheDocument();
    expect(within(region).getByRole('button', { name: 'Make illustrations' })).toBeDisabled();
    expect(within(region).getByText('No image model is set up on this server.')).toBeInTheDocument();
    // The candidates already made can still be used.
    expect(within(candidate('A key handed from a person to an agent')).getByRole('button', { name: 'Use as hero' })).toBeEnabled();
  });

  it('asks for a draft first while the article is planned', async () => {
    setup({ status: 'planned' });
    await openImages();
    expect(await screen.findByText(/^Draft the article first: the illustrations are planned/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Make illustrations' })).toBeDisabled();
  });

  it('explains a settings read that failed, and tries again', async () => {
    const draft = setup();
    let failures = 1;
    server.use(
      http.get(apiUrl('/api/kb/models'), () => {
        if (failures > 0) {
          failures -= 1;
          return problemResponse(422, 'validation_failed');
        }
        return HttpResponse.json(draft.models);
      }),
    );
    const { user } = await openImages();
    const region = screen.getByRole('region', { name: 'Make illustrations' });
    const alert = await within(region).findByRole('alert');
    expect(within(alert).getByText('Couldn’t check whether illustrations are on.')).toBeInTheDocument();
    await user.click(within(alert).getByRole('button', { name: 'Try again' }));
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Make illustrations' })).toBeEnabled();
    });
  });
});

describe('Draft > Images: the hero in the preview and the approval', () => {
  it('shows a hero too large for the preview frame above it', async () => {
    const big = { ...mediaFixture({ sha256: '1'.repeat(64), sizeBytes: 3_100_000 }), role: 'hero_image' as const, altText: ONE.altText };
    setup({ illustrations: [ONE], media: [...titleCardMedia().slice(0, 1), big] });
    const { user } = await openImages();
    await user.click(within(heroRegion()).getByRole('button', { name: 'Preview as portfolio page' }));
    const dialog = await screen.findByRole('dialog', { name: 'Preview as portfolio page' });
    const figure = within(dialog).getByRole('figure');
    expect(within(figure).getByRole('img', { name: ONE.altText })).toHaveAttribute('src', `/api/media/${big.name}`);
    expect(within(figure).getByText(/It is over 2 MB, so the preview below leaves it out\./)).toBeInTheDocument();
    expect(within(dialog).getByTitle('Portfolio preview of “Agent identity in practice”')).toHaveAttribute('sandbox', '');
  });

  it('leaves a hero the frame inlines to the frame, and notes large images in the text', async () => {
    const small = { ...ONE.media, role: 'hero_image' as const, sizeBytes: 900_000 };
    const large = illustrationFixture('2', { media: { ...TWO.media, sizeBytes: 2_500_000 } });
    setup({
      illustrations: [ONE, large],
      media: [...titleCardMedia().slice(0, 1), small],
      markdown: `## Why agents need identity\n\n![${TWO.altText}](/api/media/${TWO.media.name})\n\n## What to do first\n\nStart small.\n`,
    });
    const { user } = await openImages();
    await user.click(within(heroRegion()).getByRole('button', { name: 'Preview as portfolio page' }));
    const dialog = await screen.findByRole('dialog', { name: 'Preview as portfolio page' });
    expect(within(dialog).queryByRole('figure')).not.toBeInTheDocument();
    expect(within(dialog).getByText(/^1 image in the text is over 2 MB, so the preview shows its alt text\./)).toBeInTheDocument();
  });

  it('lists the images an approval covers', async () => {
    setup({
      illustrations: [ONE],
      media: [...titleCardMedia().slice(0, 1), { ...ONE.media, role: 'hero_image', altText: ONE.altText }],
    });
    const { user } = renderDraft(<DraftTab pieceId={ARTICLE_ID} />);
    await findArticleEditor();
    await user.click(await screen.findByRole('button', { name: 'Approve…' }));
    const dialog = await screen.findByRole('dialog', { name: 'Approve for publishing' });
    const media = within(dialog).getByRole('group', { name: 'Images you approve' });
    expect(within(media).getByText(`Hero image: ${ONE.altText}`)).toBeInTheDocument();
    expect(within(media).getByText('No images in the text.')).toBeInTheDocument();
  });
});
