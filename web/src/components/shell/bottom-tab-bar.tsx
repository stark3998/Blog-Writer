import { NavLink } from 'react-router';

import { AREAS } from '@/app/nav';
import { cn } from '@/lib/cn';

/** Phone navigation: five tabs within thumb reach, above the home indicator. */
export function BottomTabBar() {
  return (
    <nav
      aria-label="Main"
      className="fixed inset-x-0 bottom-0 z-30 border-t border-line bg-sheet pb-[env(safe-area-inset-bottom)]"
    >
      <ul className="mx-auto grid max-w-lg grid-cols-5">
        {AREAS.map(({ id, label, path, icon: Icon }) => (
          <li key={id}>
            <NavLink
              to={path}
              className={cn(
                'group flex h-16 flex-col items-center justify-center gap-1 text-xs font-medium text-ink-muted',
                'transition-colors duration-150 focus-visible:outline-offset-[-2px] aria-[current=page]:text-ink',
                // Ink against muted ink and the faint pill are both under 3:1, so the weight
                // marks the current area too (as in the sidebar), not colour alone.
                'aria-[current=page]:font-semibold',
              )}
            >
              <span className="flex h-7 w-12 items-center justify-center rounded-full transition-colors duration-150 group-aria-[current=page]:bg-wash-strong">
                <Icon className="size-5" aria-hidden="true" />
              </span>
              {label}
            </NavLink>
          </li>
        ))}
      </ul>
    </nav>
  );
}
