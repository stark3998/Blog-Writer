import { createElement, lazy, Suspense, type ComponentType, type ReactElement, type ReactNode } from 'react';

import { Skeleton } from '@/components/ui/skeleton';

/** Loads one section's component (a dynamic `import()` of its feature module). */
export type SectionLoader<P> = () => Promise<ComponentType<P>>;

export interface SectionModules<Id extends string, P extends object> {
  /**
   * Loads the code of section `id`, once. The route's `loader` awaits it, so a tab switch
   * commits only when the section can render: no flash of a fallback, and focus stays on the tab.
   */
  readonly preload: (id: Id) => Promise<void>;
  /**
   * The section as an element. Once loaded it is the component itself; before that, a lazy
   * component that suspends until its code arrives (wrap it in `SectionSuspense`).
   */
  readonly render: (id: Id, props: P) => ReactElement;
}

/**
 * The sections or tabs of one area, each in its own chunk, loaded by id. Route modules (thin
 * shells in `src/routes/`) use it so that every section lives in its feature folder.
 *
 * ```ts
 * const sections = sectionModules<LibrarySectionId>({
 *   pillars: async () => (await import('@/features/library/pillars-section')).PillarsSection,
 * });
 * ```
 */
export function sectionModules<Id extends string, P extends object = Record<string, never>>(
  loaders: Readonly<Record<Id, SectionLoader<P>>>,
): SectionModules<Id, P> {
  const loaded = new Map<Id, ComponentType<P>>();
  const pending = new Map<Id, Promise<ComponentType<P>>>();
  const lazies = new Map<Id, ComponentType<P>>();

  const load = (id: Id): Promise<ComponentType<P>> => {
    const ready = loaded.get(id);
    if (ready) return Promise.resolve(ready);
    const inFlight = pending.get(id);
    if (inFlight) return inFlight;
    const promise = loaders[id]().then(
      (component) => {
        loaded.set(id, component);
        pending.delete(id);
        return component;
      },
      (error: unknown) => {
        // A failed download (an old tab after a deploy) may succeed on the next try.
        pending.delete(id);
        throw error;
      },
    );
    pending.set(id, promise);
    return promise;
  };

  const lazyComponent = (id: Id): ComponentType<P> => {
    let component = lazies.get(id);
    if (!component) {
      component = lazy(async () => ({ default: await load(id) }));
      lazies.set(id, component);
    }
    return component;
  };

  return {
    preload: async (id) => {
      await load(id);
    },
    render: (id, props) => createElement(loaded.get(id) ?? lazyComponent(id), props),
  };
}

/** What a section shows while its code loads (only when its route's loader did not load it first). */
export function SectionFallback() {
  return (
    <div>
      <p role="status" className="sr-only">
        Loading section
      </p>
      <Skeleton className="h-8 w-[min(20rem,90%)]" />
      <Skeleton className="mt-4 h-4 w-[min(36rem,100%)]" />
      <Skeleton className="mt-2 h-4 w-[min(30rem,95%)]" />
    </div>
  );
}

/** Suspense around one lazy section, with the section skeleton. */
export function SectionSuspense({ children }: { children: ReactNode }) {
  return <Suspense fallback={<SectionFallback />}>{children}</Suspense>;
}
