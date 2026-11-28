import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http } from 'msw';
import { MemoryRouter } from 'react-router';
import { describe, expect, it } from 'vitest';

import { DataProvider } from '@/features/data/data-provider';
import { checkResult, findingAt, MESSAGES, redactionProposal } from '@/features/intake/testing/fixtures';
import { intakeHandlers, problemResponse } from '@/features/intake/testing/handlers';
import { createTestAppData, seedSession, type TestAppData } from '@/test/app-data';
import { setMedia } from '@/test/media';
import { apiUrl, problem, server } from '@/test/msw';
import { renderApp } from '@/test/render-app';

import { draftStoreFor } from './draft-store';
import { InterviewTab } from './interview-tab';
import {
  ARTICLE_ID,
  articleFixture,
  FULL_QUESTIONS,
  INTERVIEW_ID,
  interviewEntry,
  QUESTIONS,
} from './testing/fixtures';
import { interviewHandlers, type InterviewHandlerOptions } from './testing/handlers';

const Q1 = QUESTIONS[0]?.text ?? '';
const Q2 = QUESTIONS[1]?.text ?? '';
const FIRST_RAW = 'PRIVATE-ONE: at Northwind we cut over 2,137 apps.';
const SECOND_RAW = 'PRIVATE-TWO: start with the app inventory, ask jane@northwind.example.';
const FIRST_WORDING = 'We cut over 2,000+ apps in waves.';
const SECOND_WORDING = 'Start with the app inventory.';

/** The anonymizer's answer per raw text: the wording never repeats the private markers. */
const intake = () =>
  intakeHandlers({
    redact: (body) =>
      redactionProposal({ findings: [], proposal: body.text.startsWith('PRIVATE-ONE') ? FIRST_WORDING : SECOND_WORDING }),
  });

function setup(options: InterviewHandlerOptions = { interviews: [interviewEntry()] }) {
  const interview = interviewHandlers(options);
  const flow = intake();
  server.use(...interview.handlers, ...flow.handlers);
  return { interview, flow };
}

function renderTab({
  pieceId = ARTICLE_ID,
  viewport = 'desktop',
  appData = createTestAppData(),
}: { pieceId?: string; viewport?: 'desktop' | 'mobile'; appData?: TestAppData } = {}) {
  setMedia({ desktop: viewport === 'desktop' });
  seedSession(appData);
  const user = userEvent.setup();
  const tree = () => (
    <MemoryRouter>
      <DataProvider appData={appData}>
        <InterviewTab pieceId={pieceId} />
      </DataProvider>
    </MemoryRouter>
  );
  const view = render(tree());
  return { ...view, appData, user, tree };
}

type User = ReturnType<typeof userEvent.setup>;

/** Writes the raw answer to the question on screen, has it redacted and confirms the wording. */
async function answer(user: User, question: string, raw: string) {
  const field = await screen.findByRole('textbox', { name: question });
  await user.click(field);
  await user.paste(raw);
  await user.click(screen.getByRole('button', { name: 'Make it public-safe' }));
  await user.click(await screen.findByRole('button', { name: 'Use this wording' }));
}

function storageText(storage: Storage): string {
  return Array.from({ length: storage.length }, (_, index) => {
    const key = storage.key(index) ?? '';
    return `${key}=${storage.getItem(key) ?? ''}`;
  }).join('\n');
}

function cacheText(appData: TestAppData): string {
  const queries = appData.queryClient.getQueryCache().getAll().map((query) => [query.queryKey, query.state.data]);
  const mutations = appData.queryClient.getMutationCache().getAll().map((mutation) => mutation.state.variables);
  return JSON.stringify([queries, mutations]);
}

describe('InterviewTab', () => {
  it('answers a micro-interview one question per screen on a phone and sends only the confirmed wording', async () => {
    const { interview, flow } = setup();
    const { user, appData } = renderTab({ viewport: 'mobile' });

    expect(await screen.findByRole('heading', { level: 2, name: 'Two quick questions' })).toBeInTheDocument();
    expect(screen.getByText('Question 1 of 2')).toBeInTheDocument();
    expect(screen.getByRole('heading', { level: 3, name: Q1 })).toBeInTheDocument();
    // One question per screen: the second is not on the page, and the list is behind a button.
    expect(screen.queryByRole('heading', { name: Q2 })).not.toBeInTheDocument();
    expect(screen.queryByRole('navigation', { name: 'Interview questions' })).not.toBeInTheDocument();
    expect(screen.getByRole('progressbar', { name: 'Answers confirmed' })).toHaveAttribute('aria-valuetext', '0 of 2');

    await answer(user, Q1, FIRST_RAW);
    expect(await screen.findByRole('status', { name: '' })).toHaveTextContent('Answer 1 confirmed.');
    const second = screen.getByRole('heading', { level: 3, name: Q2 });
    await waitFor(() => {
      expect(second).toHaveFocus();
    });
    expect(screen.getByRole('progressbar', { name: 'Answers confirmed' })).toHaveAttribute('aria-valuetext', '1 of 2');

    await answer(user, Q2, SECOND_RAW);
    const send = await screen.findByRole('heading', { level: 3, name: 'Check and send' });
    await waitFor(() => {
      expect(send).toHaveFocus();
    });
    expect(screen.getByText(FIRST_WORDING)).toBeInTheDocument();
    expect(screen.getByText(SECOND_WORDING)).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Send 2 answers' }));
    expect(await screen.findByText('Answers sent')).toBeInTheDocument();
    expect(interview.calls.answers).toEqual([
      {
        interviewId: INTERVIEW_ID,
        ifMatch: '"etag-1"',
        body: {
          answers: [
            { questionId: 'q1', text: FIRST_WORDING, redactionFindings: 0 },
            { questionId: 'q2', text: SECOND_WORDING, redactionFindings: 0 },
          ],
        },
      },
    ]);
    expect(screen.getByRole('heading', { level: 2, name: 'Your interview' })).toBeInTheDocument();
    expect(screen.getByText('Answered')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Open the draft' })).toHaveAttribute('href', `/studio/${ARTICLE_ID}/draft`);
    expect(screen.getByRole('heading', { name: 'What happens next' })).toBeInTheDocument();
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();

    // Raw words went to redact only, and nothing kept them: no storage, no cache, no drafts.
    expect(flow.calls.redact.map((call) => call.purpose)).toEqual(['interview_answer', 'interview_answer']);
    expect(flow.calls.redact.map((call) => call.pillar)).toEqual(['identity', 'identity']);
    expect(JSON.stringify([interview.calls, flow.calls.check])).not.toContain('PRIVATE-');
    expect(storageText(window.localStorage) + storageText(window.sessionStorage)).not.toContain('PRIVATE-');
    expect(cacheText(appData)).not.toContain('PRIVATE-');
    expect(draftStoreFor(appData).get(INTERVIEW_ID).size).toBe(0);
  });

  it('autosaves drafts in memory across questions and tab switches, and drops them when the session ends', async () => {
    setup();
    const { user, appData, unmount, tree } = renderTab();
    const rail = await screen.findByRole('navigation', { name: 'Interview questions' });
    await user.click(await screen.findByRole('textbox', { name: Q1 }));
    await user.paste(FIRST_RAW);

    await user.click(within(rail).getByRole('button', { name: /^Question 2/ }));
    await waitFor(() => {
      expect(screen.getByRole('heading', { level: 3, name: Q2 })).toHaveFocus();
    });
    expect(within(rail).getByRole('button', { name: /^Question 2/ })).toHaveAttribute('aria-current', 'step');
    expect(within(rail).getByRole('button', { name: /^Question 1/ })).toHaveTextContent('Draft');
    await user.click(within(rail).getByRole('button', { name: /^Question 1/ }));
    expect(await screen.findByRole('textbox', { name: Q1 })).toHaveValue(FIRST_RAW);

    // Another tab and back: the draft is still in memory.
    unmount();
    render(tree());
    expect(await screen.findByRole('textbox', { name: Q1 })).toHaveValue(FIRST_RAW);
    expect(storageText(window.localStorage) + storageText(window.sessionStorage)).not.toContain('PRIVATE-');
    expect(cacheText(appData)).not.toContain('PRIVATE-');

    // A session-expiry redirect drops it.
    appData.auth.handleUnauthenticated();
    await waitFor(() => {
      expect(appData.navigate).toHaveBeenCalled();
    });
    expect(draftStoreFor(appData).get(INTERVIEW_ID).size).toBe(0);
  });

  it('asks before sending an interview with questions left unanswered', async () => {
    const { interview } = setup();
    const { user } = renderTab();
    await answer(user, Q1, FIRST_RAW);
    await screen.findByRole('heading', { level: 3, name: Q2 });
    const rail = screen.getByRole('navigation', { name: 'Interview questions' });
    await user.click(within(rail).getByRole('button', { name: /^Check and send/ }));
    expect(await screen.findByText('Not answered: it stays empty if you send now.')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Send 1 answer' }));
    const dialog = await screen.findByRole('alertdialog', { name: 'Send 1 of 2 answers?' });
    expect(dialog).toHaveAccessibleDescription(
      'Question 2 isn’t confirmed, so it stays unanswered. The interview takes no more answers once these are sent.',
    );
    await user.click(within(dialog).getByRole('button', { name: 'Send 1 answer' }));
    expect(await screen.findByText('Answers sent')).toBeInTheDocument();
    expect(interview.calls.answers[0]?.body.answers).toEqual([{ questionId: 'q1', text: FIRST_WORDING, redactionFindings: 0 }]);
  });

  it('skips the interview after asking, with the version the owner saw', async () => {
    const { interview } = setup();
    const { user } = renderTab({ viewport: 'mobile' });
    await user.click(await screen.findByRole('button', { name: 'Skip the interview' }));
    const dialog = await screen.findByRole('alertdialog', { name: 'Skip this interview?' });
    await user.click(within(dialog).getByRole('button', { name: 'Skip the interview' }));
    expect(await screen.findByText('Interview skipped')).toBeInTheDocument();
    expect(interview.calls.skip).toEqual([{ interviewId: INTERVIEW_ID, ifMatch: '"etag-1"' }]);
    expect(screen.getByText('Skipped')).toBeInTheDocument();
  });

  it('opens the conflict dialog when the interview changed meanwhile, and keeps mine with the current version', async () => {
    const { interview } = setup();
    const { user } = renderTab();
    await answer(user, Q1, FIRST_RAW);
    await answer(user, Q2, SECOND_RAW);
    // The expiry job ran while the owner was answering.
    interview.set([interviewEntry({ status: 'expired', updatedAt: '2026-11-26T23:00:01.000000Z' }, '"etag-9"')]);
    await user.click(await screen.findByRole('button', { name: 'Send 2 answers' }));

    const dialog = await screen.findByRole('alertdialog', { name: 'This interview changed since you opened it' });
    // The comparison arrives with the saved version (no answers there yet).
    expect(await within(dialog).findByRole('group', { name: 'Your version compared with the saved one' })).toBeInTheDocument();
    await user.click(within(dialog).getByRole('button', { name: 'Keep mine' }));
    expect(await screen.findByText('Answers sent')).toBeInTheDocument();
    expect(interview.calls.answers.map((call) => call.ifMatch)).toEqual(['"etag-1"', '"etag-9"']);
  });

  it('sends a refused answer back to its wording and says which question needs a fix', async () => {
    const { flow } = setup({
      interviews: [interviewEntry()],
      answers: (_call, count) =>
        count === 1
          ? problemResponse(422, 'confidential_text', {
              detail: 'Some text matches the blocklist. Rephrase it and try again.',
              findings: [
                { field: 'answers[1].text', code: 'blocklisted-term', severity: 'block', message: MESSAGES.blocklisted, start: 0, end: 5, ref: 'term-1' },
              ],
            })
          : undefined,
    });
    server.use(
      ...intakeHandlers({
        redact: (body) =>
          redactionProposal({ findings: [], proposal: body.text.startsWith('PRIVATE-ONE') ? FIRST_WORDING : SECOND_WORDING }),
        check: (text, count) =>
          count > 2 && text === SECOND_WORDING
            ? checkResult({ ok: false, findings: [findingAt(text, 'Start', 'blocklisted-term', 'block', MESSAGES.blocklisted)] })
            : checkResult(),
      }).handlers,
    );
    const { user } = renderTab();
    await answer(user, Q1, FIRST_RAW);
    await answer(user, Q2, SECOND_RAW);
    await user.click(await screen.findByRole('button', { name: 'Send 2 answers' }));

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Some wording can’t be sent yet');
    expect(alert).toHaveTextContent('Question 2 still has details that block saving.');
    await waitFor(() => {
      expect(screen.getByRole('heading', { level: 3, name: Q2 })).toHaveFocus();
    });
    expect(screen.getByRole('textbox', { name: 'Wording to keep' })).toHaveValue(SECOND_WORDING);
    expect(await screen.findByText('1 detail to fix before you can use it.')).toBeInTheDocument();
    const rail = screen.getByRole('navigation', { name: 'Interview questions' });
    expect(within(rail).getByRole('button', { name: /^Question 2/ })).toHaveTextContent('Needs a fix');
    expect(flow.calls.redact).toHaveLength(0);
  });

  it('still takes late answers once time ran out', async () => {
    setup({ interviews: [interviewEntry({ status: 'expired' })] });
    renderTab();
    expect(await screen.findByText(/^Time ran out on /)).toBeInTheDocument();
    expect(screen.getByText(/Late answers still count/)).toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: Q1 })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Skip the interview' })).toBeEnabled();
  });

  it('shows an answered interview read-only, with earlier interviews folded away', async () => {
    const answered = interviewEntry({
      status: 'answered',
      statusChangedAt: '2026-11-25T09:30:00.000000Z',
      answers: [{ questionId: 'q1', text: FIRST_WORDING, confirmedAt: '2026-11-25T09:30:00.000000Z', redactionFindings: 2 }],
    });
    const older = interviewEntry({
      id: 'intv-older',
      mode: 'full',
      questions: FULL_QUESTIONS,
      status: 'skipped',
      createdAt: '2026-11-10T09:00:00.000000Z',
    });
    setup({ interviews: [answered, older] });
    const { user } = renderTab();
    expect(await screen.findByRole('heading', { level: 2, name: 'Your interview' })).toBeInTheDocument();
    expect(screen.getByText('Answered')).toBeInTheDocument();
    expect(screen.getByText(FIRST_WORDING)).toBeInTheDocument();
    expect(screen.getByText('Not answered.')).toBeInTheDocument();
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Skip the interview' })).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Show the earlier interview' }));
    expect(await screen.findByRole('heading', { level: 3, name: /^Interview of / })).toBeInTheDocument();
    expect(screen.getByText('Skipped')).toBeInTheDocument();
  });

  it('says why there are no questions yet', async () => {
    setup({ interviews: [], article: articleFixture({ status: 'researching', interviewId: null }) });
    renderTab();
    expect(await screen.findByRole('heading', { level: 2, name: 'No questions yet.' })).toBeInTheDocument();
    expect(await screen.findByText('Research is running. Your questions arrive when it’s done, with a notification.')).toBeInTheDocument();
    expect(screen.getByText('Your part')).toBeInTheDocument();
  });

  it('says what stopped the pipeline before the questions', async () => {
    setup({
      interviews: [],
      article: articleFixture({ status: 'needs_attention', interviewId: null, lastError: 'ai_use_not_confirmed' }),
    });
    renderTab();
    expect(
      await screen.findByText(
        'The pipeline stopped before your questions were written. Confirm your employer’s AI-use policy in Settings > Blocklist & policy first.',
      ),
    ).toBeInTheDocument();
  });

  it('lists a standalone post’s interviews by post', async () => {
    const { interview } = setup({ interviews: [] });
    server.use(http.get(apiUrl('/api/posts/:id'), () => problem(404, 'not_found')));
    renderTab({ pieceId: 'post-0193f2a1b2c3d4e5f60718293a4d' });
    expect(await screen.findByText(/^Interviews come with articles\./)).toBeInTheDocument();
    expect(interview.calls.lists[0]?.get('postId')).toBe('post-0193f2a1b2c3d4e5f60718293a4d');
    expect(interview.calls.lists[0]?.get('articleId')).toBeNull();
  });

  it('shows a load failure with its request id and loads again', async () => {
    setup({ interviews: [interviewEntry()], list: (count) => (count <= 4 ? problem(500, 'internal_error', 'req-500') : undefined) });
    const { user } = renderTab();
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Couldn’t load the interview.');
    expect(alert).toHaveTextContent('req-500');
    await user.click(within(alert).getByRole('button', { name: 'Try loading the interview again' }));
    expect(await screen.findByRole('heading', { level: 2, name: 'Two quick questions' })).toBeInTheDocument();
  });

  it('opens from a push link as a whole page, with the actions within thumb reach', async () => {
    // Other parts of the page (the piece header) may ask for more; those answer 404 here.
    server.use(http.get(apiUrl('/api/*'), () => problem(404, 'not_found')));
    setup();
    const { user } = renderApp(`/studio/${ARTICLE_ID}/interview`, { viewport: 'mobile' });
    expect(await screen.findByRole('heading', { level: 2, name: 'Two quick questions' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Interview' })).toHaveAttribute('aria-selected', 'true');
    const action = screen.getByRole('button', { name: 'Make it public-safe' });
    expect(action.closest('[data-slot="intake-action-bar"]')).toHaveClass('sticky', 'bottom-[calc(4rem+env(safe-area-inset-bottom))]');

    await user.click(screen.getByRole('button', { name: /^All questions/ }));
    const sheet = await screen.findByRole('dialog', { name: 'Questions' });
    expect(sheet).toHaveClass('bottom-0');
    await user.click(within(sheet).getByRole('button', { name: /^Question 2/ }));
    await waitFor(() => {
      expect(screen.getByRole('heading', { level: 3, name: Q2 })).toHaveFocus();
    });
  });
});
