import { isBlocking, maskBlocking, redactionCount } from './findings';
import type { Finding, ProposalReason, RedactionProposal } from './types';

/**
 * The intake flow as data: raw text -> server redaction and checks -> the owner confirms or
 * edits the public-safe wording -> only that wording leaves the flow.
 *
 * An `IntakeState` holds private raw text, so it lives in component state or the interview's
 * in-memory draft store only: never in web storage, the query cache, a URL or a log (contract
 * 1.9 rule 9). Every function here is pure and returns a new state.
 */

/** Where the owner is: writing (or dictating), reviewing the proposal, or done. */
export type IntakeStage = 'write' | 'review' | 'confirmed';

/** What the redact call answered for one version of the raw text, plus the owner's edits. */
export interface IntakeReview {
  /** The raw text the findings index (their spans point into exactly this text). */
  readonly raw: string;
  readonly findings: readonly Finding[];
  /** The anonymizer's wording, or `null` with the `reason` it proposed none. */
  readonly proposal: string | null;
  readonly reason: ProposalReason | null;
  readonly anonymizerUsed: boolean;
  /** The public-safe wording as the owner edits it: the proposal, or the masked raw text. */
  readonly wording: string;
}

/** The wording the owner confirmed and the check passed: the only text that is ever saved. */
export interface ConfirmedWording {
  readonly text: string;
  /** How many details redaction flagged in the raw text (a count, never the spans). */
  readonly redactionFindings: number;
  /** Findings the check reported on the confirmed text that do not block it (names, figures). */
  readonly warnings: readonly Finding[];
}

export interface IntakeState {
  readonly stage: IntakeStage;
  /** The owner's raw words. Private: memory only. */
  readonly raw: string;
  /** The last redaction; kept while the owner goes back to the raw text, dropped when it changes. */
  readonly review: IntakeReview | null;
  /** Set only in the `confirmed` stage. */
  readonly confirmed: ConfirmedWording | null;
}

export const EMPTY_INTAKE: IntakeState = { stage: 'write', raw: '', review: null, confirmed: null };

/** The owner typed or dictated: back to writing; a redaction of other text no longer applies. */
export function withRaw(state: IntakeState, raw: string): IntakeState {
  if (state.raw === raw && state.stage === 'write') return state;
  return { stage: 'write', raw, review: state.review, confirmed: null };
}

/** The wording to start from: the proposal, else the raw text with every blocking detail masked. */
export function startingWording(raw: string, proposal: Pick<RedactionProposal, 'findings' | 'proposal'>): string {
  return proposal.proposal ?? maskBlocking(raw, proposal.findings);
}

/** The redaction of `raw` came back: review it. Ignored when the raw text changed meanwhile. */
export function withRedaction(state: IntakeState, raw: string, proposal: RedactionProposal): IntakeState {
  if (state.raw !== raw) return state;
  return {
    stage: 'review',
    raw,
    review: {
      raw,
      findings: proposal.findings,
      proposal: proposal.proposal,
      reason: proposal.reason,
      anonymizerUsed: proposal.anonymizerUsed,
      wording: startingWording(raw, proposal),
    },
    confirmed: null,
  };
}

/** True when the last redaction is of the current raw text, so review can reopen without a new call. */
export function hasCurrentReview(state: IntakeState): state is IntakeState & { review: IntakeReview } {
  return state.review !== null && state.review.raw === state.raw;
}

/** The owner edits the public-safe wording. */
export function withWording(state: IntakeState, wording: string): IntakeState {
  if (!state.review) return state;
  return { ...state, stage: 'review', review: { ...state.review, wording }, confirmed: null };
}

/** Back to the raw text ("Edit my words"); the review stays until the raw text changes. */
export function backToWriting(state: IntakeState): IntakeState {
  return { ...state, stage: 'write', confirmed: null };
}

/**
 * Review again: from a confirmed answer (its wording becomes the draft again) or from writing
 * when the raw text has not changed since the last redaction. Otherwise nothing changes.
 */
export function reopenReview(state: IntakeState): IntakeState {
  if (state.stage === 'confirmed' && state.confirmed && state.review) {
    return { ...state, stage: 'review', review: { ...state.review, wording: state.confirmed.text }, confirmed: null };
  }
  if (hasCurrentReview(state)) return { ...state, stage: 'review', confirmed: null };
  return state;
}

/** The check passed on `text`: the wording is confirmed. */
export function withConfirmed(state: IntakeState, text: string, checkFindings: readonly Finding[]): IntakeState {
  if (!state.review) return state;
  return {
    ...state,
    stage: 'confirmed',
    review: { ...state.review, wording: text },
    confirmed: {
      text,
      redactionFindings: redactionCount(state.review.findings),
      warnings: checkFindings.filter((finding) => !isBlocking(finding)),
    },
  };
}

/** A short state for lists ("Confirmed", "Draft", "Not started"). */
export type IntakeProgress = 'empty' | 'draft' | 'review' | 'confirmed';

export function intakeProgress(state: IntakeState | undefined): IntakeProgress {
  if (!state) return 'empty';
  if (state.stage === 'confirmed' && state.confirmed) return 'confirmed';
  if (state.stage === 'review' && state.review) return 'review';
  return state.raw.trim() === '' ? 'empty' : 'draft';
}

/** True when the state holds anything the owner would lose (raw words or a wording). */
export function hasUnsavedText(state: IntakeState | undefined): boolean {
  return intakeProgress(state) !== 'empty';
}
