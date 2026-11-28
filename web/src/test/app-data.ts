import { vi, type Mock } from 'vitest';

import { pathOf } from '@/lib/api/session';
import { createAppData, type AppData, type AppDataOptions } from '@/lib/app-data';
import { queryKeys } from '@/lib/query/keys';

import { MockEventSource } from './event-source';
import { TEST_HEALTH, TEST_ME } from './msw';

export interface TestAppData extends AppData {
  /** Records top-level navigations (jsdom cannot navigate). */
  readonly navigate: Mock<(url: string) => void>;
}

/**
 * The real data layer with the browser edges replaced: navigation is recorded, the event stream
 * uses `MockEventSource`, retries and backoff are fast and without jitter. Requests go to msw.
 */
export function createTestAppData(options: AppDataOptions = {}): TestAppData {
  const navigate = vi.fn<(url: string) => void>();
  const appData = createAppData({
    navigate,
    storage: window.sessionStorage,
    currentPath: () => pathOf(window.location),
    createEventSource: MockEventSource.create,
    query: { retryBaseMs: 1, retryMaxMs: 4, random: () => 0.5 },
    streamBackoff: { random: () => 0.5 },
    ...options,
  });
  return { ...appData, navigate };
}

/** Puts the signed-in owner and the server build in the cache, so a page renders without fetching. */
export function seedSession(appData: AppData): void {
  appData.queryClient.setQueryData(queryKeys.me, TEST_ME);
  appData.queryClient.setQueryData(queryKeys.health, TEST_HEALTH);
}
