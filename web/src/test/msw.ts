import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';

import type { HealthResponse, MeResponse } from '@/lib/api/types';

/** jsdom's origin in Vitest: the app's requests are same-origin with it. */
export const TEST_ORIGIN = 'http://localhost:3000';

export function apiUrl(path: string): string {
  return new URL(path, TEST_ORIGIN).href;
}

export const TEST_ME: MeResponse = { oid: '00000000-0000-4000-8000-000000000001', name: 'Test Owner' };
export const TEST_HEALTH: HealthResponse = { status: 'ok', sha: '0123456789abcdef0123456789abcdef01234567' };

/** The signed-in happy path. Tests add their own handlers with `server.use(...)`. */
export const defaultHandlers = [
  http.get(apiUrl('/api/me'), () => HttpResponse.json(TEST_ME)),
  http.get(apiUrl('/api/health'), () => HttpResponse.json(TEST_HEALTH)),
];

/**
 * Every fetch in tests goes through this server; a request without a handler fails the call
 * (`onUnhandledRequest: 'error'`), so no unit test ever reaches the network.
 */
export const server = setupServer(...defaultHandlers);

/** A problem response shaped like the server's (`application/problem+json`, `X-Request-Id`). */
export function problem(status: number, code?: string, requestId = 'req-test-1'): Response {
  return HttpResponse.json(
    { type: 'about:blank', title: 'Error', status, ...(code ? { code } : {}), request_id: requestId },
    { status, headers: { 'Content-Type': 'application/problem+json', 'X-Request-Id': requestId } },
  );
}
