import type { MouseEvent } from 'react';

/** The first stop for keyboard users: jumps past the navigation to the page. */
export function SkipLink() {
  const skip = (event: MouseEvent<HTMLAnchorElement>) => {
    // Focus the page directly instead of changing the URL hash, which the
    // router would treat as a navigation.
    event.preventDefault();
    const main = document.getElementById('main');
    if (!main) return;
    main.focus();
    main.scrollIntoView({ block: 'start' });
  };

  return (
    <a
      href="#main"
      onClick={skip}
      className="sr-only focus:not-sr-only focus:fixed focus:left-3 focus:top-3 focus:z-[60] focus:rounded-md focus:bg-ink focus:px-4 focus:py-2.5 focus:text-sm focus:font-medium focus:text-ink-inverse"
    >
      Skip to content
    </a>
  );
}
