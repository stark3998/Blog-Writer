import { createPersistentStore } from '@/lib/persistent-store';

export type ThemePreference = 'system' | 'light' | 'dark';
export type ResolvedTheme = 'light' | 'dark';

export const THEME_PREFERENCES: readonly ThemePreference[] = ['system', 'light', 'dark'];

/** Shared with public/theme-init.js, which applies the theme before first paint. */
export const THEME_STORAGE_KEY = 'bw:theme';

/**
 * Browser chrome colour per theme: the sheet token, as hex. Shared with
 * public/theme-init.js, index.html and the manifest; tokens.test.ts keeps them in step.
 */
export const THEME_COLOR: Readonly<Record<ResolvedTheme, string>> = {
  light: '#ffffff',
  dark: '#14171e',
};

export function isThemePreference(value: string): value is ThemePreference {
  return (THEME_PREFERENCES as readonly string[]).includes(value);
}

export const themeStore = createPersistentStore<ThemePreference>({
  key: THEME_STORAGE_KEY,
  fallback: 'system',
  parse: (raw) => (isThemePreference(raw) ? raw : undefined),
  serialize: (value) => value,
});

export function resolveTheme(preference: ThemePreference, systemPrefersDark: boolean): ResolvedTheme {
  if (preference === 'system') return systemPrefersDark ? 'dark' : 'light';
  return preference;
}

/** Puts the resolved theme on <html> (class + color-scheme) and the browser chrome. */
export function applyTheme(theme: ResolvedTheme, doc: Document = document): void {
  const root = doc.documentElement;
  root.classList.toggle('dark', theme === 'dark');
  root.style.colorScheme = theme;
  for (const meta of doc.querySelectorAll<HTMLMetaElement>('meta[name="theme-color"]')) {
    meta.content = THEME_COLOR[theme];
  }
}
