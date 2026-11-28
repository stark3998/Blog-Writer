import { QueryClientProvider } from '@tanstack/react-query';
import { useContext, useEffect, useState, type ReactNode } from 'react';

import { AuthGate } from '@/features/auth/auth-gate';
import type { ScreenLayout } from '@/features/auth/auth-screens';
import { AppDataContext, getBrowserAppData, type AppData } from '@/lib/app-data';

export interface DataProviderProps {
  children: ReactNode;
  /** Defaults to the browser's one instance. Tests pass their own. */
  appData?: AppData;
  /**
   * `fullscreen` when the provider wraps the whole app (no shell around the auth screens),
   * `inline` (default) inside the shell.
   */
  layout?: ScreenLayout;
}

/**
 * Provides the data layer: the API client, TanStack Query, the session handling (auth screens)
 * and the app-wide event stream.
 *
 * Mount it once around the router. It is safe to nest: an inner `DataProvider` finds the outer
 * one and renders its children as they are, so a route can declare that it needs data without
 * creating a second cache, stream or gate.
 */
export function DataProvider({ children, appData, layout = 'inline' }: DataProviderProps) {
  const outer = useContext(AppDataContext);
  if (outer) return children;
  return (
    <OwnDataProvider appData={appData} layout={layout}>
      {children}
    </OwnDataProvider>
  );
}

interface OwnDataProviderProps {
  children: ReactNode;
  appData: AppData | undefined;
  layout: ScreenLayout;
}

function OwnDataProvider({ children, appData, layout }: OwnDataProviderProps) {
  const [data] = useState(() => appData ?? getBrowserAppData());
  // Leaving the tree (a test ending, a route unmounting) closes the stream and its timers.
  useEffect(() => () => {
    data.events.stop();
  }, [data]);
  return (
    <AppDataContext value={data}>
      <QueryClientProvider client={data.queryClient}>
        <AuthGate layout={layout}>{children}</AuthGate>
      </QueryClientProvider>
    </AppDataContext>
  );
}
