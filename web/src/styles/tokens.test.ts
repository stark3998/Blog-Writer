import { readdirSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';

import { describe, expect, it } from 'vitest';

import { THEME_COLOR } from '@/theme/theme';
import { composite, contrast, inSrgbGamut, parseOklch, toHex, toSrgb, type Oklch } from '@/test/color';
import { readProjectFile } from '@/test/files';

const css = readProjectFile('src/styles/index.css');

/** The body of the first `selector {` block, with nested braces handled. */
function block(source: string, selector: string): string {
  const start = source.indexOf(`${selector} {`);
  if (start < 0) throw new Error(`no ${selector} block in index.css`);
  let depth = 0;
  for (let i = source.indexOf('{', start); i < source.length; i += 1) {
    if (source[i] === '{') depth += 1;
    if (source[i] === '}') depth -= 1;
    if (depth === 0) return source.slice(source.indexOf('{', start) + 1, i);
  }
  throw new Error(`unterminated ${selector} block`);
}

function colorTokens(body: string): Map<string, string> {
  const tokens = new Map<string, string>();
  for (const match of body.matchAll(/--color-([\w-]+):\s*(oklch\([^)]*\));/g)) {
    const [, name, value] = match;
    if (name && value) tokens.set(name, value);
  }
  return tokens;
}

const lightTokens = colorTokens(block(css, '@theme'));
const darkTokens = colorTokens(block(css, '.dark'));

type Theme = 'light' | 'dark';
const palettes: Record<Theme, Map<string, string>> = { light: lightTokens, dark: darkTokens };

function token(theme: Theme, name: string): Oklch {
  const value = palettes[theme].get(name);
  if (!value) throw new Error(`--color-${name} is not defined for the ${theme} theme`);
  return parseOklch(value);
}

const SURFACES = ['sheet', 'desk', 'raised', 'sunken'] as const;
const PILLARS = ['agents', 'identity', 'governance'] as const;
const STATES = ['success', 'warn', 'danger'] as const;

/** [foreground, background, minimum ratio]. 4.5:1 for text, 3:1 for UI parts (WCAG 2.2 AA). */
const PAIRS: readonly (readonly [string, string, number])[] = [
  ...SURFACES.flatMap((surface) => [
    ['ink', surface, 7] as const,
    ['ink-muted', surface, 4.5] as const,
    ['ink-subtle', surface, 4.5] as const,
    ['control', surface, 3] as const,
    ['focus', surface, 3] as const,
  ]),
  ...[...PILLARS, ...STATES].flatMap((hue) => [
    [hue, 'sheet', 4.5] as const,
    [hue, 'desk', 4.5] as const,
    [hue, `${hue}-soft`, 4.5] as const,
  ]),
  ['ink-inverse', 'ink', 7],
  ['ink-inverse', 'danger', 4.5],
  ['ink', 'highlight', 4.5],
  ['ink-subtle', 'raised', 4.5],
];

describe('design tokens', () => {
  it('defines the semantic palette in @theme', () => {
    for (const name of ['desk', 'sheet', 'ink', 'ink-muted', 'focus', ...PILLARS, ...STATES]) {
      expect(lightTokens.has(name), name).toBe(true);
    }
  });

  it('re-points every colour token in the dark theme, so components never need dark: overrides', () => {
    expect([...darkTokens.keys()].sort()).toEqual([...lightTokens.keys()].sort());
  });

  it.each(['light', 'dark'] as const)('keeps every %s colour inside sRGB', (theme) => {
    for (const [name, value] of palettes[theme]) {
      expect(inSrgbGamut(parseOklch(value)), `--color-${name}: ${value}`).toBe(true);
    }
  });

  it.each(['light', 'dark'] as const)('meets AA contrast in the %s theme', (theme) => {
    for (const [fg, bg, minimum] of PAIRS) {
      const ratio = contrast(toSrgb(token(theme, fg)), toSrgb(token(theme, bg)));
      expect(ratio, `${fg} on ${bg}: ${ratio.toFixed(2)}`).toBeGreaterThanOrEqual(minimum);
    }
  });

  it.each(['light', 'dark'] as const)('keeps text readable on hover and selected rows in the %s theme', (theme) => {
    for (const surface of ['sheet', 'desk', 'raised'] as const) {
      const row = composite(token(theme, 'wash-strong'), toSrgb(token(theme, surface)));
      for (const text of ['ink', 'ink-muted'] as const) {
        const ratio = contrast(toSrgb(token(theme, text)), row);
        expect(ratio, `${text} on wash-strong over ${surface}`).toBeGreaterThanOrEqual(4.5);
      }
    }
  });

  it('matches the browser chrome colours to the sheet', () => {
    expect(toHex(token('light', 'sheet'))).toBe(THEME_COLOR.light);
    expect(toHex(token('dark', 'sheet'))).toBe(THEME_COLOR.dark);
    const html = readProjectFile('index.html');
    expect(html).toContain(`content="${THEME_COLOR.light}"`);
    expect(html).toContain(`content="${THEME_COLOR.dark}"`);
  });

  it('uses the type scale from The Elements of Typographic Style', () => {
    const sizes = [...block(css, '@theme').matchAll(/--text-(\w+):\s*([\d.]+)rem;/g)].map(
      ([, name, rem]) => [name, Number(rem) * 16] as const,
    );
    expect(Object.fromEntries(sizes)).toEqual({
      xs: 12,
      sm: 14,
      base: 16,
      lg: 18,
      xl: 21,
      '2xl': 24,
      '3xl': 36,
      '4xl': 48,
    });
  });

  it('respects reduced motion globally', () => {
    expect(css).toMatch(/@media \(prefers-reduced-motion: reduce\)[\s\S]*animation-duration: 0\.01ms !important/);
  });
});

describe('phone edges', () => {
  it('keeps the app clear of the notch in landscape, since the page draws under it (viewport-fit=cover)', () => {
    expect(readProjectFile('index.html')).toContain('viewport-fit=cover');
    const body = block(css, 'body');
    expect(body).toMatch(/padding-left:\s*env\(safe-area-inset-left\);/);
    expect(body).toMatch(/padding-right:\s*env\(safe-area-inset-right\);/);
  });

  it('stops keyboard focus clear of the phone header (3.5rem) and tab bar (4rem)', () => {
    const phone = block(css, '@media (width < 48rem)');
    expect(phone).toContain('scroll-padding-top: calc(3.5rem + env(safe-area-inset-top));');
    expect(phone).toContain('scroll-padding-bottom: calc(4rem + env(safe-area-inset-bottom));');
    // The bars those paddings clear.
    expect(readProjectFile('src/components/shell/mobile-top-bar.tsx')).toContain('h-14');
    expect(readProjectFile('src/components/shell/bottom-tab-bar.tsx')).toContain('h-16');
  });
});

describe('component rules', () => {
  function sourceFiles(dir: string): string[] {
    return readdirSync(dir).flatMap((entry) => {
      const path = join(dir, entry);
      if (statSync(path).isDirectory()) return sourceFiles(path);
      return /\.tsx?$/.test(entry) && !/\.test\.tsx?$/.test(entry) ? [path] : [];
    });
  }

  const files = sourceFiles(join(process.cwd(), 'src')).map((path) => ({
    path: relative(process.cwd(), path),
    source: readProjectFile(relative(process.cwd(), path)),
  }));

  it('never branches on theme with dark: classes (tokens do that)', () => {
    // A variant is glued to its utility (`dark:bg-sheet`); object keys (`dark: '#…'`) are not.
    const variant = /(^|["'`\s])dark:[\w[!-]/m;
    const offenders = files.filter(({ source }) => variant.test(source)).map(({ path }) => path);
    expect(offenders).toEqual([]);
  });

  it('never uses colour classes outside the token set', () => {
    // Tailwind's default palette is removed in @theme; this catches stray names early.
    const stray = /\b(?:bg|text|border|ring|fill|stroke)-(?:gray|slate|zinc|neutral|stone|red|blue|green|white|black)(?:-\d+)?\b/;
    const offenders = files.filter(({ source }) => stray.test(source)).map(({ path }) => path);
    expect(offenders).toEqual([]);
  });

  /** Hex literals (also inside arbitrary classes such as `bg-[#fff]`) and colour functions. */
  const RAW_COLOUR = /#[0-9a-f]{3,8}\b|\b(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch|color-mix)\(/i;

  it('keeps raw colour values in the theme: components use tokens', () => {
    // The theme owns the few literal colours (browser chrome, the app mark); test helpers do maths.
    // The generated API types carry the brand kit's default colours as `@default` notes: data the
    // owner edits, never a style.
    const allowed = (path: string) =>
      /^src[\\/](?:theme|test)[\\/]/.test(path) || /^src[\\/]lib[\\/]api[\\/]schema\.ts$/.test(path);
    const offenders = files
      .filter(({ path, source }) => !allowed(path) && RAW_COLOUR.test(source))
      .map(({ path }) => path);
    expect(offenders).toEqual([]);
  });

  it('recognises raw colours, and leaves ids and anchors alone', () => {
    for (const sample of ['fill="#151d2b"', "'bg-[#FFF]'", 'rgb(0 0 0)', 'oklch(0.5 0.1 200)']) {
      expect(RAW_COLOUR.test(sample), sample).toBe(true);
    }
    for (const sample of ["querySelector('#main')", 'href="#sidebar"', 'item #1', "getElementById('root')"]) {
      expect(RAW_COLOUR.test(sample), sample).toBe(false);
    }
  });

  it('loads nothing from a CDN', () => {
    expect(css).not.toMatch(/url\(\s*['"]?(https?:)?\/\//);
    expect(css).not.toMatch(/@import\s+(url\()?['"]?https?:/);
    expect(readProjectFile('index.html')).not.toMatch(/(src|href)="(https?:)?\/\//);
  });
});
