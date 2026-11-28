import { useEffect, useRef } from 'react';
import { useMatches } from 'react-router';

import { focusPageHeading } from './focus';

/**
 * After navigating to a different page, focus its heading. Switching tabs
 * within a page keeps the same route, so focus stays on the tab list.
 */
export function useFocusHeadingOnRouteChange(): void {
  const matches = useMatches();
  const routeId = matches.at(-1)?.id;
  const previous = useRef(routeId);

  useEffect(() => {
    if (previous.current === routeId) return;
    previous.current = routeId;
    focusPageHeading();
  }, [routeId]);
}
