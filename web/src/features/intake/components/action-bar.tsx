import type { ComponentProps } from 'react';

import { cn } from '@/lib/cn';

export interface ActionBarProps extends ComponentProps<'div'> {
  /**
   * Keep the bar within thumb reach: on phones it sticks above the tab bar (4 rem plus the home
   * indicator) and spans the page's padding, so it reads as the sheet's edge, not a floating
   * card; on wide screens it sticks to the bottom of the window within its column.
   */
  sticky?: boolean;
}

/** The row of actions under a flow step: secondary actions first, the main one last (the thumb's side). */
export function ActionBar({ sticky = false, className, ...props }: ActionBarProps) {
  return (
    <div
      data-slot="intake-action-bar"
      className={cn(
        'flex flex-wrap items-center gap-2',
        sticky && [
          'sticky bottom-[calc(4rem+env(safe-area-inset-bottom))] z-10 border-t border-line bg-sheet py-3 md:bottom-0',
          // Phones: edge to edge across the page's padding. Wide screens: within its column.
          '-mx-5 px-5 sm:-mx-8 sm:px-8 md:mx-0 md:px-0',
        ],
        className,
      )}
      {...props}
    />
  );
}
