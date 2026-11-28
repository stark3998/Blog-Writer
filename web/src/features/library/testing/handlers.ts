import { http, HttpResponse } from 'msw';

import { apiUrl } from '@/test/msw';

import { MACHINES, T2 } from './fixtures';
import { machinesResponse } from '@/test/machines';

/**
 * msw handlers for the Library's routes. Install with `server.use(...handlers)`; the recorded
 * requests let a test check the precondition headers and bodies the screens send.
 */

/** A problem+json answer with extension members (`findings`, `errors`, `current`, `references`). */
export function problemWith(
  status: number,
  code: string,
  extensions: Readonly<Record<string, unknown>> = {},
  requestId = 'req-library-1',
): Response {
  return HttpResponse.json(
    { type: 'about:blank', title: 'Error', status, code, request_id: requestId, ...extensions },
    { status, headers: { 'Content-Type': 'application/problem+json', 'X-Request-Id': requestId } },
  );
}

/** A `validation_failed` (422) answer with FastAPI-shaped `errors[]` (`loc` starts with `body`). */
export function validationFailed(errors: readonly { loc: readonly (string | number)[]; msg: string }[]): Response {
  return problemWith(422, 'validation_failed', {
    errors: errors.map((error) => ({ loc: ['body', ...error.loc], msg: error.msg, type: 'value_error' })),
  });
}

export interface RecordedRequest {
  readonly method: string;
  readonly path: string;
  readonly search: URLSearchParams;
  readonly ifMatch: string | null;
  readonly ifNoneMatch: string | null;
  readonly contentType: string | null;
  readonly body: unknown;
}

async function readBody(request: Request): Promise<unknown> {
  const type = request.headers.get('Content-Type') ?? '';
  if (request.method === 'GET' || request.method === 'DELETE') return undefined;
  if (type.includes('json')) return request.json();
  return request.arrayBuffer();
}

/** Records a request (its body read from a clone, so the handler can still read it). */
export async function record(requests: RecordedRequest[], request: Request): Promise<RecordedRequest> {
  const url = new URL(request.url);
  const entry: RecordedRequest = {
    method: request.method,
    path: url.pathname,
    search: url.searchParams,
    ifMatch: request.headers.get('If-Match'),
    ifNoneMatch: request.headers.get('If-None-Match'),
    contentType: request.headers.get('Content-Type'),
    body: await readBody(request.clone()),
  };
  requests.push(entry);
  return entry;
}

export interface Versioned<T> {
  item: T;
  etag: string | null;
}

export interface SingletonOptions {
  /** Answers a PUT instead of the default (a problem, say); `undefined` falls through. */
  onPut?: (body: unknown, call: number) => Response | undefined;
}

/**
 * A kb singleton (`GET`/`PUT /api/kb/<name>`) with the server's ETag rules: `If-Match` must be
 * the current etag, or `If-None-Match: *` while it was never saved; otherwise 412 with `current`.
 */
export function singleton<T extends object>(path: string, initial: Versioned<T>, options: SingletonOptions = {}) {
  let current = initial;
  let version = 1;
  const requests: RecordedRequest[] = [];
  const state = {
    requests,
    get current(): Versioned<T> {
      return current;
    },
    /** Someone else saved: the next stale save gets a 412. */
    replace(next: Versioned<T>) {
      current = next;
    },
    handlers: [
      http.get(apiUrl(path), () => HttpResponse.json(current)),
      http.put(apiUrl(path), async ({ request }) => {
        const recorded = await record(requests, request);
        const custom = options.onPut?.(recorded.body, requests.filter((entry) => entry.method === 'PUT').length);
        if (custom) return custom;
        const fresh = current.etag === null ? recorded.ifNoneMatch === '*' : recorded.ifMatch === current.etag;
        if (!fresh) {
          return problemWith(412, 'etag_mismatch', { current: { etag: current.etag, updatedAt: T2, updatedBy: 'owner' } });
        }
        version += 1;
        current = { item: { ...current.item, ...(recorded.body as object), updatedAt: T2 }, etag: `"etag-saved-${String(version)}"` };
        return HttpResponse.json(current);
      }),
    ],
  };
  return state;
}

/** `GET /api/meta/machines`. */
export const machinesHandler = http.get(apiUrl('/api/meta/machines'), () => HttpResponse.json(machinesResponse(MACHINES)));

/** A GET answered with a fixed body (lists and views). */
export function getJson(path: string, body: Parameters<typeof HttpResponse.json>[0]) {
  return http.get(apiUrl(path), () => HttpResponse.json(body));
}
