import { createContext, use, useLayoutEffect, useMemo, useSyncExternalStore, type ReactNode } from 'react';

import { DARK_SCHEME_QUERY, useMediaQuery } from '@/lib/media';

import { applyTheme, resolveTheme, themeStore, type ResolvedTheme, type ThemePreference } from './theme';

interface ThemeContextValue {
  /** What the owner chose: follow the system, or a fixed theme. */
  preference: ThemePreference;
  /** What is on screen right now. */
  resolved: ResolvedTheme;
  setPreference: (preference: ThemePreference) => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

const serverPreference = (): ThemePreference => 'system';

export function ThemeProvider({ children }: { children: ReactNode }) {
  const preference = useSyncExternalStore(themeStore.subscribe, themeStore.get, serverPreference);
  const systemPrefersDark = useMediaQuery(DARK_SCHEME_QUERY);
  const resolved = resolveTheme(preference, systemPrefersDark);

  useLayoutEffect(() => {
    applyTheme(resolved);
  }, [resolved]);

  const value = useMemo<ThemeContextValue>(
    () => ({ preference, resolved, setPreference: themeStore.set }),
    [preference, resolved],
  );

  return <ThemeContext value={value}>{children}</ThemeContext>;
}

export function useTheme(): ThemeContextValue {
  const value = use(ThemeContext);
  if (!value) throw new Error('useTheme must be used inside <ThemeProvider>');
  return value;
}
