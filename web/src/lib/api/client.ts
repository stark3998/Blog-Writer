import createClient, { type Client, type Middleware } from 'openapi-fetch';

import type { AuthController } from '@/lib/auth/auth-controller';

import { apiErrorFromResponse, isAbortError, networkError, readProblem, REQUEST_ID_HEADER } from './errors';
import type { paths } from './schema';
import { isLoggedOutResponse } from './session';

/** The typed client for the server's API (`paths` from `./schema`). */
export type ApiClient = Client<paths>;

export type FetchLike = (request: Request) => Promise<Response>;

/**
 * Options every call uses. Same-origin only, with cookies (Easy Auth's session and the app's
 * `__Host-bw`). Redirects are never followed: a redirect from an API call is Easy Auth sending
 * the browser to sign in, which must be a top-level navigation, not a cross-origin fetch.
 */
export const REQUEST_DEFAULTS = {
  credentials: 'same-origin',
  redirect: 'manual',
} as const satisfies RequestInit;

/** Refreshes the app cookie; also the cheapest authenticated call (the session probe). */
export const ME_PATH = '/api/me';

const UNSAFE_METHODS: ReadonlySet<string> = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);

export interface ApiClientOptions {
  /** Told about missing sessions and refused accounts. */
  readonly auth: Pick<AuthController, 'handleUnauthenticated' | 'handleForbidden'>;
  /** Defaults to the page's own origin. */
  readonly baseUrl?: string;
  /** Defaults to the global `fetch`, looked up on every call. */
  readonly fetch?: FetchLike;
}

/**
 * Creates the API client: openapi-fetch with the session rules from the server contract
 * (`docs/v2/phase1/core.md`, "Client contract"):
 *
 * - 401 or a redirect: no session, so `auth.handleUnauthenticated()` (sign in again, loop-guarded).
 * - 403 `csrf_failed` on a write: `GET /api/me` re-issues the app cookie, then the write is sent
 *   once more. A second failure is returned as it is.
 * - Any other 403 (`not_owner`, `owner_not_configured`, or Easy Auth's own refusal):
 *   `auth.handleForbidden()`.
 * - No response at all: the call rejects with an `ApiError` of kind `network`.
 */
export function createApiClient<Paths extends object = paths>({
  auth,
  baseUrl = window.location.origin,
  fetch: fetchImpl = (request) => fetch(request),
}: ApiClientOptions): Client<Paths> {
  const client = createClient<Paths>({
    baseUrl,
    fetch: fetchImpl,
    headers: { Accept: 'application/json' },
    ...REQUEST_DEFAULTS,
  });
  client.use(sessionMiddleware(auth, baseUrl, fetchImpl));
  return client;
}

function sessionMiddleware(
  auth: ApiClientOptions['auth'],
  baseUrl: string,
  fetchImpl: FetchLike,
): Middleware {
  /** Copies of in-flight writes, kept until their response arrives, for the one CSRF retry. */
  const writes = new Map<string, Request>();

  const react = async (response: Response): Promise<void> => {
    if (isLoggedOutResponse(response)) {
      auth.handleUnauthenticated();
      return;
    }
    if (response.status !== 403) return;
    const problem = await readProblem(response);
    // A CSRF failure is about the request, not the account.
    if (problem?.code === 'csrf_failed') return;
    auth.handleForbidden({
      code: problem?.code,
      requestId: response.headers.get(REQUEST_ID_HEADER) ?? problem?.requestId,
    });
  };

  const send = async (request: Request): Promise<Response> => {
    try {
      return await fetchImpl(request);
    } catch (error) {
      throw isAbortError(error) ? error : networkError(error);
    }
  };

  return {
    onRequest({ request, id }) {
      if (UNSAFE_METHODS.has(request.method)) writes.set(id, request.clone());
    },
    async onResponse({ response, id }) {
      const write = writes.get(id);
      writes.delete(id);
      if (write && response.status === 403 && (await readProblem(response))?.code === 'csrf_failed') {
        try {
          const refresh = { ...REQUEST_DEFAULTS, headers: { Accept: 'application/json' } };
          await fetchImpl(new Request(new URL(ME_PATH, baseUrl), refresh));
        } catch {
          // The retry below reports whatever is wrong.
        }
        const retried = await send(write);
        await react(retried);
        return retried;
      }
      await react(response);
      return undefined;
    },
    onError({ error, id }) {
      writes.delete(id);
      return isAbortError(error) ? undefined : networkError(error);
    },
  };
}

/** The `data` of a call's success branch (openapi-fetch's error branch has no required `data`). */
export type SuccessData<R> = R extends { data: infer D } ? D : never;

/**
 * Resolves with the data of a successful call, or rejects with an `ApiError` built from the
 * response (status, problem `code`, request id). For TanStack Query `queryFn`s:
 * `queryFn: ({ signal }) => unwrap(api.GET('/api/me', { signal }))`.
 */
export async function unwrap<R extends { data?: unknown; error?: unknown; response: Response }>(
  pending: Promise<R>,
): Promise<SuccessData<R>> {
  const result = await pending;
  if (!result.response.ok) throw apiErrorFromResponse(result.response, result.error);
  // openapi-fetch returns the success branch for every OK response.
  return result.data as SuccessData<R>;
}
