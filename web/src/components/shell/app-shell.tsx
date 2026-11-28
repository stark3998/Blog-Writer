import { lazy, Suspense, useCallback, useEffect, useState } from 'react';
import { Outlet, ScrollRestoration } from 'react-router';

import { Toaster } from '@/components/ui/sonner';
import { TooltipProvider } from '@/components/ui/tooltip';
import { cn } from '@/lib/cn';
import { DESKTOP_QUERY, useMediaQuery } from '@/lib/media';

import { BottomTabBar } from './bottom-tab-bar';
import { MobileTopBar } from './mobile-top-bar';
import { RouteProgress } from './route-progress';
import { Sidebar } from './sidebar';
import { useSidebarCollapsed } from './sidebar-state';
import { SkipLink } from './skip-link';
import { useCommandPaletteShortcut } from './use-command-palette-shortcut';
import { useConnectivityToasts } from './use-connectivity-toasts';
import { useFocusHeadingOnRouteChange } from './use-route-focus';

const loadCommandPalette = () => import('./command-palette');
const CommandPalette = lazy(async () => ({ default: (await loadCommandPalette()).CommandPalette }));

/** Fetches the palette's code once the page is idle, so the first Cmd+K opens instantly. */
function usePrefetchCommandPalette(): void {
  useEffect(() => {
    const prefetch = () => {
      void loadCommandPalette();
    };
    // Older Safari has no requestIdleCallback, whatever the DOM types say.
    const idle = window as Partial<Pick<Window, 'requestIdleCallback' | 'cancelIdleCallback'>>;
    if (idle.requestIdleCallback && idle.cancelIdleCallback) {
      const cancel = idle.cancelIdleCallback.bind(window);
      const handle = idle.requestIdleCallback.call(window, prefetch, { timeout: 4000 });
      return () => {
        cancel(handle);
      };
    }
    const handle = window.setTimeout(prefetch, 2000);
    return () => {
      window.clearTimeout(handle);
    };
  }, []);
}

/**
 * The app frame. Desktop: a collapsible sidebar on the desk and one sheet for
 * the page. Phone: a slim top bar, the page, and a bottom tab bar.
 */
export function AppShell() {
  const isDesktop = useMediaQuery(DESKTOP_QUERY);
  const [collapsed, setCollapsed] = useSidebarCollapsed();
  const [paletteOpen, setPaletteOpen] = useState(false);
  // The palette's code loads on first use; after that it stays mounted.
  const [paletteLoaded, setPaletteLoaded] = useState(false);

  const openPalette = useCallback(() => {
    setPaletteLoaded(true);
    setPaletteOpen(true);
  }, []);
  const togglePalette = useCallback(() => {
    setPaletteLoaded(true);
    setPaletteOpen((open) => !open);
  }, []);

  useCommandPaletteShortcut(togglePalette);
  useFocusHeadingOnRouteChange();
  useConnectivityToasts();
  usePrefetchCommandPalette();

  return (
    <TooltipProvider>
      <SkipLink />
      <RouteProgress />
      <div className="flex min-h-dvh">
        {isDesktop ? (
          <Sidebar collapsed={collapsed} onCollapsedChange={setCollapsed} onOpenCommandPalette={openPalette} />
        ) : null}
        <div className="flex min-w-0 flex-1 flex-col">
          {isDesktop ? null : <MobileTopBar onOpenCommandPalette={openPalette} />}
          <main
            id="main"
            tabIndex={-1}
            className={cn(
              'flex-1 bg-sheet outline-none',
              isDesktop
                ? 'my-2 mr-2 min-h-[calc(100dvh-1rem)] rounded-xl border border-line'
                : 'pb-[calc(4rem+env(safe-area-inset-bottom))]',
            )}
          >
            <Outlet />
          </main>
        </div>
      </div>
      {isDesktop ? null : <BottomTabBar />}
      {paletteLoaded ? (
        <Suspense fallback={null}>
          <CommandPalette
            open={paletteOpen}
            onOpenChange={setPaletteOpen}
            sidebar={isDesktop ? { collapsed, onCollapsedChange: setCollapsed } : undefined}
          />
        </Suspense>
      ) : null}
      <Toaster />
      <ScrollRestoration />
    </TooltipProvider>
  );
}
