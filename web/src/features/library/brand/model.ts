import { isProblem } from '@/lib/api/problems';

import { BRAND_COLOR_INFO, BRAND_COLOR_TOKENS, checkPairs, normalizeHex, type BrandColorToken } from '../lib/contrast';
import type { ErrorMap } from '../lib/errors';
import type { BrandIn, BrandKit, MediaRef } from '../lib/types';

/** `BrandKit` limits (`blogwriter.domain.settings`; the renderer's `RenderBrand` agrees). */
export const BRAND_LIMITS = { authorName: 48, authorHeadline: 64, siteName: 40, artBrief: 500 } as const;

export type BrandColorsDraft = Readonly<Record<BrandColorToken, string>>;

export interface BrandDraft {
  readonly authorName: string;
  readonly authorHeadline: string;
  readonly siteName: string;
  /** Colours as typed (`#` and six hex digits once valid). */
  readonly colors: BrandColorsDraft;
  readonly headshot: MediaRef | null;
  readonly nameMark: MediaRef | null;
  /** Per pillar id. */
  readonly artBriefs: Readonly<Record<string, string>>;
}

/** The editor's starting point from `GET /api/kb/brand` (module-level for `useSettingsDraft`). */
export function brandToDraft(kit: BrandKit): BrandDraft {
  const colors = kit.colors;
  const read = (token: BrandColorToken): string => {
    const value = colors?.[token];
    return typeof value === 'string' ? value : '';
  };
  return {
    authorName: kit.authorName ?? '',
    authorHeadline: kit.authorHeadline ?? '',
    siteName: kit.siteName ?? '',
    colors: Object.fromEntries(BRAND_COLOR_TOKENS.map((token) => [token, read(token)])) as Record<BrandColorToken, string>,
    headshot: kit.headshot ?? null,
    nameMark: kit.nameMark ?? null,
    artBriefs: { ...kit.artBriefs },
  };
}

function textOrNull(value: string): string | null {
  const trimmed = value.trim();
  return trimmed === '' ? null : trimmed;
}

/**
 * `PUT /api/kb/brand` (`BrandIn`: exactly these fields). Art briefs go only for the current
 * pillars (the server refuses others) and only when written.
 */
export function brandToBody(draft: BrandDraft, pillarIds: readonly string[] | undefined): BrandIn {
  const color = (token: BrandColorToken) => normalizeHex(draft.colors[token]) ?? draft.colors[token];
  const briefs = Object.entries(draft.artBriefs)
    .map(([pillar, brief]) => [pillar, brief.trim()] as const)
    .filter(([pillar, brief]) => brief !== '' && (pillarIds === undefined || pillarIds.includes(pillar)));
  return {
    authorName: textOrNull(draft.authorName),
    authorHeadline: textOrNull(draft.authorHeadline),
    siteName: textOrNull(draft.siteName),
    colors: {
      ink: color('ink'),
      paper: color('paper'),
      muted: color('muted'),
      mutedOnInk: color('mutedOnInk'),
      signal: color('signal'),
      signalText: color('signalText'),
      rule: color('rule'),
      ruleOnInk: color('ruleOnInk'),
    },
    font: 'inter',
    headshot: draft.headshot,
    nameMark: draft.nameMark,
    artBriefs: Object.fromEntries(briefs),
  };
}

/** What the server would refuse, by field (`authorName`, `colors.ink`, `artBriefs.agents`, `colors`). */
export function validateBrand(draft: BrandDraft): ErrorMap {
  const errors: Record<string, string> = {};
  const longer = (field: 'authorName' | 'authorHeadline' | 'siteName', label: string) => {
    if (draft[field].trim().length > BRAND_LIMITS[field]) errors[field] = `${label} has at most ${String(BRAND_LIMITS[field])} characters.`;
  };
  longer('authorName', 'Your name');
  longer('authorHeadline', 'The headline');
  longer('siteName', 'The site name');
  for (const token of BRAND_COLOR_TOKENS) {
    if (normalizeHex(draft.colors[token]) === undefined) {
      errors[`colors.${token}`] = `Enter ${BRAND_COLOR_INFO[token].label.toLowerCase()} as # and six hex digits.`;
    }
  }
  const failing = checkPairs(draft.colors).filter((pair) => pair.passes === false);
  if (failing.length > 0) {
    errors.colors = `${failing.map((pair) => pair.label).join(', ')} ${failing.length === 1 ? 'is' : 'are'} below 4.5:1. Slides must reach WCAG AA.`;
  }
  for (const [pillar, brief] of Object.entries(draft.artBriefs)) {
    if (brief.trim().length > BRAND_LIMITS.artBrief) errors[`artBriefs.${pillar}`] = `At most ${String(BRAND_LIMITS.artBrief)} characters.`;
  }
  return errors;
}

/**
 * The findings of a refused save (`brand_contrast` or `brand_invalid`, 422) by `ref`: a colour
 * pair (`muted:paper`) or a field (`authorName`, `headshot`, `nameMark`).
 */
export function brandFindings(error: unknown): ErrorMap {
  if (!isProblem(error, ['brand_contrast', 'brand_invalid'])) return {};
  const findings = error.problem?.extensions?.findings;
  if (!Array.isArray(findings)) return {};
  const result: Record<string, string> = {};
  for (const entry of findings) {
    if (typeof entry !== 'object' || entry === null) continue;
    const { ref, message } = entry as { ref?: unknown; message?: unknown };
    if (typeof ref === 'string' && typeof message === 'string' && !(ref in result)) result[ref] = message;
  }
  return result;
}

/** Plain text of the kit, for the conflict dialog's comparison. */
export function describeBrand(draft: BrandDraft): string {
  return [
    `Name: ${draft.authorName || '(not set)'}`,
    `Headline: ${draft.authorHeadline || '(not set)'}`,
    `Site name: ${draft.siteName || '(not set)'}`,
    BRAND_COLOR_TOKENS.map((token) => `${BRAND_COLOR_INFO[token].label}: ${draft.colors[token]}`).join('\n'),
    `Headshot: ${draft.headshot ? 'uploaded' : 'none'}. Name mark: ${draft.nameMark ? 'uploaded' : 'none'}.`,
    ...Object.entries(draft.artBriefs)
      .filter(([, brief]) => brief.trim() !== '')
      .map(([pillar, brief]) => `Art brief (${pillar}): ${brief}`),
  ].join('\n\n');
}
