import type { PairRow } from '../components/pairs-editor';
import type { ErrorMap } from '../lib/errors';
import {
  fold,
  hashtagError,
  hostError,
  isSlug,
  MAX_HASHTAGS,
  parsePercent,
  percentText,
  repeatedIndexes,
  SLUG_HINT,
} from '../lib/rules';
import type { Pillar, PillarInput, PillarSettings } from '../lib/types';

/** `PillarSettings`: one to five pillars. */
export const MAX_PILLARS = 5;
export const LIMITS = {
  id: 40,
  name: 80,
  topics: 300,
  description: 2000,
  audience: 1000,
  term: 80,
  vocabulary: 200,
  renames: 200,
  renameName: 100,
  domains: 50,
} as const;

/** One pillar as the editor holds it: text as typed, the share as the percentage typed. */
export interface PillarDraft {
  /** A stable React key: `saved:<id>` for stored pillars, `new:<n>` for added ones. */
  readonly key: string;
  /** Stored on the server: its id is referenced elsewhere and cannot change. */
  readonly saved: boolean;
  readonly id: string;
  readonly name: string;
  readonly topics: string;
  readonly description: string;
  readonly audience: string;
  readonly vocabulary: readonly string[];
  readonly renames: readonly PairRow[];
  readonly hashtags: readonly string[];
  readonly researchDomains: readonly string[];
  /** The target share as a percentage, as typed ("33"). */
  readonly share: string;
  /** Fields this version of the app does not know, sent back unchanged. */
  readonly extra: Readonly<Record<string, unknown>>;
}

export interface PillarsDraft {
  readonly pillars: readonly PillarDraft[];
}

const KNOWN_FIELDS = new Set([
  'id',
  'name',
  'topics',
  'description',
  'audience',
  'vocabulary',
  'renames',
  'hashtags',
  'researchDomains',
  'targetShare',
]);

export function pillarToDraft(pillar: Pillar): PillarDraft {
  return {
    key: `saved:${pillar.id}`,
    saved: true,
    id: pillar.id,
    name: pillar.name,
    topics: pillar.topics,
    description: pillar.description,
    audience: pillar.audience,
    vocabulary: pillar.vocabulary ?? [],
    renames: Object.entries(pillar.renames ?? {}).map(([from, to]) => ({ from, to })),
    hashtags: pillar.hashtags ?? [],
    researchDomains: pillar.researchDomains ?? [],
    share: percentText(pillar.targetShare),
    extra: Object.fromEntries(Object.entries(pillar).filter(([field]) => !KNOWN_FIELDS.has(field))),
  };
}

/** The editor's starting point from `GET /api/kb/pillars` (module-level: `useSettingsDraft` needs a stable function). */
export function pillarsToDraft(settings: PillarSettings): PillarsDraft {
  return { pillars: (settings.pillars ?? []).map(pillarToDraft) };
}

let newPillars = 0;

/** A blank pillar for "Add a pillar". */
export function newPillar(): PillarDraft {
  newPillars += 1;
  return {
    key: `new:${String(newPillars)}`,
    saved: false,
    id: '',
    name: '',
    topics: '',
    description: '',
    audience: '',
    vocabulary: [],
    renames: [],
    hashtags: [],
    researchDomains: [],
    share: '0',
    extra: {},
  };
}

function filledRenames(rows: readonly PairRow[]): PairRow[] {
  return rows.filter((row) => row.from.trim() !== '' || row.to.trim() !== '');
}

/** `PUT /api/kb/pillars`: the whole list, as typed (the server trims and validates again). */
export function draftToBody(draft: PillarsDraft): { pillars: PillarInput[] } {
  return {
    pillars: draft.pillars.map((pillar) => ({
      ...pillar.extra,
      id: pillar.id.trim(),
      name: pillar.name.trim(),
      topics: pillar.topics.trim(),
      description: pillar.description.trim(),
      audience: pillar.audience.trim(),
      vocabulary: [...pillar.vocabulary],
      renames: Object.fromEntries(filledRenames(pillar.renames).map((row) => [row.from.trim(), row.to.trim()])),
      hashtags: [...pillar.hashtags],
      researchDomains: [...pillar.researchDomains],
      targetShare: parsePercent(pillar.share) ?? 0,
    })),
  };
}

/** The sum of the target shares that can be read (0-1). */
export function shareTotal(pillars: readonly PillarDraft[]): number {
  return pillars.reduce((sum, pillar) => sum + (parsePercent(pillar.share) ?? 0), 0);
}

function lengthError(value: string, max: number, label: string): string | undefined {
  return value.trim().length > max ? `${label} has at most ${String(max)} characters.` : undefined;
}

/**
 * What would stop the server from saving the pillars, by request path (`pillars.0.name`), so
 * each control shows its own message before the owner presses Save. Mirrors the domain model and
 * `kb.checks.pillar_problems`.
 */
export function validatePillars(draft: PillarsDraft): ErrorMap {
  const errors: Record<string, string> = {};
  const set = (path: string, message: string | undefined) => {
    if (message !== undefined && !(path in errors)) errors[path] = message;
  };
  const { pillars } = draft;
  if (pillars.length === 0) set('pillars', 'Keep at least one pillar.');
  if (pillars.length > MAX_PILLARS) set('pillars', `At most ${String(MAX_PILLARS)} pillars.`);
  if (shareTotal(pillars) > 1 + 1e-9) set('pillars', 'The target shares add up to more than 100%.');

  const ids = pillars.map((pillar) => pillar.id.trim());
  const repeatedIds = repeatedIndexes(ids);
  // A hashtag's first spelling wins across pillars (LinkedIn treats #ZeroTrust and #zerotrust alike).
  const spelling = new Map<string, string>();

  pillars.forEach((pillar, index) => {
    const base = `pillars.${String(index)}`;
    const id = pillar.id.trim();
    if (id === '') set(`${base}.id`, 'Give the pillar an id.');
    else if (!isSlug(id, LIMITS.id)) set(`${base}.id`, SLUG_HINT);
    else if (repeatedIds.has(index)) set(`${base}.id`, 'Another pillar has this id.');

    if (pillar.name.trim() === '') set(`${base}.name`, 'Give the pillar a name.');
    set(`${base}.name`, lengthError(pillar.name, LIMITS.name, 'The name'));
    set(`${base}.topics`, lengthError(pillar.topics, LIMITS.topics, 'Topics'));
    set(`${base}.description`, lengthError(pillar.description, LIMITS.description, 'The description'));
    set(`${base}.audience`, lengthError(pillar.audience, LIMITS.audience, 'The audience'));

    const share = parsePercent(pillar.share);
    if (share === undefined) set(`${base}.targetShare`, 'Enter a percentage from 0 to 100.');
    else if (share > 1) set(`${base}.targetShare`, 'At most 100%.');

    if (pillar.vocabulary.length > LIMITS.vocabulary) set(`${base}.vocabulary`, `At most ${String(LIMITS.vocabulary)} terms.`);
    for (const position of repeatedIndexes(pillar.vocabulary)) set(`${base}.vocabulary.${String(position)}`, 'Listed twice.');

    if (pillar.hashtags.length > MAX_HASHTAGS) set(`${base}.hashtags`, `At most ${String(MAX_HASHTAGS)} hashtags.`);
    const repeatedTags = repeatedIndexes(pillar.hashtags);
    pillar.hashtags.forEach((tag, position) => {
      const path = `${base}.hashtags.${String(position)}`;
      set(path, hashtagError(tag));
      if (repeatedTags.has(position)) set(path, 'Listed twice.');
      const known = spelling.get(fold(tag));
      if (known === undefined) spelling.set(fold(tag), tag);
      else if (known !== tag) set(path, 'Another pillar spells this hashtag with different capitals.');
    });

    if (pillar.researchDomains.length > LIMITS.domains) set(`${base}.researchDomains`, `At most ${String(LIMITS.domains)} sites.`);
    const repeatedHosts = repeatedIndexes(pillar.researchDomains);
    pillar.researchDomains.forEach((host, position) => {
      const path = `${base}.researchDomains.${String(position)}`;
      set(path, hostError(host));
      if (repeatedHosts.has(position)) set(path, 'Listed twice.');
    });

    const renames = filledRenames(pillar.renames);
    if (renames.length > LIMITS.renames) set(`${base}.renames`, `At most ${String(LIMITS.renames)} renames.`);
    const sources = new Set(renames.map((row) => fold(row.from.trim())));
    const seenSources = new Set<string>();
    pillar.renames.forEach((row, position) => {
      const from = row.from.trim();
      const to = row.to.trim();
      if (from === '' && to === '') return;
      const path = `${base}.renames.${String(position)}`;
      if (from === '' || to === '') set(path, 'Both names are required.');
      else if (Math.max(from.length, to.length) > LIMITS.renameName) {
        set(path, `Names have at most ${String(LIMITS.renameName)} characters.`);
      } else if (fold(from) === fold(to)) set(path, 'A name cannot be renamed to itself.');
      else if (sources.has(fold(to))) set(path, 'The current name is renamed again. Rename once, to the final name.');
      else if (seenSources.has(fold(from))) set(path, 'This old name is listed twice.');
      seenSources.add(fold(from));
    });
  });
  return errors;
}

/** Plain text of the pillars, for the conflict dialog's comparison (one paragraph per pillar). */
export function describePillars(draft: PillarsDraft): string {
  return draft.pillars
    .map((pillar) =>
      [
        `${pillar.name} (${pillar.id}), target share ${pillar.share}%`,
        pillar.topics && `Topics: ${pillar.topics}`,
        pillar.description && `Description: ${pillar.description}`,
        pillar.audience && `Audience: ${pillar.audience}`,
        pillar.vocabulary.length > 0 && `Vocabulary: ${pillar.vocabulary.join(', ')}`,
        pillar.renames.length > 0 && `Renames: ${pillar.renames.map((row) => `${row.from} -> ${row.to}`).join('; ')}`,
        pillar.hashtags.length > 0 && `Hashtags: ${pillar.hashtags.map((tag) => `#${tag}`).join(' ')}`,
        pillar.researchDomains.length > 0 && `Research sites: ${pillar.researchDomains.join(', ')}`,
      ]
        .filter(Boolean)
        .join('\n'),
    )
    .join('\n\n');
}
