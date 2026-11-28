import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { toast } from 'sonner';
import { describe, expect, it, vi } from 'vitest';

import { server } from '@/test/msw';
import { findPageHeading, renderApp } from '@/test/render-app';

import { approvedPost, ARTICLE_ID, CAROUSEL_MEDIA, CONFIRM_TOKEN, makePost, POST_ID, SHA256, T0 } from '../posts/testing/fixtures';
import { postsServer, problemWith } from '../posts/testing/handlers';
import { renderTab } from '../posts/testing/render';
import type { Post } from '../posts/model/types';

import { GoLiveTab } from './go-live-tab';
import { goLiveServer, POST_URL, type GoLiveServerOptions } from './testing/handlers';

const NATIVE: Partial<Post> = { lane: 'native', articleId: null };

function open(post: Post, options: GoLiveServerOptions = {}, viewport: 'desktop' | 'mobile' = 'mobile') {
  const api = goLiveServer(post, options);
  server.use(...api.handlers);
  const view = renderTab(<GoLiveTab pieceId={post.id} />, `/studio/${post.id}/go-live?post=${post.id}`, { viewport });
  return { ...view, api };
}

describe('the one-click publish page', () => {
  it('shows the exact final render, asks about replies, and keeps the actions within thumb reach', async () => {
    open(
      approvedPost({ ...NATIVE, format: 'carousel', documentTitle: '2,000 apps off Okta: the wave plan', media: CAROUSEL_MEDIA }),
      {
        view: () => ({
          cover: CAROUSEL_MEDIA[1] ?? null,
          documentTitle: '2,000 apps off Okta: the wave plan',
          altTexts: [null],
        }),
      },
    );
    expect(await screen.findByRole('heading', { level: 2, name: /^Post at 09:00 / })).toBeInTheDocument();
    const proof = screen.getByRole('figure', { name: 'LinkedIn preview, phone width' });
    expect(within(proof).getByRole('separator', { name: /The fold/ })).toBeInTheDocument();
    expect(within(proof).getByRole('img', { name: '2,000 apps, zero outages' })).toBeInTheDocument();
    expect(within(proof).getByText('9 pages')).toBeInTheDocument();
    expect(screen.getByText('Document title')).toBeInTheDocument();
    expect(screen.getByText('9b74c989…c5de')).toBeInTheDocument();
    expect(screen.getByRole('radiogroup', { name: 'Can you reply to comments in the next hour?' })).toBeInTheDocument();

    const bar = screen.getByRole('group', { name: 'Go-live actions' });
    expect(bar).toHaveClass('sticky');
    for (const name of ['Publish now', 'Snooze 30 min', 'Next slot', 'Switch to kit']) {
      expect(within(bar).getByRole('button', { name })).toBeEnabled();
    }
  });

  it('publishes on one fresh tap, with the token and the approval it shows, exactly once', async () => {
    const success = vi.spyOn(toast, 'success').mockReturnValue(1);
    const { user, api } = open(approvedPost(NATIVE));
    const publish = await screen.findByRole('button', { name: 'Publish now' });
    await user.dblClick(publish);
    const posted = await screen.findByRole('heading', { level: 2, name: 'Posted on LinkedIn' });
    await waitFor(() => {
      expect(posted).toHaveFocus();
    });
    expect(api.calls('POST', api.path('/publish'))).toEqual([
      { method: 'POST', path: api.path('/publish'), ifMatch: '"1"', body: { approvalSha256: SHA256, confirmToken: CONFIRM_TOKEN } },
    ]);
    expect(success).toHaveBeenCalledWith('Published on LinkedIn.', {});
    expect(screen.getByRole('link', { name: /Open on LinkedIn/ })).toHaveAttribute('href', POST_URL);
  });

  it('says why LinkedIn refused it and waits for a fresh tap: nothing is retried', async () => {
    const { user, api } = open(approvedPost(NATIVE), { publish: { rejected: 'linkedin_422' } });
    await user.click(await screen.findByRole('button', { name: 'Publish now' }));
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('LinkedIn refused the post.');
    expect(alert).toHaveTextContent('LinkedIn refused the post’s content.');
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Publish now' })).toBeEnabled();
    });
    expect(api.calls('POST', api.path('/publish'))).toHaveLength(1);
  });

  it('after a dropped connection reads the post again instead of posting again', async () => {
    const { user, api } = open(approvedPost(NATIVE), { publish: 'network' });
    await user.click(await screen.findByRole('button', { name: 'Publish now' }));
    expect(await screen.findByText('The connection dropped during the tap.')).toBeInTheDocument();
    await waitFor(() => {
      expect(api.reads.goLive).toBeGreaterThanOrEqual(2);
    });
    expect(await screen.findByText(/The server shows it as not published/)).toBeInTheDocument();
    expect(api.calls('POST', api.path('/publish'))).toHaveLength(1);
  });

  it('turns a stale page into a fresh one when the confirm token has lapsed', async () => {
    const { user, api } = open(approvedPost(NATIVE), { publish: { problem: problemWith(409, 'confirm_invalid') } });
    await user.click(await screen.findByRole('button', { name: 'Publish now' }));
    expect(await screen.findByText('This page was open too long.')).toBeInTheDocument();
    await waitFor(() => {
      expect(api.reads.goLive).toBeGreaterThanOrEqual(2);
    });
    expect(api.calls('POST', api.path('/publish'))).toHaveLength(1);
  });

  it('explains why Publish is off and offers the kit instead', async () => {
    const { user, api } = open(approvedPost(NATIVE), {
      view: () => ({ canPublish: false, reasons: ['reconnect_required'], connection: { status: 'needs_reconnect', tokenDay: 61 } }),
    });
    expect(await screen.findByText('Publish is off for now.')).toBeInTheDocument();
    expect(screen.getByText(/LinkedIn needs you to sign in again/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Publish now' })).toBeDisabled();
    await user.click(screen.getByRole('button', { name: 'Post it yourself with the kit' }));
    expect(await screen.findByRole('heading', { level: 2, name: 'Post it yourself' })).toBeInTheDocument();
    expect(api.calls('POST', api.path('/mode'))).toEqual([expect.objectContaining({ ifMatch: '"1"', body: { mode: 'kit' } })]);
  });

  it('snoozes 30 minutes or takes the next slot, and explains a clash', async () => {
    const success = vi.spyOn(toast, 'success').mockReturnValue(1);
    const { user, api } = open(approvedPost(NATIVE));
    await user.click(await screen.findByRole('button', { name: 'Snooze 30 min' }));
    await waitFor(() => {
      expect(success).toHaveBeenCalledWith(expect.stringMatching(/^Snoozed to 09:30\b/), {});
    });
    expect(api.calls('POST', api.path('/reslot'))).toEqual([
      expect.objectContaining({ ifMatch: '"1"', body: { action: 'snooze' } }),
    ]);
    expect(await screen.findByRole('heading', { level: 2, name: /^Post at 09:30 / })).toBeInTheDocument();
  });

  it('says when the next slot clashes with another post', async () => {
    const { user } = open(approvedPost(NATIVE), {
      reslotProblem: problemWith(409, 'slot_conflict', {}, 'Another post already goes out that day. Posts are never stacked.'),
    });
    await user.click(await screen.findByRole('button', { name: 'Next slot' }));
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Couldn’t move the post.');
    expect(alert).toHaveTextContent('Posts are never stacked.');
  });

  it('suggests waiting when you can’t reply in the next hour', async () => {
    const { user } = open(approvedPost(NATIVE));
    await screen.findByRole('button', { name: 'Publish now' });
    await user.click(screen.getByRole('radio', { name: 'Not in the next hour' }));
    const bar = screen.getByRole('group', { name: 'Go-live actions' });
    expect(within(bar).getByRole('button', { name: 'Publish anyway' })).toBeInTheDocument();
    expect(within(bar).getByRole('button', { name: 'Next slot' })).toHaveClass('w-full');
    expect(screen.getByText(/Then let it wait/)).toBeInTheDocument();
  });
});

describe('needs-attention and other states', () => {
  it('asks you to check LinkedIn after an unanswered attempt and records what you saw', async () => {
    const post = makePost({
      ...NATIVE,
      status: 'needs_attention',
      approval: { sha256: SHA256, approvedAt: T0 },
      lastError: 'timeout',
      intent: { id: 'intent-1', createdAt: T0, approvalSha256: SHA256, status: 'unknown', errorCode: 'timeout' },
    });
    const { user, api } = open(post);
    expect(await screen.findByRole('heading', { level: 2, name: 'Check LinkedIn: did your post go out?' })).toBeInTheDocument();
    expect(screen.getByText(/LinkedIn didn’t answer within 30 seconds/)).toBeInTheDocument();
    expect(await screen.findByRole('link', { name: /Open LinkedIn/ })).toHaveAttribute('target', '_blank');
    const save = screen.getByRole('button', { name: 'Save what I saw' });
    expect(save).toBeDisabled();
    await user.click(screen.getByRole('radio', { name: 'It’s on LinkedIn' }));
    await user.type(screen.getByRole('textbox', { name: 'Post link' }), POST_URL);
    await user.click(save);
    expect(await screen.findByRole('heading', { level: 2, name: 'Posted on LinkedIn' })).toBeInTheDocument();
    expect(api.calls('POST', api.path('/resolve'))).toEqual([
      expect.objectContaining({ ifMatch: '"1"', body: { outcome: 'posted', postUrl: POST_URL } }),
    ]);
  });

  it('offers the next free slot after a missed one', async () => {
    const { user, api } = open(approvedPost({ ...NATIVE, status: 'missed' }));
    expect(await screen.findByRole('heading', { level: 2, name: /^The 09:00\b.* slot passed$/ })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Take the next free slot' }));
    await waitFor(() => {
      expect(api.calls('POST', api.path('/reslot'))).toEqual([expect.objectContaining({ body: { action: 'next_slot' } })]);
    });
  });

  it('has nothing scheduled while the post is a draft', async () => {
    open(makePost(NATIVE));
    const empty = await screen.findByRole('heading', { level: 2, name: 'Nothing scheduled.' });
    const section = empty.closest('section');
    if (!section) throw new Error('the empty state is not a section');
    expect(within(section).getByText('Your part')).toBeInTheDocument();
    expect(within(section).getByRole('link', { name: 'Posts & carousel' })).toHaveAttribute('href', `/studio/${POST_ID}/posts`);
  });

  it('edits the live post on LinkedIn with the page’s token, and shows what the lint refused', async () => {
    const post = approvedPost({
      ...NATIVE,
      status: 'posted',
      postedAt: T0,
      postUrl: POST_URL,
      linkedinUrn: 'urn:li:share:7212345678901234567',
      finalText: 'What went out.',
    });
    const { user, api } = open(post);
    await user.click(await screen.findByRole('button', { name: 'Edit the live post' }));
    const text = await screen.findByRole('textbox', { name: 'Live text' });
    expect(text).toHaveValue('What went out.');
    fireEvent.change(text, { target: { value: 'What went out, see https://example.com' } });
    await user.click(screen.getByRole('button', { name: 'Update on LinkedIn' }));
    expect(await screen.findByText('Remove the link: posts carry no links.')).toBeInTheDocument();
    fireEvent.change(text, { target: { value: 'What went out, corrected.' } });
    await user.click(screen.getByRole('button', { name: 'Update on LinkedIn' }));
    await waitFor(() => {
      expect(api.calls('PATCH', api.path('/linkedin'))).toHaveLength(2);
    });
    expect(api.calls('PATCH', api.path('/linkedin'))[1]).toMatchObject({
      body: { displayText: 'What went out, corrected.', confirmToken: CONFIRM_TOKEN },
    });
  });

  it('asks before deleting a live post from LinkedIn', async () => {
    const post = approvedPost({ ...NATIVE, status: 'posted', postedAt: T0, postUrl: POST_URL, linkedinUrn: 'urn:li:share:7212345678901234567' });
    const { user, api } = open(post);
    await user.click(await screen.findByRole('button', { name: 'Delete from LinkedIn…' }));
    const dialog = await screen.findByRole('alertdialog', { name: 'Delete this post from LinkedIn?' });
    await waitFor(() => {
      expect(within(dialog).getByRole('button', { name: 'Keep it' })).toHaveFocus();
    });
    await user.click(within(dialog).getByRole('button', { name: 'Delete from LinkedIn' }));
    await waitFor(() => {
      expect(api.calls('POST', api.path('/linkedin/delete'))).toEqual([
        expect.objectContaining({ ifMatch: '"1"', body: { confirmToken: CONFIRM_TOKEN } }),
      ]);
    });
    expect(await screen.findByText('Deleted from LinkedIn.')).toBeInTheDocument();
  });
});

describe('the ready-to-post kit', () => {
  const kitPost = approvedPost({
    ...NATIVE,
    status: 'kit',
    goLiveMode: 'kit',
    format: 'carousel',
    documentTitle: '2,000 apps off Okta: the wave plan',
    media: CAROUSEL_MEDIA,
    kit: { state: 'prepared', preparedAt: T0 },
  });
  const options: GoLiveServerOptions = {
    kit: () => ({
      downloads: [
        {
          media: CAROUSEL_MEDIA[0] ?? (undefined as never),
          url: `/api/media/${CAROUSEL_MEDIA[0]?.name ?? ''}`,
          filename: 'carousel.pdf',
        },
      ],
    }),
  };

  it('copies the text exactly, offers the files, the four steps and Open LinkedIn', async () => {
    const { user } = open(kitPost, options);
    expect(await screen.findByRole('heading', { level: 2, name: 'Post it yourself' })).toBeInTheDocument();
    const progress = screen.getByRole('list', { name: 'Kit progress' });
    expect(within(progress).getByText('Prepared').closest('li')).toHaveAttribute('aria-current', 'step');
    await user.click(screen.getByRole('button', { name: 'Copy the post text' }));
    await expect(navigator.clipboard.readText()).resolves.toBe(kitPost.displayText);
    await user.click(screen.getByRole('button', { name: 'Copy the document title' }));
    await expect(navigator.clipboard.readText()).resolves.toBe('2,000 apps off Okta: the wave plan');
    // The bar keeps the main copy within thumb reach.
    const bar = screen.getByRole('group', { name: 'Kit actions' });
    await user.click(within(bar).getByRole('button', { name: 'Copy the text' }));
    await expect(navigator.clipboard.readText()).resolves.toBe(kitPost.displayText);
    expect(screen.getByRole('link', { name: 'Download the PDF' })).toHaveAttribute('download', 'carousel.pdf');
    expect(screen.getAllByRole('checkbox')).toHaveLength(4);
    expect(screen.getAllByRole('link', { name: /Open LinkedIn/ })[0]).toHaveAttribute('href', 'https://www.linkedin.com/feed/');
  });

  it('records a native draft, a native schedule at your time, and the post with its link and final text', async () => {
    const { user, api } = open(kitPost, options);
    await user.click(await screen.findByRole('button', { name: 'I saved it as a draft in LinkedIn' }));
    await waitFor(() => {
      expect(api.calls('POST', api.path('/kit'))).toEqual([expect.objectContaining({ body: { to: 'native_draft_saved' } })]);
    });

    await user.click(await screen.findByRole('button', { name: 'I scheduled it in LinkedIn' }));
    fireEvent.change(screen.getByLabelText(/When LinkedIn will post it/), { target: { value: '2030-12-04T09:15' } });
    await user.click(screen.getByRole('button', { name: 'Mark as scheduled' }));
    await waitFor(() => {
      expect(api.calls('POST', api.path('/kit'))).toHaveLength(2);
    });
    expect(api.calls('POST', api.path('/kit'))[1]?.body).toEqual({
      to: 'natively_scheduled',
      scheduledAt: new Date('2030-12-04T09:15').toISOString(),
    });

    await user.click(await screen.findByRole('button', { name: 'It’s posted' }));
    const finalText = screen.getByRole('textbox', { name: /Final text/ });
    expect(finalText).toHaveValue(kitPost.displayText);
    await user.click(screen.getByRole('button', { name: 'Mark as posted' }));
    expect(await screen.findByText(/Paste the post’s link from LinkedIn/)).toBeInTheDocument();
    await user.type(screen.getByRole('textbox', { name: /Post link/ }), POST_URL);
    await user.click(screen.getByRole('button', { name: 'Mark as posted' }));
    expect(await screen.findByRole('heading', { level: 2, name: 'Posted on LinkedIn' })).toBeInTheDocument();
    expect(api.calls('POST', api.path('/kit'))[2]?.body).toEqual({ to: 'posted', postUrl: POST_URL, finalText: kitPost.displayText });
  });
});

describe('an article piece', () => {
  it('lists its posts that can go live, most urgent first', async () => {
    const api = postsServer([
      approvedPost({ id: POST_ID }),
      makePost({ id: 'post-0193a1b2c3d4e5f60718293a4b5e', displayText: 'Still a draft.' }),
    ]);
    server.use(...api.handlers);
    renderTab(<GoLiveTab pieceId={ARTICLE_ID} />, `/studio/${ARTICLE_ID}/go-live`);
    const nav = await screen.findByRole('navigation', { name: 'Posts going live' });
    const links = within(nav).getAllByRole('link');
    expect(links).toHaveLength(1);
    expect(links[0]).toHaveAttribute('href', `/studio/${ARTICLE_ID}/go-live?post=${POST_ID}`);
  });

  it('has nothing scheduled when no post is approved', async () => {
    const api = postsServer([makePost()]);
    server.use(...api.handlers);
    renderTab(<GoLiveTab pieceId={ARTICLE_ID} />, `/studio/${ARTICLE_ID}/go-live`);
    expect(await screen.findByRole('heading', { level: 2, name: 'Nothing scheduled.' })).toBeInTheDocument();
  });
});

describe('in the app', () => {
  it('opens from a push link on a phone, inside the Studio', async () => {
    const api = goLiveServer(approvedPost(NATIVE));
    server.use(...api.handlers);
    renderApp(`/studio/${POST_ID}/go-live?post=${POST_ID}`, { viewport: 'mobile' });
    await findPageHeading(/./);
    expect(await screen.findByRole('button', { name: 'Publish now' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Go-live' })).toHaveAttribute('aria-selected', 'true');
  });
});
