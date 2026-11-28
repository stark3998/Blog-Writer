/**
 * Text rules of the reply helper. Lengths and finding spans count Unicode code points, as the
 * server does (Python `len`), so an emoji counts once.
 */

/** Replies and comments stay under this (the plan's rule; LinkedIn allows 1,250). */
export const REPLY_LIMIT = 1000;
/** The longest draft the server takes (`MAX_DRAFT_CHARS`, LinkedIn's comment limit). */
export const DRAFT_MAX = 1250;

/** Length in code points. */
export function lengthOf(text: string): number {
  return Array.from(text).length;
}

/** The text between two code-point offsets (a finding's span). */
export function sliceCodePoints(text: string, start: number, end: number): string {
  return Array.from(text).slice(start, end).join('');
}

/** One line of a longer text for a list row: whitespace folded, cut at a word near `limit`. */
export function excerpt(text: string, limit = 140): string {
  const flat = text.replace(/\s+/gu, ' ').trim();
  const points = Array.from(flat);
  if (points.length <= limit) return flat;
  const cut = points.slice(0, limit).join('');
  const space = cut.lastIndexOf(' ');
  return `${(space > limit * 0.6 ? cut.slice(0, space) : cut).trimEnd()}…`;
}

/** The first line of a post (its hook), for "On your post: …". */
export function firstLine(text: string, limit = 90): string {
  const line = text.split(/\r?\n/u).find((part) => part.trim() !== '') ?? '';
  return excerpt(line, limit);
}

const GROUNDING_KINDS: readonly (readonly [prefix: string, label: string])[] = [
  ['story-', 'story'],
  ['pov-', 'position'],
  ['brief-', 'research brief'],
];

/** "your story", "your position and a research brief": what an angle draws on, from its ids. */
export function groundingWords(ids: readonly string[] | undefined): string | undefined {
  const labels: string[] = [];
  for (const id of ids ?? []) {
    const label = GROUNDING_KINDS.find(([prefix]) => id.startsWith(prefix))?.[1];
    if (label !== undefined && !labels.includes(label)) labels.push(label);
  }
  if (labels.length === 0) return undefined;
  const words = labels.map((label) => (label === 'research brief' ? 'a research brief' : `your ${label}`));
  return words.length === 1 ? words[0] : `${words.slice(0, -1).join(', ')} and ${words.at(-1) ?? ''}`;
}
