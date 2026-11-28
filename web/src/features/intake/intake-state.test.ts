// @vitest-environment node
import { describe, expect, it } from 'vitest';

import {
  backToWriting,
  EMPTY_INTAKE,
  hasCurrentReview,
  hasUnsavedText,
  intakeProgress,
  reopenReview,
  withConfirmed,
  withRaw,
  withRedaction,
  withWording,
} from './intake-state';
import { PROPOSED_WORDING, RAW_ANSWER, RAW_FINDINGS, redactionProposal } from './testing/fixtures';

describe('intake state', () => {
  it('goes from raw words to a review, an edited wording and a confirmation', () => {
    const written = withRaw(EMPTY_INTAKE, RAW_ANSWER);
    expect(intakeProgress(written)).toBe('draft');
    const reviewed = withRedaction(written, RAW_ANSWER, redactionProposal());
    expect(reviewed.stage).toBe('review');
    expect(reviewed.review?.wording).toBe(PROPOSED_WORDING);
    expect(intakeProgress(reviewed)).toBe('review');
    const edited = withWording(reviewed, 'We moved thousands of apps.');
    const confirmed = withConfirmed(edited, 'We moved thousands of apps.', [
      { ...RAW_FINDINGS[0], code: 'possible-name', severity: 'warn', message: 'Name?', overridable: true },
    ]);
    expect(confirmed.stage).toBe('confirmed');
    expect(confirmed.confirmed).toEqual({
      text: 'We moved thousands of apps.',
      redactionFindings: 3,
      warnings: [expect.objectContaining({ code: 'possible-name' })],
    });
    expect(intakeProgress(confirmed)).toBe('confirmed');
  });

  it('ignores a redaction of text the owner has changed since', () => {
    const written = withRaw(EMPTY_INTAKE, 'Second thoughts');
    expect(withRedaction(written, RAW_ANSWER, redactionProposal())).toBe(written);
  });

  it('starts from the masked words when no proposal came back', () => {
    const reviewed = withRedaction(withRaw(EMPTY_INTAKE, RAW_ANSWER), RAW_ANSWER, redactionProposal({ proposal: null, reason: 'strict_mode', anonymizerUsed: false }));
    expect(reviewed.review?.wording).toBe('We moved 2,137 apps for Northwind Bank. Ask [email address] for the runbook.');
  });

  it('keeps the review while the owner goes back to unchanged words, and drops it once they change', () => {
    const reviewed = withRedaction(withRaw(EMPTY_INTAKE, RAW_ANSWER), RAW_ANSWER, redactionProposal());
    const back = backToWriting(withWording(reviewed, 'Edited'));
    expect(back.stage).toBe('write');
    expect(hasCurrentReview(back)).toBe(true);
    expect(reopenReview(back).review?.wording).toBe('Edited');
    const changed = withRaw(back, `${RAW_ANSWER} More.`);
    expect(hasCurrentReview(changed)).toBe(false);
    expect(reopenReview(changed)).toBe(changed);
  });

  it('reopens a confirmed wording for another edit', () => {
    const reviewed = withRedaction(withRaw(EMPTY_INTAKE, RAW_ANSWER), RAW_ANSWER, redactionProposal());
    const confirmed = withConfirmed(reviewed, 'Final wording', []);
    const reopened = reopenReview(confirmed);
    expect(reopened.stage).toBe('review');
    expect(reopened.confirmed).toBeNull();
    expect(reopened.review?.wording).toBe('Final wording');
  });

  it('knows when there is something to lose', () => {
    expect(hasUnsavedText(undefined)).toBe(false);
    expect(hasUnsavedText(withRaw(EMPTY_INTAKE, '   '))).toBe(false);
    expect(hasUnsavedText(withRaw(EMPTY_INTAKE, 'Words'))).toBe(true);
  });
});
