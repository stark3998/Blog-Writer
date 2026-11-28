import { isLoggedOutResponse } from './session';

/** Every response carries this header; error screens and toasts show it for support. */
export const REQUEST_ID_HEADER = 'X-Request-Id';

/**
 * The fields the app uses from an RFC 9457 problem document (`application/problem+json`), which
 * is what the server returns for every error.
 */
export interface ProblemDetails {
  readonly type?: string;
  readonly title?: string;
  readonly status?: number;
  readonly detail?: string;
  /** Machine-readable reason, for example `not_owner` or `csrf_failed`. */
  readonly code?: string;
  readonly requestId?: string;
  /**
   * The problem's other members (contract section 1.6), unparsed: `current` on a 412, `errors`
   * on `validation_failed`, `findings` on `confidential_text`, `missing`, `allowed`, … Read them
   * through `./problems` (`conflictOf`, `fieldErrors`, …), never show them as they are.
   */
  readonly extensions?: Readonly<Record<string, unknown>>;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function stringOrUndefined(value: unknown): string | undefined {
  return typeof value === 'string' && value !== '' ? value : undefined;
}

/** The members every problem has (RFC 9457 plus the server's `code` and `request_id`). */
const STANDARD_MEMBERS: ReadonlySet<string> = new Set(['type', 'title', 'status', 'detail', 'code', 'request_id']);

/** Reads a problem document from a parsed body. Anything that is not one gives `undefined`. */
export function parseProblem(body: unknown): ProblemDetails | undefined {
  if (!isRecord(body)) return undefined;
  const extensions = Object.entries(body).filter(([name]) => !STANDARD_MEMBERS.has(name));
  return {
    type: stringOrUndefined(body.type),
    title: stringOrUndefined(body.title),
    status: typeof body.status === 'number' ? body.status : undefined,
    detail: stringOrUndefined(body.detail),
    code: stringOrUndefined(body.code),
    requestId: stringOrUndefined(body.request_id),
    ...(extensions.length > 0 ? { extensions: Object.fromEntries(extensions) } : {}),
  };
}

/**
 * Reads the problem document from a response without consuming its body (the caller may still
 * parse it). Responses that are not JSON give `undefined`.
 */
export async function readProblem(response: Response): Promise<ProblemDetails | undefined> {
  const type = response.headers.get('Content-Type') ?? '';
  if (!/json/i.test(type)) return undefined;
  try {
    return parseProblem(await response.clone().json());
  } catch {
    return undefined;
  }
}

export type ApiErrorKind =
  /** No session: a 401, or a redirect to the sign-in page. */
  | 'unauthenticated'
  /** Signed in with an account that may not use the app (any 403 except a CSRF failure). */
  | 'forbidden'
  /** A 403 `csrf_failed` that the one automatic retry did not fix. */
  | 'csrf'
  | 'not-found'
  /** Any other 4xx. */
  | 'client'
  /** 5xx. */
  | 'server'
  /** No response at all: offline, DNS, a dropped connection. */
  | 'network';

export interface ApiErrorInit {
  readonly kind: ApiErrorKind;
  /** The HTTP status, or 0 when there was no response (or only an opaque redirect). */
  readonly status: number;
  readonly problem?: ProblemDetails;
  readonly requestId?: string;
  readonly cause?: unknown;
}

/**
 * A failed API call. The message holds the status and code only, never response content, so it is
 * safe to log.
 */
export class ApiError extends Error {
  override readonly name = 'ApiError';
  readonly kind: ApiErrorKind;
  readonly status: number;
  readonly code: string | undefined;
  readonly requestId: string | undefined;
  readonly problem: ProblemDetails | undefined;

  constructor({ kind, status, problem, requestId, cause }: ApiErrorInit) {
    const code = problem?.code;
    const what = kind === 'network' ? 'no response' : `HTTP ${status}${code ? ` ${code}` : ''}`;
    super(`API request failed: ${what}`, cause === undefined ? undefined : { cause });
    this.kind = kind;
    this.status = status;
    this.code = code;
    this.requestId = requestId ?? problem?.requestId;
    this.problem = problem;
  }

  /** Worth trying again later: no response, a server error, a timeout or rate limiting. */
  get transient(): boolean {
    return this.kind === 'network' || this.kind === 'server' || this.status === 408 || this.status === 429;
  }
}

/** The kind of error for an HTTP status that is not a redirect. */
export function kindForStatus(status: number, code?: string): ApiErrorKind {
  if (status === 401) return 'unauthenticated';
  if (status === 403) return code === 'csrf_failed' ? 'csrf' : 'forbidden';
  if (status === 404) return 'not-found';
  if (status >= 500) return 'server';
  if (status >= 400) return 'client';
  // 1xx/2xx never reach here as errors; status 0 without an opaque redirect means no usable response.
  return 'network';
}

/** Builds the error for a non-OK response and its parsed body (JSON or text, as the client read it). */
export function apiErrorFromResponse(response: Response, body: unknown): ApiError {
  const problem = parseProblem(body);
  return new ApiError({
    kind: isLoggedOutResponse(response) ? 'unauthenticated' : kindForStatus(response.status, problem?.code),
    status: response.status,
    problem,
    requestId: response.headers.get(REQUEST_ID_HEADER) ?? undefined,
  });
}

/** The error for a request that got no response. */
export function networkError(cause: unknown): ApiError {
  return new ApiError({ kind: 'network', status: 0, cause });
}

/** True for the error `fetch` throws when its signal aborts (a cancelled query, for example). */
export function isAbortError(error: unknown): boolean {
  return typeof error === 'object' && error !== null && 'name' in error && error.name === 'AbortError';
}

/** One plain sentence for an error, for inline error states. Never includes response content. */
export function describeError(error: unknown): string {
  if (!(error instanceof ApiError)) return 'Something went wrong.';
  switch (error.kind) {
    case 'network':
      return 'Can’t reach the server. Check your connection.';
    case 'server':
      return 'The server had a problem.';
    case 'unauthenticated':
      return 'You’re signed out.';
    case 'forbidden':
      return 'This account isn’t allowed to use Blog Writer.';
    case 'not-found':
      return 'Not found.';
    case 'csrf':
    case 'client':
      return 'The server refused the request.';
  }
}
