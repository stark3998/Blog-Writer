// @vitest-environment node
import { describe, expect, it } from 'vitest';

import { EMPTY_INTAKE, withConfirmed, withRaw, withRedaction, type IntakeState } from '@/features/intake';
import { redactionProposal } from '@/features/intake/testing/fixtures';
import { ApiError, kindForStatus, parseProblem } from '@/lib/api/errors';

import { createDraftStore } from './draft-store';
import {
  answersText,
  answersToSend,
  firstPosition,
  formatMoment,
  isOpenInterview,
  newerEntry,
  nextPosition,
  pickInterviews,
  questionProgress,
  refusedQuestionIds,
} from './interview-model';
import { FULL_QUESTIONS, interviewEntry, QUESTIONS } from './testing/fixtures';

function confirmedState(text: string): IntakeState {
  const raw = `raw ${text}`;
  return withConfirmed(withRedaction(withRaw(EMPTY_INTAKE, raw), raw, redactionProposal({ findings: [] })), text, []);
}

function drafts(entries: Record<string, IntakeState>) {
  return new Map(Object.entries(entries));
}

describe('picking the interview', () => {
  it('prefers the article’s own interview, then the newest open one, then the newest', () => {
    const newest = interviewEntry({ id: 'intv-3', status: 'answered' });
    const open = interviewEntry({ id: 'intv-2', status: 'expired' });
    const oldest = interviewEntry({ id: 'intv-1', status: 'skipped' });
    expect(pickInterviews([newest, open, oldest], 'intv-1').current).toBe(oldest);
    expect(pickInterviews([newest, open, oldest]).current).toBe(open);
    expect(pickInterviews([newest, oldest]).current).toBe(newest);
    expect(pickInterviews([newest, open, oldest]).earlier).toEqual([newest, oldest]);
    expect(pickInterviews([]).current).toBeUndefined();
  });

  it('keeps the newer of two versions of the same interview', () => {
    const listed = interviewEntry({ updatedAt: '2026-11-25T09:00:00.000000Z' });
    const saved = interviewEntry({ status: 'answered', updatedAt: '2026-11-25T09:30:00.000000Z' }, '"etag-2"');
    expect(newerEntry(listed, saved)).toBe(saved);
    expect(newerEntry(saved, listed)).toBe(saved);
    expect(newerEntry(listed, interviewEntry({ id: 'intv-other', updatedAt: '2027-01-01T00:00:00.000000Z' }))).toBe(listed);
    expect(newerEntry(listed, undefined)).toBe(listed);
  });

  it('knows which statuses still take answers', () => {
    expect(isOpenInterview('pending')).toBe(true);
    expect(isOpenInterview('expired')).toBe(true);
    expect(isOpenInterview('answered')).toBe(false);
    expect(isOpenInterview('skipped')).toBe(false);
  });
});

describe('answers and positions', () => {
  it('sends only confirmed wording, in question order', () => {
    const current = drafts({
      q2: confirmedState('Second.'),
      q1: withRaw(EMPTY_INTAKE, 'raw words only'),
      q3: confirmedState('Third.'),
    });
    expect(answersToSend(FULL_QUESTIONS, current)).toEqual([
      { questionId: 'q2', text: 'Second.', redactionFindings: 0 },
      { questionId: 'q3', text: 'Third.', redactionFindings: 0 },
    ]);
  });

  it('starts at the first unconfirmed question and moves on to the next one, then to sending', () => {
    expect(firstPosition(QUESTIONS, drafts({}))).toBe(0);
    expect(firstPosition(QUESTIONS, drafts({ q1: confirmedState('One.') }))).toBe(1);
    expect(firstPosition(QUESTIONS, drafts({ q1: confirmedState('One.'), q2: confirmedState('Two.') }))).toBe('send');
    expect(firstPosition([], drafts({}))).toBe('send');
    const partly = drafts({ q1: confirmedState('One.'), q3: confirmedState('Three.') });
    expect(nextPosition(FULL_QUESTIONS, partly, 0)).toBe(1);
    expect(nextPosition(FULL_QUESTIONS, partly, 2)).toBe(3);
    expect(nextPosition(QUESTIONS, drafts({ q2: confirmedState('Two.') }), 1)).toBe('send');
  });

  it('marks the questions the server refused until they are confirmed again', () => {
    const fix = new Set(['q2']);
    expect(questionProgress(drafts({ q2: withRaw(EMPTY_INTAKE, 'x') }), 'q2', fix)).toBe('fix');
    expect(questionProgress(drafts({ q2: confirmedState('Two.') }), 'q2', fix)).toBe('confirmed');
    expect(questionProgress(drafts({}), 'q1', fix)).toBe('empty');
  });

  it('maps a confidential_text refusal back to the questions it names', () => {
    const problem = parseProblem({
      status: 422,
      code: 'confidential_text',
      findings: [
        { field: 'answers[1].text', code: 'blocklisted-term', severity: 'block', message: 'm', start: 0, end: 3 },
        { field: 'answers.0', code: 'email-address', severity: 'block', message: 'm' },
        { field: 'answers[7].text', code: 'url', severity: 'block', message: 'm' },
      ],
    });
    const error = new ApiError({ kind: kindForStatus(422), status: 422, problem });
    const sent = [
      { questionId: 'q1', text: 'One.', redactionFindings: 0 },
      { questionId: 'q3', text: 'Three.', redactionFindings: 0 },
    ];
    expect(refusedQuestionIds(error, sent)).toEqual(['q3', 'q1']);
    expect(refusedQuestionIds(new Error('nope'), sent)).toEqual([]);
  });

  it('writes answers out for the conflict dialog’s comparison', () => {
    expect(answersText(QUESTIONS, [{ questionId: 'q2', text: 'Inventory first.' }])).toBe(
      `1. ${QUESTIONS[0]?.text ?? ''}\n\n(not answered)\n\n2. ${QUESTIONS[1]?.text ?? ''}\n\nInventory first.`,
    );
  });

  it('formats a moment, and ignores missing or unreadable ones', () => {
    expect(formatMoment('2026-11-26T23:00:00.000000Z')).toMatch(/Nov/);
    expect(formatMoment(null)).toBeUndefined();
    expect(formatMoment('not a date')).toBeUndefined();
  });
});

describe('createDraftStore', () => {
  it('keeps one snapshot per interview until it changes, and forgets on request', () => {
    const store = createDraftStore();
    let notified = 0;
    const stop = store.subscribe(() => {
      notified += 1;
    });
    const empty = store.get('intv-1');
    expect(store.get('intv-1')).toBe(empty);
    store.update('intv-1', 'q1', (current) => withRaw(current, 'draft'));
    const first = store.get('intv-1');
    expect(first).not.toBe(empty);
    expect(first.get('q1')?.raw).toBe('draft');
    expect(store.get('intv-2').size).toBe(0);
    // No change, no new snapshot, no notification.
    store.update('intv-1', 'q1', (current) => current);
    expect(store.get('intv-1')).toBe(first);
    expect(notified).toBe(1);
    store.update('intv-2', 'q1', withRaw(EMPTY_INTAKE, 'other'));
    store.clear('intv-1');
    expect(store.get('intv-1').size).toBe(0);
    expect(store.get('intv-2').size).toBe(1);
    store.clearAll();
    expect(store.get('intv-2').size).toBe(0);
    expect(notified).toBe(4);
    stop();
    store.update('intv-3', 'q1', withRaw(EMPTY_INTAKE, 'unseen'));
    expect(notified).toBe(4);
  });
});
