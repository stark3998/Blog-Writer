import { act, screen, waitFor, within } from '@testing-library/react';
import { userEvent } from '@testing-library/user-event';
import { toast } from 'sonner';
import { afterEach, describe, expect, it } from 'vitest';

import { todayResponse } from '@/features/today/testing/fixtures';
import { todayHandlers } from '@/features/today/testing/handlers';
import { server } from '@/test/msw';
import { renderApp } from '@/test/render-app';

import { ANGLES, itemEntry, record, THEIR_COMMENT, watchEntry, type ItemEntry } from './testing/fixtures';
import { createEngagementBackend, problemResponse } from './testing/handlers';

afterEach(() => {
  // Toasts live in a module-level store that replays them to the next test's toaster.
  act(() => {
    toast.dismiss();
  });
});

const [FIRST_ANGLE, SECOND_ANGLE] = ANGLES;
if (!FIRST_ANGLE || !SECOND_ANGLE) throw new Error('two angles expected');

function setup(seed: Parameters<typeof createEngagementBackend>[0] = {}) {
  const backend = createEngagementBackend(seed);
  // The backend answers the open list first; Today's own reads come after.
  server.use(...backend.handlers, ...todayHandlers({ today: todayResponse({ items: [] }) }));
  return backend;
}

async function openHelper(path = '/today', viewport: 'desktop' | 'mobile' = 'mobile') {
  const view = renderApp(path, { viewport });
  await view.user.click(await screen.findByRole('button', { name: 'Open the reply helper' }));
  const sheet = await screen.findByRole('dialog', { name: 'Reply helper' });
  return { ...view, sheet: within(sheet), sheetElement: sheet };
}

const LINT_WAIT = { timeout: 3000 };

describe('Engage: a reply from paste to posted', () => {
  it('saves their text, offers angles, checks the reply, copies it and keeps what was posted', async () => {
    const backend = setup();
    const { user, sheet } = await openHelper();

    await user.click(sheet.getByRole('button', { name: 'New reply' }));
    await user.click(sheet.getByRole('radio', { name: 'Reply to a comment' }));
    await user.click(sheet.getByRole('textbox', { name: 'Their text' }));
    await user.paste(THEIR_COMMENT);
    await user.click(sheet.getByRole('button', { name: 'Save and get angles' }));

    // The item is saved for 48 hours and angles are asked for at once.
    const angle = await sheet.findByRole('radio', { name: FIRST_ANGLE.text });
    expect(sheet.getByRole('radio', { name: SECOND_ANGLE.text })).toHaveAccessibleDescription(
      'Draws on your position and a research brief',
    );
    expect(backend.callsTo('POST', '/api/engagement/items')[0]?.body).toEqual({
      itemKind: 'reply',
      pastedText: THEIR_COMMENT,
      postId: null,
      targetUrl: null,
    });
    expect(backend.callsTo('POST', '/api/engagement/items/eng-0002/angles')).toHaveLength(1);
    expect(sheet.getByText('Deleted in 48 h')).toBeInTheDocument();

    // Choosing an angle starts the draft from it; the check runs when typing pauses.
    await user.click(angle);
    expect(sheet.getByRole('textbox', { name: 'Your reply' })).toHaveValue(FIRST_ANGLE.text);
    expect(await sheet.findByText('Ready to copy.', {}, LINT_WAIT)).toBeInTheDocument();
    expect(backend.callsTo('POST', '/api/engagement/items/eng-0002/lint')[0]?.body).toEqual({ draftText: FIRST_ANGLE.text });

    await user.click(sheet.getByRole('button', { name: 'Copy reply' }));
    await expect(navigator.clipboard.readText()).resolves.toBe(FIRST_ANGLE.text);
    expect(await screen.findByText('Reply copied. Paste it on LinkedIn.')).toBeInTheDocument();

    await user.click(sheet.getByRole('button', { name: 'I posted it' }));
    expect(sheet.getByRole('textbox', { name: 'What you posted' })).toHaveValue(FIRST_ANGLE.text);
    await user.type(sheet.getByRole('textbox', { name: 'Link to your reply' }), 'https://www.linkedin.com/feed/update/urn:li:comment:9/');
    await user.click(sheet.getByRole('button', { name: 'Save what I posted' }));

    expect(await screen.findByText('Saved what you posted. Their text is deleted.')).toBeInTheDocument();
    const [finalize] = backend.callsTo('POST', '/api/engagement/items/eng-0002/finalize');
    expect(finalize?.ifMatch).toBe('"eng-102"');
    expect(finalize?.body).toEqual({
      finalText: FIRST_ANGLE.text,
      url: 'https://www.linkedin.com/feed/update/urn:li:comment:9/',
      watchEntryId: null,
    });
    expect(await sheet.findByText(/^Nothing waiting\./)).toBeInTheDocument();
  });

  it('blocks Copy until the check passes and shows what to fix', async () => {
    setup({ items: [itemEntry({ angles: ANGLES })] });
    const { user, sheet } = await openHelper();
    await user.click(await sheet.findByRole('button', { name: /How do you stop agents/ }));

    const draft = sheet.getByRole('textbox', { name: 'Your reply' });
    await user.click(draft);
    await user.paste('We wrote it up here https://example.com/post and it helped.');
    expect(await sheet.findByText('1 thing to fix before you copy it.', {}, LINT_WAIT)).toBeInTheDocument();
    expect(sheet.getByText(/A link: LinkedIn shows comments with links to fewer people/)).toBeInTheDocument();
    expect(sheet.getByText('“https://example.com/post”')).toBeInTheDocument();
    expect(sheet.getByRole('button', { name: 'Copy reply' })).toBeDisabled();

    await user.clear(draft);
    await user.paste('We wrote it up and it helped: one identity per agent, expiring after each task.');
    expect(await sheet.findByText('Ready to copy.', {}, LINT_WAIT)).toBeInTheDocument();
    expect(sheet.getByRole('button', { name: 'Copy reply' })).toBeEnabled();
  });

  it('counts characters and stops Copy over 1,000', async () => {
    setup({ items: [itemEntry({ angles: ANGLES })] });
    const { user, sheet } = await openHelper();
    await user.click(await sheet.findByRole('button', { name: /How do you stop agents/ }));
    await user.click(sheet.getByRole('textbox', { name: 'Your reply' }));
    await user.paste('a'.repeat(1010));
    expect(sheet.getByText('1,010 / 1,000')).toBeInTheDocument();
    // Read out once, without the running count (the counter beside it has the number).
    expect(sheet.getByText('Over 1,000 characters. Shorten it before you copy it.')).toHaveAttribute('role', 'status');
    expect(sheet.getByRole('button', { name: 'Copy reply' })).toBeDisabled();
  });
});

describe('Engage: problems', () => {
  it('explains a missing AI-use confirmation with the way to fix it', async () => {
    const backend = setup({ items: [itemEntry()] });
    backend.angles = () => problemResponse(409, 'ai_use_not_confirmed');
    const { user, sheet } = await openHelper();
    await user.click(await sheet.findByRole('button', { name: /How do you stop agents/ }));
    await user.click(sheet.getByRole('button', { name: 'Get angles' }));

    expect(await sheet.findByText('Angles need your AI-use confirmation')).toBeInTheDocument();
    expect(sheet.getByRole('link', { name: 'Open Blocklist & policy' })).toHaveAttribute('href', '/settings/policy');
    // The owner can still write the reply.
    expect(sheet.getByRole('textbox', { name: 'Your reply' })).toBeEnabled();
  });

  it('offers a retry when the model does not answer', async () => {
    const backend = setup({ items: [itemEntry()] });
    let calls = 0;
    backend.angles = () => {
      calls += 1;
      return calls === 1 ? problemResponse(504, 'timeout', { detail: 'The model took too long. Try again.' }) : ANGLES;
    };
    const { user, sheet } = await openHelper();
    await user.click(await sheet.findByRole('button', { name: /How do you stop agents/ }));
    await user.click(sheet.getByRole('button', { name: 'Get angles' }));
    expect(await sheet.findByText('The model took too long. Try again.')).toBeInTheDocument();
    await user.click(sheet.getByRole('button', { name: 'Try again' }));
    expect(await sheet.findByRole('radio', { name: FIRST_ANGLE.text })).toBeInTheDocument();
  });

  it('says when the item expired, keeps the draft and stops offering it unchecked', async () => {
    const backend = setup({ items: [itemEntry({ angles: ANGLES })] });
    backend.lint = () => problemResponse(410, 'expired');
    const { user, sheet } = await openHelper();
    await user.click(await sheet.findByRole('button', { name: /How do you stop agents/ }));
    await user.click(sheet.getByRole('textbox', { name: 'Your reply' }));
    await user.paste('One identity per agent.');
    expect(await sheet.findByText('This item expired', {}, LINT_WAIT)).toBeInTheDocument();
    expect(sheet.getByRole('textbox', { name: 'Your reply' })).toHaveValue('One identity per agent.');
    // Fail closed: an unchecked draft is not offered for copying, and nothing can be saved.
    expect(sheet.getByRole('button', { name: 'I posted it' })).toBeDisabled();
    expect(sheet.getByRole('button', { name: 'Copy reply' })).toBeDisabled();
  });

  it('shows a conflict on a stale item and saves over it with Keep mine', async () => {
    const backend = setup({ items: [itemEntry({ angles: ANGLES }, '"eng-1"')] });
    const { user, sheet } = await openHelper();
    await user.click(await sheet.findByRole('button', { name: /How do you stop agents/ }));
    await user.click(sheet.getByRole('textbox', { name: 'Your reply' }));
    await user.paste('One identity per agent, expiring after each task.');
    await user.click(sheet.getByRole('button', { name: 'I posted it' }));
    // Another tab asked for new angles in the meantime.
    backend.items = backend.items.map((entry): ItemEntry => ({ ...entry, etag: '"eng-7"' }));
    await user.click(sheet.getByRole('button', { name: 'Save what I posted' }));

    const conflict = await screen.findByRole('alertdialog', { name: 'This reply changed since you opened it' });
    await user.click(within(conflict).getByRole('button', { name: 'Keep mine' }));
    expect(await screen.findByText('Saved what you posted. Their text is deleted.')).toBeInTheDocument();
    expect(backend.callsTo('POST', '/api/engagement/items/eng-0001/finalize').map((call) => call.ifMatch)).toEqual([
      '"eng-1"',
      '"eng-7"',
    ]);
  });

  it('asks before dismissing a reply in progress, then deletes it with If-Match', async () => {
    const backend = setup({ items: [itemEntry({ angles: ANGLES }, '"eng-3"')] });
    const { user, sheet } = await openHelper();
    await user.click(await sheet.findByRole('button', { name: /How do you stop agents/ }));
    await user.click(sheet.getByRole('textbox', { name: 'Your reply' }));
    await user.paste('Half a thought');
    await user.click(sheet.getByRole('button', { name: 'Dismiss' }));

    const confirm = await screen.findByRole('alertdialog', { name: 'Dismiss this one?' });
    expect(within(confirm).getByRole('button', { name: 'Keep it' })).toHaveFocus();
    await user.click(within(confirm).getByRole('button', { name: 'Dismiss' }));
    expect(await screen.findByText('Dismissed. Their text is deleted.')).toBeInTheDocument();
    expect(backend.callsTo('POST', '/api/engagement/items/eng-0001/dismiss')[0]?.ifMatch).toBe('"eng-3"');
  });
});

describe('Engage: the share target and screenshots', () => {
  it('reads a share once, removes it from the URL and saves nothing until Save', async () => {
    const backend = setup();
    const text = 'Identity is the control plane for agents. Agree?';
    const link = 'https://www.linkedin.com/posts/someone_identity-activity-1';
    const { router, user } = renderApp(`/today?share=${encodeURIComponent(text)}&share_url=${encodeURIComponent(link)}`, {
      viewport: 'mobile',
    });

    const sheet = within(await screen.findByRole('dialog', { name: 'Reply helper' }));
    await waitFor(() => {
      expect(router.state.location.search).toBe('');
    });
    expect(sheet.getByText('Shared from another app')).toBeInTheDocument();
    expect(sheet.getByRole('textbox', { name: 'Their text' })).toHaveValue(text);
    expect(sheet.getByRole('textbox', { name: 'Link to it' })).toHaveValue(link);
    expect(sheet.getByRole('radio', { name: 'Comment on a post' })).toBeChecked();
    expect(backend.callsTo('POST', '/api/engagement/items')).toHaveLength(0);

    await user.click(sheet.getByRole('button', { name: 'Save and get angles' }));
    expect(await sheet.findByRole('radio', { name: FIRST_ANGLE.text })).toBeInTheDocument();
    expect(backend.callsTo('POST', '/api/engagement/items')[0]?.body).toEqual({
      itemKind: 'comment',
      pastedText: text,
      postId: null,
      targetUrl: link,
    });
  });

  it('reads the owner’s screenshot into the text box, sending the image as the raw body', async () => {
    const backend = setup();
    const { user, sheet } = await openHelper();
    await user.click(sheet.getByRole('button', { name: 'New reply' }));
    const bytes = new Uint8Array([137, 80, 78, 71, 13, 10, 26, 10]);
    await user.upload(sheet.getByLabelText('Read a screenshot'), new File([bytes], 'shot.png', { type: 'image/png' }));

    expect(await sheet.findByText('Text read from your screenshot')).toBeInTheDocument();
    expect(sheet.getByRole('textbox', { name: 'Their text' })).toHaveValue(
      'Read from the screenshot: which identity do your agents use?',
    );
    const [call] = backend.callsTo('POST', '/api/engagement/extract');
    expect(call?.contentType).toBe('image/png');
    expect(call?.body).toEqual(bytes);
    expect(backend.callsTo('POST', '/api/engagement/items')).toHaveLength(0);
  });

  it('refuses files it cannot read before anything is sent', async () => {
    const backend = setup();
    const { sheet } = await openHelper();
    const user = userEvent.setup({ applyAccept: false });
    await user.click(sheet.getByRole('button', { name: 'New reply' }));
    await user.upload(sheet.getByLabelText('Read a screenshot'), new File(['x'], 'shot.heic', { type: 'image/heic' }));
    expect(await sheet.findByText('Use a PNG, JPEG or WebP screenshot.')).toBeInTheDocument();
    expect(backend.callsTo('POST', '/api/engagement/extract')).toHaveLength(0);
  });

  it('validates the paste and the link in place', async () => {
    const backend = setup();
    const { user, sheet } = await openHelper();
    await user.click(sheet.getByRole('button', { name: 'New reply' }));
    await user.type(sheet.getByRole('textbox', { name: 'Link to it' }), 'http://insecure.example');
    await user.click(sheet.getByRole('button', { name: 'Save and get angles' }));
    const text = sheet.getByRole('textbox', { name: 'Their text' });
    expect(text).toHaveAttribute('aria-invalid', 'true');
    expect(text).toHaveFocus();
    expect(sheet.getByText('Paste their text, or read it from a screenshot.')).toBeInTheDocument();
    expect(sheet.getByText('Use the https link LinkedIn gives you, or leave it empty.')).toBeInTheDocument();
    expect(backend.callsTo('POST', '/api/engagement/items')).toHaveLength(0);
  });
});

describe('Engage: the daily session and what was posted', () => {
  it('starts with how many voices to add, adds one and marks it engaged', async () => {
    const backend = setup();
    const { user, sheet } = await openHelper();
    await user.click(sheet.getByRole('tab', { name: 'Daily session' }));
    expect(await sheet.findByText('No voices yet.')).toBeInTheDocument();
    expect(sheet.getByText(/Add 5 to 10 niche voices outside your network/)).toBeInTheDocument();

    await user.type(sheet.getByRole('textbox', { name: 'Name' }), 'Dana Reyes');
    await user.type(sheet.getByRole('textbox', { name: 'Profile link' }), 'https://www.linkedin.com/in/dana-reyes');
    await user.click(sheet.getByRole('button', { name: 'Add the voice' }));
    expect(await screen.findByText('Added Dana Reyes.')).toBeInTheDocument();
    expect(backend.callsTo('POST', '/api/engagement/watchlist')[0]?.body).toEqual({
      name: 'Dana Reyes',
      profileUrl: 'https://www.linkedin.com/in/dana-reyes',
      pillar: null,
      note: null,
      active: true,
    });

    await user.click(await sheet.findByRole('button', { name: 'Mark engaged with Dana Reyes' }));
    expect(await sheet.findByText('Done for today')).toBeInTheDocument();
    expect(sheet.getByText('All 1 voices engaged today.')).toBeInTheDocument();
    expect(sheet.getByRole('link', { name: 'Open profile of Dana Reyes (opens in a new tab)' })).toHaveAttribute(
      'rel',
      'noopener noreferrer',
    );
  });

  it('pauses a voice with If-Match from its menu', async () => {
    const backend = setup({ watch: [watchEntry({}, '"watch-5"')] });
    const { user, sheet } = await openHelper();
    await user.click(sheet.getByRole('tab', { name: 'Daily session' }));
    expect(await sheet.findByText('Identity modernization')).toBeInTheDocument();
    await user.click(sheet.getByRole('button', { name: 'More for Dana Reyes' }));
    await user.click(await screen.findByRole('menuitem', { name: 'Pause' }));
    await waitFor(() => {
      expect(backend.callsTo('PUT', '/api/engagement/watchlist/watch-0001')[0]?.ifMatch).toBe('"watch-5"');
    });
    expect(await sheet.findByRole('button', { name: 'Paused voices (1)' })).toBeInTheDocument();
  });

  it('lists what the owner posted, with its link', async () => {
    setup({ records: [record()] });
    const { user, sheet } = await openHelper();
    await user.click(sheet.getByRole('tab', { name: 'Posted' }));
    expect(await sheet.findByText(/We moved approvals for new agents/)).toBeInTheDocument();
    expect(sheet.getByText('Substantive')).toBeInTheDocument();
    expect(sheet.getByRole('link', { name: 'Open on LinkedIn (opens in a new tab)' })).toHaveAttribute(
      'href',
      'https://www.linkedin.com/feed/update/urn:li:activity:2/',
    );
  });
});

describe('Engage on Today', () => {
  it('says how many replies are waiting and opens as a side sheet on desktop', async () => {
    setup({ items: [itemEntry()] });
    const { sheetElement } = await openHelper('/today', 'desktop');
    expect(sheetElement).toHaveClass('right-0');
    expect(screen.getByText('1 waiting for a reply.')).toBeInTheDocument();
  });
});
