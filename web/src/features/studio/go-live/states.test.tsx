import { screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { server } from '@/test/msw';

import { approvedPost, makePost, POST_ID, SHA256, T0 } from '../posts/testing/fixtures';
import { renderTab } from '../posts/testing/render';
import type { Post } from '../posts/model/types';

import { GoLiveTab } from './go-live-tab';
import { goLiveServer, type GoLiveServerOptions } from './testing/handlers';

const NATIVE: Partial<Post> = { lane: 'native', articleId: null };

function open(post: Post, options: GoLiveServerOptions = {}) {
  const api = goLiveServer(post, options);
  server.use(...api.handlers);
  const view = renderTab(<GoLiveTab pieceId={post.id} />, `/studio/${post.id}/go-live?post=${post.id}`, {
    viewport: 'mobile',
  });
  return { ...view, api };
}

describe('go-live states', () => {
  it('says an attempt is under way and not to publish again', async () => {
    open(
      approvedPost({
        ...NATIVE,
        status: 'publishing',
        intent: { id: 'intent-1', createdAt: T0, approvalSha256: SHA256, status: 'pending' },
      }),
    );
    expect(await screen.findByRole('heading', { level: 2, name: 'Publishing to LinkedIn…' })).toBeInTheDocument();
    expect(screen.getByText(/one attempt is under way, and it is never repeated/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Publish now' })).not.toBeInTheDocument();
  });

  it('records that an unanswered attempt did not go out, so it can be published again later', async () => {
    const post = makePost({
      ...NATIVE,
      status: 'needs_attention',
      approval: { sha256: SHA256, approvedAt: T0 },
      intent: { id: 'intent-1', createdAt: T0, approvalSha256: SHA256, status: 'unknown', errorCode: 'linkedin_503' },
    });
    const { user, api } = open(post);
    expect(await screen.findByText(/LinkedIn had a problem \(HTTP 503\)/)).toBeInTheDocument();
    await user.click(screen.getByRole('radio', { name: 'It didn’t go out' }));
    expect(screen.queryByRole('textbox', { name: 'Post link' })).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Save what I saw' }));
    await waitFor(() => {
      expect(api.calls('POST', api.path('/resolve'))).toEqual([expect.objectContaining({ body: { outcome: 'not_posted' } })]);
    });
    expect(await screen.findByRole('button', { name: 'Publish now' })).toBeInTheDocument();
  });

  it('refuses a post link that is not LinkedIn’s, before sending anything', async () => {
    const post = makePost({
      ...NATIVE,
      status: 'needs_attention',
      approval: { sha256: SHA256, approvedAt: T0 },
      intent: { id: 'intent-1', createdAt: T0, approvalSha256: SHA256, status: 'unknown' },
    });
    const { user, api } = open(post);
    await user.click(await screen.findByRole('radio', { name: 'It’s on LinkedIn' }));
    await user.type(screen.getByRole('textbox', { name: 'Post link' }), 'https://example.com/post');
    await user.click(screen.getByRole('button', { name: 'Save what I saw' }));
    expect(await screen.findByText(/Paste the post’s link from LinkedIn/)).toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: 'Post link' })).toHaveAttribute('aria-invalid', 'true');
    expect(api.calls('POST', api.path('/resolve'))).toHaveLength(0);
  });

  it('sends a post the writer could not finish back to the composer', async () => {
    open(makePost({ ...NATIVE, status: 'needs_attention', lastError: 'no_hooks' }));
    expect(await screen.findByRole('heading', { level: 2, name: 'This post needs you' })).toBeInTheDocument();
    expect(screen.getByText(/The writer produced no usable hook/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Open the post' })).toHaveAttribute('href', `/studio/${POST_ID}/posts`);
  });

  it('explains a staging failure on the publish page', async () => {
    open(approvedPost({ ...NATIVE, lastError: 'reconnect_required' }));
    expect(await screen.findByText('The last attempt didn’t go through.')).toBeInTheDocument();
    expect(screen.getByText(/LinkedIn needs you to sign in again\. Reconnect in Settings > Integrations/)).toBeInTheDocument();
  });

  it('offers the kit for a post set to go out by hand', async () => {
    const { user, api } = open(approvedPost({ ...NATIVE, goLiveMode: 'kit' }));
    expect(await screen.findByText('This post is set to go out by hand.')).toBeInTheDocument();
    // The note's button comes first; the action bar has the same one.
    const [inNote] = screen.getAllByRole('button', { name: 'Switch to kit' });
    if (!inNote) throw new Error('no Switch to kit button');
    await user.click(inNote);
    expect(await screen.findByRole('heading', { level: 2, name: 'Post it yourself' })).toBeInTheDocument();
    expect(api.calls('POST', api.path('/mode'))).toHaveLength(1);
  });

  it('goes back from a prepared kit to one-click publishing', async () => {
    const { user, api } = open(
      approvedPost({ ...NATIVE, status: 'kit', goLiveMode: 'kit', kit: { state: 'prepared', preparedAt: T0 } }),
    );
    await user.click(await screen.findByRole('button', { name: 'Back to one-click publishing' }));
    expect(await screen.findByRole('button', { name: 'Publish now' })).toBeInTheDocument();
    expect(api.calls('POST', api.path('/mode'))).toEqual([expect.objectContaining({ body: { mode: 'one_click' } })]);
  });

  it('shows a scheduled kit with its time and lets you change it', async () => {
    open(
      approvedPost({
        ...NATIVE,
        status: 'kit',
        goLiveMode: 'kit',
        kit: { state: 'natively_scheduled', preparedAt: T0, scheduledAt: '2030-12-04T14:15:00.000000Z' },
      }),
    );
    expect(await screen.findByText(/Scheduled in LinkedIn for /)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Change the scheduled time' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'I unscheduled it: it’s a draft in LinkedIn' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Back to one-click publishing' })).not.toBeInTheDocument();
  });

  it('keeps a post posted by hand out of the app’s edit and delete', async () => {
    open(approvedPost({ ...NATIVE, status: 'posted', postedAt: T0, postUrl: 'https://www.linkedin.com/feed/update/urn:li:share:1/' }));
    expect(await screen.findByText(/You posted this one by hand/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Edit the live post' })).not.toBeInTheDocument();
  });

  it.each([
    ['expired', 'This post expired.'],
    ['deleted', 'Deleted from LinkedIn.'],
    ['archived', 'This post is archived.'],
  ] as const)('explains a %s post', async (status, title) => {
    open(approvedPost({ ...NATIVE, status }));
    expect(await screen.findByText(title)).toBeInTheDocument();
  });

  it('shows a load failure with the request id and a retry', async () => {
    server.use(...goLiveServer(makePost()).handlers);
    const missing = 'post-0193a1b2c3d4e5f60718293a4bff';
    const { user } = renderTab(<GoLiveTab pieceId={missing} />, `/studio/${missing}/go-live`);
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Couldn’t load the go-live page.');
    expect(alert).toHaveTextContent('Request ID req-test-9');
    expect(within(alert).getByRole('button', { name: 'Try loading the go-live page again' })).toBeInTheDocument();
    await user.click(within(alert).getByRole('button', { name: 'Try loading the go-live page again' }));
  });
});
