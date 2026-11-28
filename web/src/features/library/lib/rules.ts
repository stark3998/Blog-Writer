/**
 * Input rules the Library checks as the owner types, mirroring the server's models
 * (`blogwriter.domain.settings`, `kb.checks`). The server checks again on save; these give the
 * answer early and point at the exact control.
 */

/** Case-insensitive comparison key, like the server's `NFC` + `casefold`. */
export function fold(text: string): string {
  return text.normalize('NFC').toLocaleLowerCase('en-US');
}

/** Indexes of the values that repeat an earlier one (ignoring case). */
export function repeatedIndexes(values: readonly string[]): Set<number> {
  const seen = new Set<string>();
  const repeated = new Set<number>();
  values.forEach((value, index) => {
    const key = fold(value.trim());
    if (seen.has(key)) repeated.add(index);
    seen.add(key);
  });
  return repeated;
}

// ------------------------------------------------------------------------------------------------
// Slugs (pillar, format pack, series and tag ids)
// ------------------------------------------------------------------------------------------------

export const SLUG_PATTERN = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

export function isSlug(value: string, maxLength = 40): boolean {
  return value.length <= maxLength && SLUG_PATTERN.test(value);
}

/** "AI agents & security" becomes "ai-agents-security" (at most `maxLength` characters). */
export function slugify(text: string, maxLength = 40): string {
  const slug = text
    .normalize('NFKD')
    .replace(/\p{M}+/gu, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
  return slug.slice(0, maxLength).replace(/-+$/, '');
}

/** `base`, or `base-2`, `base-3`… when it is taken. */
export function uniqueSlug(base: string, taken: Iterable<string>, maxLength = 40): string {
  const used = new Set(taken);
  const root = base === '' ? 'item' : base;
  if (!used.has(root)) return root;
  for (let n = 2; ; n += 1) {
    const suffix = `-${String(n)}`;
    const candidate = `${root.slice(0, maxLength - suffix.length).replace(/-+$/, '')}${suffix}`;
    if (!used.has(candidate)) return candidate;
  }
}

export const SLUG_HINT = 'Lower-case letters, digits and single hyphens, such as “case-study”.';

// ------------------------------------------------------------------------------------------------
// Hashtags and research domains (pillars)
// ------------------------------------------------------------------------------------------------

/** Approved hashtags are stored without `#` and serialized as plain `#Tag` on LinkedIn. */
export const HASHTAG_PATTERN = /^[A-Za-z][A-Za-z0-9]{0,49}$/;
export const MAX_HASHTAGS = 3;

/** What the owner typed, without the `#` (so "#ZeroTrust" and "ZeroTrust" are the same tag). */
export function normalizeHashtag(input: string): string {
  return input.trim().replace(/^#+/, '').trim();
}

export function hashtagError(tag: string): string | undefined {
  if (tag === '') return 'Type a hashtag.';
  if (tag.length > 50) return 'Hashtags have at most 50 characters.';
  if (!HASHTAG_PATTERN.test(tag)) return 'Use letters and digits only, starting with a letter.';
  return undefined;
}

/** A bare lower-case host name with at least two labels, like `learn.microsoft.com`. */
export const HOST_PATTERN = /^(?=.{4,253}$)(?:(?!-)[a-z0-9-]{1,63}(?<!-)\.)+(?!-)[a-z0-9-]{1,63}(?<!-)$/;

/** The host of what the owner typed or pasted: an address (`https://…/path`) keeps its host. */
export function normalizeHost(input: string): string {
  let text = input.trim().toLowerCase();
  if (/^[a-z][a-z0-9+.-]*:\/\//.test(text)) {
    try {
      text = new URL(text).hostname;
    } catch {
      return text;
    }
  } else {
    text = text.split(/[/?#]/, 1)[0] ?? '';
    text = text.replace(/:\d+$/, '');
  }
  return text.replace(/\.$/, '');
}

export function hostError(host: string): string | undefined {
  if (host === '') return 'Type a site, such as learn.microsoft.com.';
  if (!HOST_PATTERN.test(host)) return 'Use a site name such as learn.microsoft.com, without spaces.';
  return undefined;
}

// ------------------------------------------------------------------------------------------------
// Whole numbers typed into inputs (kept as text while editing)
// ------------------------------------------------------------------------------------------------

/** The whole number in `text`, or `undefined` when it is empty or not a whole number. */
export function parseWhole(text: string): number | undefined {
  const trimmed = text.trim();
  if (!/^\d+$/.test(trimmed)) return undefined;
  const value = Number(trimmed);
  return Number.isSafeInteger(value) ? value : undefined;
}

/** An error for a whole number that must lie in `min..max`, or `undefined` when it does. */
export function wholeError(text: string, min: number, max: number): string | undefined {
  const value = parseWhole(text);
  if (value === undefined) return 'Enter a whole number.';
  if (value < min || value > max) return `Between ${String(min)} and ${String(max)}.`;
  return undefined;
}

/** A percentage typed as text ("33" or "33.5"), as a 0-1 fraction; `undefined` when unreadable. */
export function parsePercent(text: string): number | undefined {
  const trimmed = text.trim().replace(/%$/, '').trim();
  if (!/^\d+(?:\.\d+)?$/.test(trimmed)) return undefined;
  const value = Number(trimmed);
  return Number.isFinite(value) ? value / 100 : undefined;
}

/** A fraction as the text a percentage input shows: 0.33 is "33", 0.335 is "33.5". */
export function percentText(fraction: number): string {
  return String(Math.round(fraction * 1000) / 10);
}
