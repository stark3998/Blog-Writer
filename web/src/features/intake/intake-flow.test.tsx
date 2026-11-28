import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router';
import { describe, expect, it, vi } from 'vitest';

import { DataProvider } from '@/features/data/data-provider';
import { createTestAppData } from '@/test/app-data';
import { problem, server } from '@/test/msw';

import { IntakeFlow, useIntakeState, type IntakeFlowProps } from './intake-flow';
import type { ConfirmedWording } from './intake-state';
import { checkResult, findingAt, MESSAGES, PROPOSED_WORDING, RAW_ANSWER, redactionProposal } from './testing/fixtures';
import { intakeHandlers, problemResponse, type IntakeHandlerOptions } from './testing/handlers';

type HarnessProps = Partial<Omit<IntakeFlowProps, 'state' | 'onStateChange'>>;

/** Everything a web storage area holds, as one string. */
function storageText(storage: Storage): string {
  return Array.from({ length: storage.length }, (_, index) => {
    const key = storage.key(index) ?? '';
    return `${key}=${storage.getItem(key) ?? ''}`;
  }).join('\n');
}

function Harness(props: HarnessProps) {
  const [state, setState] = useIntakeState();
  return <IntakeFlow purpose="story" label="Your story" state={state} onStateChange={setState} checkDelayMs={0} {...props} />;
}

function renderFlow(options: IntakeHandlerOptions = {}, props: HarnessProps = {}) {
  const intake = intakeHandlers(options);
  server.use(...intake.handlers);
  const appData = createTestAppData();
  const onConfirm = vi.fn<(confirmed: ConfirmedWording) => void>();
  const user = userEvent.setup();
  render(
    <MemoryRouter>
      <DataProvider appData={appData}>
        <Harness onConfirm={onConfirm} {...props} />
      </DataProvider>
    </MemoryRouter>,
  );
  return { user, appData, onConfirm, calls: intake.calls };
}

/** Types the raw words (pasted: user-event types one key at a time) and asks for the public-safe wording. */
async function writeAndCheck(user: ReturnType<typeof userEvent.setup>, text = RAW_ANSWER) {
  const field = screen.getByRole('textbox', { name: 'Your story' });
  await user.click(field);
  await user.paste(text);
  await user.click(screen.getByRole('button', { name: 'Make it public-safe' }));
}

describe('IntakeFlow', () => {
  it('turns raw words into confirmed public-safe wording, sending the raw text to redact only', async () => {
    const { user, appData, onConfirm, calls } = renderFlow({}, { pillar: 'identity' });
    await writeAndCheck(user);

    const heading = await screen.findByRole('heading', { name: 'Check the wording' });
    await waitFor(() => {
      expect(heading).toHaveFocus();
    });
    expect(calls.redact).toEqual([{ text: RAW_ANSWER, purpose: 'story', pillar: 'identity' }]);
    expect(screen.getByText('In your words: 1 detail to fix and 2 to check.')).toBeInTheDocument();

    // The raw words, marked like a proof, with a numbered list beside them.
    const marked = screen.getByRole('group', { name: 'Your words, with the details found marked' });
    expect(marked).toHaveTextContent('jane@northwind.example3 (fix 3)');
    const list = screen.getByRole('list', { name: 'Details found in your words' });
    const items = within(list).getAllByRole('listitem');
    expect(items.map((item) => item.textContent)).toEqual([
      expect.stringContaining('Check: Exact figure“2,137”'),
      expect.stringContaining('Check: Possible name“Northwind Bank”'),
      expect.stringContaining(`Fix: E-mail address“jane@northwind.example”${MESSAGES.email}`),
    ]);

    const wording = screen.getByRole('textbox', { name: 'Wording to keep' });
    expect(wording).toHaveValue(PROPOSED_WORDING);
    expect(await screen.findByText('Ready to use. Nothing confidential found.')).toBeInTheDocument();
    expect(calls.check).toEqual([PROPOSED_WORDING]);

    // What the anonymizer changed, struck through and underlined, on request.
    const toggle = screen.getByRole('button', { name: 'Show what changed from your words' });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    await user.click(toggle);
    expect(screen.getByRole('group', { name: 'What changed from your words' })).toHaveTextContent('1 paragraph changed.');
    expect(screen.getByRole('button', { name: 'Hide what changed' })).toHaveAttribute('aria-expanded', 'true');

    await user.click(screen.getByRole('button', { name: 'Use this wording' }));
    expect(onConfirm).toHaveBeenCalledWith({ text: PROPOSED_WORDING, redactionFindings: 3, warnings: [] });
    const confirmed = await screen.findByRole('heading', { name: 'Wording confirmed' });
    await waitFor(() => {
      expect(confirmed).toHaveFocus();
    });
    expect(screen.getByText(PROPOSED_WORDING)).toBeInTheDocument();

    // The raw text went to redact and nowhere else: not to the check, storage or the query cache.
    expect(calls.check.some((text) => text.includes('jane@'))).toBe(false);
    expect(storageText(window.localStorage)).not.toContain('Northwind');
    expect(storageText(window.sessionStorage)).not.toContain('Northwind');
    const cached = appData.queryClient.getQueryCache().getAll().map((query) => JSON.stringify([query.queryKey, query.state.data]));
    expect(cached.join(' ')).not.toContain('Northwind');
    expect(JSON.stringify(appData.queryClient.getMutationCache().getAll().map((mutation) => mutation.state.variables))).not.toContain(
      'Northwind',
    );
  });

  it('edits the wording, rechecks it and refuses a blocking detail until it is fixed', async () => {
    const { user, onConfirm, calls } = renderFlow({
      check: (text) =>
        text.includes('@')
          ? checkResult({ ok: false, findings: [findingAt(text, 'ops@northwind.example', 'email-address', 'block', MESSAGES.email)] })
          : checkResult(),
    });
    await writeAndCheck(user);
    const wording = await screen.findByRole('textbox', { name: 'Wording to keep' });
    await user.clear(wording);
    await user.paste('Ask ops@northwind.example for the runbook.');
    expect(await screen.findByText('1 detail to fix before you can use it.')).toBeInTheDocument();
    const found = screen.getByRole('list', { name: 'Details found in the wording' });
    expect(found).toHaveTextContent('Fix: E-mail address“ops@northwind.example”');

    await user.click(screen.getByRole('button', { name: 'Use this wording' }));
    expect(onConfirm).not.toHaveBeenCalled();
    expect(wording).toHaveAccessibleDescription(/Fix the details listed under the wording first\./);
    expect(wording).toHaveFocus();

    await user.clear(wording);
    await user.paste('Ask the app owner for the runbook.');
    expect(await screen.findByText('Ready to use. Nothing confidential found.')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Use this wording' }));
    expect(onConfirm).toHaveBeenCalledWith(expect.objectContaining({ text: 'Ask the app owner for the runbook.' }));
    expect(calls.check.every((text) => !text.includes('jane@'))).toBe(true);
  });

  it('shows details to check that appear only as the wording is confirmed, before confirming it', async () => {
    const name = findingAt(PROPOSED_WORDING, 'regional bank', 'possible-name', 'warn', MESSAGES.name);
    // A slow live check: the tap gets there first.
    const { user, onConfirm } = renderFlow({ check: () => checkResult({ findings: [name] }) }, { checkDelayMs: 60_000 });
    await writeAndCheck(user);
    await user.click(await screen.findByRole('button', { name: 'Use this wording' }));
    expect(await screen.findByText('1 detail to check. Look it over, then tap Use this wording again.')).toBeInTheDocument();
    expect(screen.getByRole('list', { name: 'Details found in the wording' })).toHaveTextContent('Check: Possible name“regional bank”');
    expect(onConfirm).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: 'Use this wording' }));
    expect(onConfirm).toHaveBeenCalledWith({ text: PROPOSED_WORDING, redactionFindings: 3, warnings: [name] });
  });

  it('explains a missing proposal and starts from the words with the blocking details masked', async () => {
    const { user } = renderFlow({
      redact: () => redactionProposal({ proposal: null, reason: 'ai_use_not_confirmed', anonymizerUsed: false }),
    });
    await writeAndCheck(user);
    expect(await screen.findByText('No suggestion: AI use isn’t confirmed')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Open Blocklist & policy' })).toHaveAttribute('href', '/settings/policy');
    expect(screen.getByRole('textbox', { name: 'Wording to keep' })).toHaveValue(
      'We moved 2,137 apps for Northwind Bank. Ask [email address] for the runbook.',
    );
    expect(screen.getByText('Replace the bracketed placeholders so each sentence reads naturally.')).toBeInTheDocument();
  });

  it('asks again for a suggestion when the anonymizer was unavailable', async () => {
    const { user, calls } = renderFlow({
      redact: (_body, call) =>
        call === 1 ? redactionProposal({ proposal: null, reason: 'ai_unavailable', anonymizerUsed: false }) : redactionProposal(),
    });
    await writeAndCheck(user);
    expect(await screen.findByText('No suggestion right now')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Ask for a suggestion again' }));
    await waitFor(() => {
      expect(screen.getByRole('textbox', { name: 'Wording to keep' })).toHaveValue(PROPOSED_WORDING);
    });
    expect(calls.redact).toHaveLength(2);
    // The button that asked is gone with the notice: focus lands on the step's heading again.
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Check the wording' })).toHaveFocus();
    });
  });

  it('goes back to the raw words and returns to the same review without another redaction', async () => {
    const { user, calls } = renderFlow();
    await writeAndCheck(user);
    await screen.findByRole('heading', { name: 'Check the wording' });
    await user.click(screen.getByRole('button', { name: 'Edit my words' }));
    const raw = screen.getByRole('textbox', { name: 'Your story' });
    await waitFor(() => {
      expect(raw).toHaveFocus();
    });
    expect(raw).toHaveValue(RAW_ANSWER);
    await user.click(screen.getByRole('button', { name: 'Make it public-safe' }));
    expect(await screen.findByRole('heading', { name: 'Check the wording' })).toBeInTheDocument();
    expect(calls.redact).toHaveLength(1);
  });

  it('points to the blocklist when it was never saved', async () => {
    const { user } = renderFlow({ redact: () => problem(409, 'blocklist_not_configured') });
    await writeAndCheck(user);
    expect(await screen.findByText('Save your blocklist first')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Open Blocklist & policy' })).toHaveAttribute('href', '/settings/policy');
    // The words are still there to try again later.
    expect(screen.getByRole('textbox', { name: 'Your story' })).toHaveValue(RAW_ANSWER);
  });

  it('shows a failed check with its request id and tries again', async () => {
    const { user } = renderFlow({
      redact: (_body, call) => (call === 1 ? problem(503, 'ai_unavailable', 'req-503') : redactionProposal()),
    });
    await writeAndCheck(user);
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Couldn’t check your words.');
    expect(alert).toHaveTextContent('req-503');
    await user.click(within(alert).getByRole('button', { name: 'Check your words again' }));
    expect(await screen.findByRole('heading', { name: 'Check the wording' })).toBeInTheDocument();
  });

  it('asks for words first, and says when they are too long for one check', async () => {
    const { user } = renderFlow({
      redact: () =>
        problemResponse(422, 'validation_failed', {
          errors: [{ loc: ['body', 'text'], msg: 'String should have at most 8000 characters', type: 'string_too_long' }],
        }),
    });
    await user.click(screen.getByRole('button', { name: 'Make it public-safe' }));
    const raw = screen.getByRole('textbox', { name: 'Your story' });
    expect(raw).toHaveAccessibleDescription(/Write or dictate something first\./);
    expect(raw).toHaveFocus();
    await writeAndCheck(user);
    await waitFor(() => {
      expect(raw).toHaveAccessibleDescription(/That’s more than one check takes\./);
    });
  });

  it('keeps the words while offline and waits to check them', async () => {
    const online = vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false);
    const { user, calls } = renderFlow();
    await user.click(screen.getByRole('textbox', { name: 'Your story' }));
    await user.paste(RAW_ANSWER);
    expect(screen.getByText('You’re offline')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Make it public-safe' })).toBeDisabled();
    online.mockReturnValue(true);
    window.dispatchEvent(new Event('online'));
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Make it public-safe' })).toBeEnabled();
    });
    expect(calls.redact).toHaveLength(0);
  });
});
