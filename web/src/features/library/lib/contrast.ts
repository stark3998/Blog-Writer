/**
 * The brand kit's colours and the contrast rules the slide renderer enforces
 * (`blogwriter.render.brand`): every pair of colours that carries text must reach WCAG 2 AA for
 * normal text (4.5:1), although slide text is at least 28 px. Checking here, as the owner types,
 * gives the same verdict the server gives on save (same pairs, same formula).
 */

/** WCAG 2 AA for normal text. */
export const AA_NORMAL_TEXT = 4.5;

/** The colour tokens of the carousel and card templates, in the order the kit shows them. */
export const BRAND_COLOR_TOKENS = [
  'ink',
  'paper',
  'muted',
  'mutedOnInk',
  'signal',
  'signalText',
  'rule',
  'ruleOnInk',
] as const;

export type BrandColorToken = (typeof BRAND_COLOR_TOKENS)[number];

export const BRAND_COLOR_INFO: Readonly<Record<BrandColorToken, { readonly label: string; readonly role: string }>> = {
  ink: { label: 'Ink', role: 'The dark surface, and text on light surfaces.' },
  paper: { label: 'Paper', role: 'The light surface, and text on dark surfaces.' },
  muted: { label: 'Muted', role: 'Secondary text on paper.' },
  mutedOnInk: { label: 'Muted on ink', role: 'Secondary text on ink.' },
  signal: { label: 'Signal', role: 'The accent on ink. Ink text sits on it.' },
  signalText: { label: 'Signal text', role: 'Accent text on paper.' },
  rule: { label: 'Rule', role: 'Hairlines on paper. Never carries text.' },
  ruleOnInk: { label: 'Rule on ink', role: 'Hairlines on ink. Never carries text.' },
};

export interface TextPair {
  /** `<foreground>:<background>`, the `ref` the server's `brand-contrast` findings use. */
  readonly id: string;
  readonly foreground: BrandColorToken;
  readonly background: BrandColorToken;
  /** "Muted on paper". */
  readonly label: string;
}

function pair(foreground: BrandColorToken, background: BrandColorToken, label: string): TextPair {
  return { id: `${foreground}:${background}`, foreground, background, label };
}

/** Every foreground and background the templates set text in (`BrandColors.text_pairs()`). */
export const BRAND_TEXT_PAIRS: readonly TextPair[] = [
  pair('ink', 'paper', 'Ink on paper'),
  pair('muted', 'paper', 'Muted on paper'),
  pair('signalText', 'paper', 'Signal text on paper'),
  pair('paper', 'ink', 'Paper on ink'),
  pair('mutedOnInk', 'ink', 'Muted on ink'),
  pair('signal', 'ink', 'Signal on ink'),
  pair('ink', 'signal', 'Ink on signal'),
];

const SIX_DIGITS = /^#?([0-9a-f]{6})$/i;
const THREE_DIGITS = /^#?([0-9a-f])([0-9a-f])([0-9a-f])$/i;

/**
 * The colour as the server stores it (`#` and six upper-case hex digits), from what the owner
 * typed: with or without `#`, and the three-digit shorthand. `undefined` when it is not a colour.
 */
export function normalizeHex(input: string): string | undefined {
  const text = input.trim();
  const full = SIX_DIGITS.exec(text)?.[1];
  if (full !== undefined) return `#${full.toUpperCase()}`;
  const short = THREE_DIGITS.exec(text);
  if (short) {
    const [, r = '', g = '', b = ''] = short;
    return `#${(r + r + g + g + b + b).toUpperCase()}`;
  }
  return undefined;
}

function channel(value: number): number {
  const s = value / 255;
  return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
}

/** WCAG 2 relative luminance of a normalized colour. */
export function relativeLuminance(hex: string): number {
  const digits = hex.slice(1);
  const [r, g, b] = [0, 2, 4].map((offset) => Number.parseInt(digits.slice(offset, offset + 2), 16)) as [
    number,
    number,
    number,
  ];
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}

/** WCAG 2 contrast ratio of two colours (1 to 21). */
export function contrastRatio(foreground: string, background: string): number {
  const a = relativeLuminance(foreground);
  const b = relativeLuminance(background);
  const [high, low] = a >= b ? [a, b] : [b, a];
  return (high + 0.05) / (low + 0.05);
}

/** "4.52:1", rounded down, so a ratio shown as 4.50 never fails the 4.5 rule. */
export function formatRatio(ratio: number): string {
  return `${(Math.floor(ratio * 100) / 100).toFixed(2)}:1`;
}

export interface PairCheck extends TextPair {
  /** `undefined` while one of the two colours is not a valid colour yet. */
  readonly ratio: number | undefined;
  readonly passes: boolean | undefined;
}

/** The verdict for every text pair of a palette (colours as typed). */
export function checkPairs(colors: Readonly<Record<BrandColorToken, string>>): PairCheck[] {
  return BRAND_TEXT_PAIRS.map((textPair) => {
    const foreground = normalizeHex(colors[textPair.foreground]);
    const background = normalizeHex(colors[textPair.background]);
    if (foreground === undefined || background === undefined) {
      return { ...textPair, ratio: undefined, passes: undefined };
    }
    const ratio = contrastRatio(foreground, background);
    return { ...textPair, ratio, passes: ratio >= AA_NORMAL_TEXT };
  });
}
