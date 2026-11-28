import type { ReactNode } from 'react';

import { cn } from '@/lib/cn';

/**
 * The page's actions within thumb reach: a bar that sticks above the phone's tab bar (4 rem
 * plus the home indicator) and to the bottom of the window on desktop. The main action comes
 * first and spans the width on a phone; the others share a row under it.
 */
export function ActionBar({
  label,
  primary,
  secondary,
  note,
  className,
}: {
  /** Names the group of actions ("Publishing actions"). */
  label: string;
  primary: ReactNode;
  secondary?: ReactNode;
  /** One line under the buttons (what the tap does, or what is happening). */
  note?: ReactNode;
  className?: string;
}) {
  return (
    <div
      role="group"
      aria-label={label}
      data-slot="action-bar"
      className={cn(
        'sticky bottom-[calc(4rem+env(safe-area-inset-bottom))] z-10 -mx-5 grid gap-2 border-t border-line bg-sheet px-5 py-3',
        'sm:-mx-8 sm:px-8 md:bottom-0 lg:-mx-12 lg:px-12',
        className,
      )}
    >
      <div className="grid gap-2 md:flex md:flex-wrap md:items-center">
        {primary}
        {secondary ? <div className="grid auto-cols-fr grid-flow-col gap-2 md:flex md:flex-wrap">{secondary}</div> : null}
      </div>
      {note ? <div className="text-sm text-ink-muted">{note}</div> : null}
    </div>
  );
}
