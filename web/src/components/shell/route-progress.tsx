import { useNavigation } from 'react-router';

import { cn } from '@/lib/cn';

/** A thin ink line at the top while a page (or its code) loads. */
export function RouteProgress() {
  const navigation = useNavigation();
  const busy = navigation.state !== 'idle';

  return (
    <>
      <div
        aria-hidden="true"
        data-testid="route-progress"
        data-busy={busy}
        className={cn(
          'pointer-events-none fixed inset-x-0 top-0 z-[70] h-0.5 overflow-hidden transition-opacity duration-150',
          busy ? 'opacity-100' : 'opacity-0',
        )}
      >
        {busy ? <div className="h-full w-2/5 animate-progress bg-ink" /> : null}
      </div>
      <p role="status" className="sr-only">
        {busy ? 'Loading page' : ''}
      </p>
    </>
  );
}
