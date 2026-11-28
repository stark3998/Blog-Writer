import { queryKeys } from '@/lib/query/keys';

import type { PairRow } from '../components/pairs-editor';
import type { ErrorMap } from '../lib/errors';
import { fold, repeatedIndexes } from '../lib/rules';
import type { GuideRules, GuideRulesIn, GuideSuggestion, StyleGuide } from '../lib/types';

/**
 * `GET /api/voice/guide` (the active guide, every version, the sample counter). It sits under the
 * style guides' list prefix, so a job's `entity.changed` for a guide (a build, an accepted
 * suggestion) refreshes it.
 */
export const GUIDE_VIEW_KEY = [...queryKeys.styleGuides.lists(), 'view'] as const;

/** `voice.models` limits. */
export const GUIDE_LIMITS = {
  body: 50_000,
  rules: 50,
  rule: 300,
  banned: 200,
  phrase: 100,
  vocabulary: 200,
  term: 100,
  note: 300,
} as const;

export const SUGGESTION_KIND_LABEL: Readonly<Record<GuideSuggestion['kind'], string>> = {
  prefer: 'Prefer',
  avoid: 'Avoid',
  banned_phrase: 'Never use',
  vocabulary: 'Vocabulary',
};

export interface RulesDraft {
  readonly prefer: readonly string[];
  readonly avoid: readonly string[];
  readonly bannedPhrases: readonly string[];
  readonly vocabulary: readonly PairRow[];
}

export interface GuideDraft {
  readonly body: string;
  readonly rules: RulesDraft;
}

export function rulesOf(rules: GuideRules | undefined): RulesDraft {
  return {
    prefer: rules?.prefer ?? [],
    avoid: rules?.avoid ?? [],
    bannedPhrases: rules?.bannedPhrases ?? [],
    vocabulary: Object.entries(rules?.vocabulary ?? {}).map(([from, to]) => ({ from, to })),
  };
}

export function guideToDraft(guide: StyleGuide): GuideDraft {
  return { body: guide.body, rules: rulesOf(guide.rules) };
}

/** `POST /api/voice/guide/{id}/versions` (`GuideEditIn`; only the known rule lists). */
export function guideToBody(draft: GuideDraft): { body: string; rules: GuideRulesIn } {
  return {
    body: draft.body,
    rules: {
      prefer: draft.rules.prefer.map((rule) => rule.trim()),
      avoid: draft.rules.avoid.map((rule) => rule.trim()),
      bannedPhrases: draft.rules.bannedPhrases.map((phrase) => phrase.trim()),
      vocabulary: Object.fromEntries(
        draft.rules.vocabulary.filter((row) => row.from.trim() !== '').map((row) => [row.from.trim(), row.to.trim()]),
      ),
    },
  };
}

/** What the server would refuse in an edited guide, by request path (`body`, `rules.prefer.2`). */
export function validateGuide(draft: GuideDraft): ErrorMap {
  const errors: Record<string, string> = {};
  const set = (path: string, message: string) => {
    if (!(path in errors)) errors[path] = message;
  };
  if (draft.body.trim() === '') set('body', 'The guide cannot be empty.');
  if (draft.body.length > GUIDE_LIMITS.body) set('body', 'The guide is longer than 50,000 characters.');
  const lists = [
    ['prefer', draft.rules.prefer, GUIDE_LIMITS.rules],
    ['avoid', draft.rules.avoid, GUIDE_LIMITS.rules],
    ['bannedPhrases', draft.rules.bannedPhrases, GUIDE_LIMITS.banned],
  ] as const;
  for (const [name, items, max] of lists) {
    if (items.length > max) set(`rules.${name}`, `At most ${String(max)}.`);
    for (const index of repeatedIndexes(items)) set(`rules.${name}.${String(index)}`, 'Listed twice.');
  }
  const terms = draft.rules.vocabulary.map((row) => row.from);
  const repeatedTerms = repeatedIndexes(terms);
  draft.rules.vocabulary.forEach((row, index) => {
    const path = `rules.vocabulary.${String(index)}`;
    if (row.from.trim() === '' && row.to.trim() !== '') set(path, 'Name the term the note is about.');
    else if (row.from.trim().length > GUIDE_LIMITS.term) set(path, `Terms have at most ${String(GUIDE_LIMITS.term)} characters.`);
    else if (row.to.trim().length > GUIDE_LIMITS.note) set(path, `Notes have at most ${String(GUIDE_LIMITS.note)} characters.`);
    else if (repeatedTerms.has(index) && row.from.trim() !== '') set(path, 'This term is listed twice.');
  });
  if (draft.rules.vocabulary.length > GUIDE_LIMITS.vocabulary) set('rules.vocabulary', `At most ${String(GUIDE_LIMITS.vocabulary)} terms.`);
  return errors;
}

export interface ListChange {
  readonly added: readonly string[];
  readonly removed: readonly string[];
}

function listChange(before: readonly string[], after: readonly string[]): ListChange {
  const had = new Set(before.map(fold));
  const has = new Set(after.map(fold));
  return { added: after.filter((item) => !had.has(fold(item))), removed: before.filter((item) => !has.has(fold(item))) };
}

export interface RulesChange {
  readonly prefer: ListChange;
  readonly avoid: ListChange;
  readonly bannedPhrases: ListChange;
  /** Vocabulary as "term: note" lines. */
  readonly vocabulary: ListChange;
}

/** What a version adds to and removes from another version's rules. */
export function rulesChange(before: RulesDraft, after: RulesDraft): RulesChange {
  const line = (row: PairRow) => (row.to.trim() ? `${row.from}: ${row.to}` : row.from);
  return {
    prefer: listChange(before.prefer, after.prefer),
    avoid: listChange(before.avoid, after.avoid),
    bannedPhrases: listChange(before.bannedPhrases, after.bannedPhrases),
    vocabulary: listChange(before.vocabulary.map(line), after.vocabulary.map(line)),
  };
}

export function changeCount(change: RulesChange): number {
  const lists: readonly ListChange[] = [change.prefer, change.avoid, change.bannedPhrases, change.vocabulary];
  return lists.reduce((sum, list) => sum + list.added.length + list.removed.length, 0);
}

export const RULE_LIST_LABEL: Readonly<Record<keyof RulesChange, string>> = {
  prefer: 'Prefer',
  avoid: 'Avoid',
  bannedPhrases: 'Never use',
  vocabulary: 'Vocabulary',
};
