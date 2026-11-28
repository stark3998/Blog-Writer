import type { QueryClient } from '@tanstack/react-query';
import { createContext, useContext } from 'react';

import { createApiClient, ME_PATH, type ApiClient, type FetchLike } from '@/lib/api/client';
import { isLoggedOutResponse, pathOf } from '@/lib/api/session';
import { createAuthController, type AuthController } from '@/lib/auth/auth-controller';
import { browserSessionStorage, createRedirectGuard } from '@/lib/auth/redirect-guard';
import { createEventFeed, type EventFeed } from '@/lib/events/event-feed';
import { createEventRouter } from '@/lib/events/event-router';
import {
  createEventStream,
  type EventSourceLike,
  type EventStream,
  type ProbeOutcome,
} from '@/lib/events/event-stream';
import { createJobStore, type JobStore } from '@/lib/events/job-store';
import type { BackoffOptions } from '@/lib/query/backoff';
import { createQueryClient, type QueryClientOptions } from '@/lib/query/query-client';

/** The data layer, wired once per app (and once per test). */
export interface AppData {
  readonly queryClient: QueryClient;
  readonly api: ApiClient;
  readonly auth: AuthController;
  readonly jobs: JobStore;
  readonly events: EventStream;
  /** Every stream event after the store and the cache have it (`useStreamEvents`, `useEntityChanged`). */
  readonly feed: EventFeed;
}

/** Everything that touches the browser can be replaced, so tests run without network or navigation. */
export interface AppDataOptions {
  readonly baseUrl?: string;
  readonly fetch?: FetchLike;
  /** A top-level navigation. Defaults to `window.location.assign`. */
  readonly navigate?: (url: string) => void;
  /** Defaults to the window's path, query and fragment. */
  readonly currentPath?: () => string;
  /** Where sign-in redirects are counted. Defaults to sessionStorage; `null` means unavailable. */
  readonly storage?: Storage | null;
  readonly now?: () => number;
  readonly query?: QueryClientOptions;
  /** Defaults to `new EventSource(url)`. */
  readonly createEventSource?: (url: string) => EventSourceLike;
  readonly streamBackoff?: Partial<BackoffOptions>;
  readonly streamLingerMs?: number;
}

/** A session probe that hangs longer than this counts as "server unavailable". */
export const PROBE_TIMEOUT_MS = 10_000;

/**
 * Asks `GET /api/me` (`redirect: 'manual'`, through the client, so its session handling runs too)
 * whether the session is still there.
 */
export async function probeSession(api: ApiClient, timeoutMs = PROBE_TIMEOUT_MS): Promise<ProbeOutcome> {
  try {
    const { response } = await api.GET(ME_PATH, { cache: 'no-store', signal: AbortSignal.timeout(timeoutMs) });
    if (response.ok) return 'authenticated';
    if (isLoggedOutResponse(response)) return 'logged-out';
    return response.status === 403 ? 'forbidden' : 'unavailable';
  } catch {
    return 'unavailable';
  }
}

export function createAppData(options: AppDataOptions = {}): AppData {
  const now = options.now ?? Date.now;
  const guard = createRedirectGuard({
    storage: options.storage === undefined ? browserSessionStorage() : (options.storage ?? undefined),
    now,
  });
  const auth = createAuthController({
    guard,
    navigate:
      options.navigate ??
      ((url) => {
        window.location.assign(url);
      }),
    currentPath: options.currentPath ?? (() => pathOf(window.location)),
  });
  const api = createApiClient({ auth, baseUrl: options.baseUrl, fetch: options.fetch });
  const queryClient = createQueryClient(options.query);
  const jobs = createJobStore();
  const route = createEventRouter(jobs, queryClient);
  const feed = createEventFeed();
  const events = createEventStream({
    createEventSource: options.createEventSource ?? ((url) => new EventSource(url)),
    probe: () => probeSession(api),
    onEvent: (event) => {
      route(event);
      feed.publish(event);
    },
    onLoggedOut: auth.handleUnauthenticated,
    onForbidden: () => {
      auth.handleForbidden();
    },
    backoff: options.streamBackoff,
    lingerMs: options.streamLingerMs,
    now,
  });
  return { queryClient, api, auth, jobs, events, feed };
}

let browserAppData: AppData | undefined;

/** The browser's one instance, created on first use. */
export function getBrowserAppData(): AppData {
  browserAppData ??= createAppData();
  return browserAppData;
}

export const AppDataContext = createContext<AppData | undefined>(undefined);

/** The data layer from the nearest `DataProvider` (`features/data/data-provider`). */
export function useAppData(): AppData {
  const appData = useContext(AppDataContext);
  if (!appData) throw new Error('useAppData() needs a <DataProvider> above it');
  return appData;
}
