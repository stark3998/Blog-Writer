import type { ErrorMap } from '../lib/errors';
import { isSlug, parseWhole, repeatedIndexes, SLUG_HINT, wholeError } from '../lib/rules';
import type { FormatPack, FormatPacks } from '../lib/types';

export type OwnerInput = FormatPack['ownerInput'];

/** `FormatPack` limits (`blogwriter.domain.settings`). */
export const PACK_LIMITS = {
  id: 40,
  name: 80,
  minWords: 100,
  maxWords: 20_000,
  minQuestions: 1,
  maxQuestions: 12,
  items: 20,
  itemLength: 200,
} as const;

export interface PackDraft {
  readonly key: string;
  readonly saved: boolean;
  readonly id: string;
  readonly name: string;
  readonly minWords: string;
  readonly maxWords: string;
  readonly mustContain: readonly string[];
  readonly ownerInput: OwnerInput;
  readonly minQuestions: string;
  readonly maxQuestions: string;
  readonly banned: readonly string[];
  readonly extra: Readonly<Record<string, unknown>>;
}

export interface FormatsDraft {
  readonly packs: readonly PackDraft[];
}

const KNOWN = new Set(['id', 'name', 'minWords', 'maxWords', 'mustContain', 'ownerInput', 'minQuestions', 'maxQuestions', 'banned']);

function packToDraft(pack: FormatPack): PackDraft {
  return {
    key: `saved:${pack.id}`,
    saved: true,
    id: pack.id,
    name: pack.name,
    minWords: String(pack.minWords),
    maxWords: String(pack.maxWords),
    mustContain: pack.mustContain ?? [],
    ownerInput: pack.ownerInput,
    minQuestions: String(pack.minQuestions),
    maxQuestions: String(pack.maxQuestions),
    banned: pack.banned ?? [],
    extra: Object.fromEntries(Object.entries(pack).filter(([field]) => !KNOWN.has(field))),
  };
}

/** The editor's starting point from `GET /api/kb/formats` (module-level for `useSettingsDraft`). */
export function formatsToDraft(doc: FormatPacks): FormatsDraft {
  return { packs: (doc.packs ?? []).map(packToDraft) };
}

let added = 0;

export function newPack(): PackDraft {
  added += 1;
  return {
    key: `new:${String(added)}`,
    saved: false,
    id: '',
    name: '',
    minWords: '1200',
    maxWords: '2000',
    mustContain: [],
    ownerInput: 'micro_interview',
    minQuestions: '2',
    maxQuestions: '2',
    banned: [],
    extra: {},
  };
}

/** `PUT /api/kb/formats`. */
export function formatsToBody(draft: FormatsDraft): { packs: FormatPack[] } {
  return {
    packs: draft.packs.map((pack) => ({
      ...pack.extra,
      id: pack.id.trim(),
      name: pack.name.trim(),
      minWords: parseWhole(pack.minWords) ?? 0,
      maxWords: parseWhole(pack.maxWords) ?? 0,
      mustContain: [...pack.mustContain],
      ownerInput: pack.ownerInput,
      minQuestions: parseWhole(pack.minQuestions) ?? 0,
      maxQuestions: parseWhole(pack.maxQuestions) ?? 0,
      banned: [...pack.banned],
    })),
  };
}

/** What the server would refuse, by request path (`packs.0.maxWords`). */
export function validateFormats(draft: FormatsDraft): ErrorMap {
  const errors: Record<string, string> = {};
  const set = (path: string, message: string | undefined) => {
    if (message !== undefined && !(path in errors)) errors[path] = message;
  };
  if (draft.packs.length === 0) set('packs', 'Keep at least one format.');
  const repeated = repeatedIndexes(draft.packs.map((pack) => pack.id.trim()));
  draft.packs.forEach((pack, index) => {
    const base = `packs.${String(index)}`;
    const id = pack.id.trim();
    if (id === '') set(`${base}.id`, 'Give the format an id.');
    else if (!isSlug(id, PACK_LIMITS.id)) set(`${base}.id`, SLUG_HINT);
    else if (repeated.has(index)) set(`${base}.id`, 'Another format has this id.');
    if (pack.name.trim() === '') set(`${base}.name`, 'Give the format a name.');
    else if (pack.name.trim().length > PACK_LIMITS.name) set(`${base}.name`, `At most ${String(PACK_LIMITS.name)} characters.`);

    set(`${base}.minWords`, wholeError(pack.minWords, PACK_LIMITS.minWords, PACK_LIMITS.maxWords));
    set(`${base}.maxWords`, wholeError(pack.maxWords, PACK_LIMITS.minWords, PACK_LIMITS.maxWords));
    const minWords = parseWhole(pack.minWords);
    const maxWords = parseWhole(pack.maxWords);
    if (minWords !== undefined && maxWords !== undefined && minWords > maxWords) {
      set(`${base}.maxWords`, 'The longest is shorter than the shortest.');
    }

    set(`${base}.minQuestions`, wholeError(pack.minQuestions, PACK_LIMITS.minQuestions, PACK_LIMITS.maxQuestions));
    set(`${base}.maxQuestions`, wholeError(pack.maxQuestions, PACK_LIMITS.minQuestions, PACK_LIMITS.maxQuestions));
    const minQuestions = parseWhole(pack.minQuestions);
    const maxQuestions = parseWhole(pack.maxQuestions);
    if (minQuestions !== undefined && maxQuestions !== undefined && minQuestions > maxQuestions) {
      set(`${base}.maxQuestions`, 'Fewer than the fewest questions.');
    }

    if (pack.mustContain.length > PACK_LIMITS.items) set(`${base}.mustContain`, `At most ${String(PACK_LIMITS.items)} parts.`);
    for (const position of repeatedIndexes(pack.mustContain)) set(`${base}.mustContain.${String(position)}`, 'Listed twice.');
    if (pack.banned.length > PACK_LIMITS.items) set(`${base}.banned`, `At most ${String(PACK_LIMITS.items)} items.`);
    for (const position of repeatedIndexes(pack.banned)) set(`${base}.banned.${String(position)}`, 'Listed twice.');
  });
  return errors;
}

/** Plain text of the formats, for the conflict dialog's comparison. */
export function describeFormats(draft: FormatsDraft): string {
  return draft.packs
    .map((pack) =>
      [
        `${pack.name} (${pack.id}): ${pack.minWords}-${pack.maxWords} words`,
        `Owner input: ${pack.ownerInput === 'full_interview' ? 'full interview' : 'micro-interview'}, ${pack.minQuestions}-${pack.maxQuestions} questions`,
        pack.mustContain.length > 0 ? `Must contain: ${pack.mustContain.join('; ')}` : '',
        pack.banned.length > 0 ? `Not allowed: ${pack.banned.join('; ')}` : '',
      ]
        .filter((line) => line !== '')
        .join('\n'),
    )
    .join('\n\n');
}
