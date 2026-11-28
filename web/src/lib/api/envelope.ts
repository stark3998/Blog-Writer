import { ApiError } from './errors';
import { isProblem } from './problems';

/**
 * The server's response envelopes (`blogwriter.domain.api`) and the ETag rules of contract
 * section 1.4. Generic, so they fit every resource; once `schema.ts` is regenerated, the
 * generated `Versioned_Article_` and friends have exactly this shape.
 */

/**
 * A document's version for optimistic concurrency: the store's ETag, verbatim (it already holds
 * its quotes, for example `"00000000-0000-0000-0000-000000000000"`). Never parse or compare it
 * except for equality.
 */
export type Etag = string;

/**
 * One resource: `{item, etag}`. `etag` is `null` only for a settings singleton that was never
 * saved (the owner sees its defaults); its first save sends `If-None-Match: *`.
 */
export interface Versioned<T> {
  readonly item: T;
  readonly etag: Etag | null;
}

/** One page of a list (contract 1.5): summaries, newest first unless the route says otherwise. */
export interface Page<T> {
  readonly items: readonly Versioned<T>[];
  /** Pass back as `?cursor=` for the next page; `null` (or absent) on the last page. */
  readonly nextCursor?: string | null;
}

/** HTTP 202 for a job the server started; follow it with `useJobStatus(jobId)` and the timeline. */
export interface JobAccepted {
  readonly jobId: string;
  readonly status: string;
}

/** The `ETag` response header (the body's `etag` is the same value). */
export function etagFromResponse(response: Pick<Response, 'headers'>): Etag | undefined {
  return response.headers.get('ETag') ?? undefined;
}

/**
 * The precondition headers for a write (contract 1.4):
 *
 * - an etag: `If-Match: <etag>` (every edit, approve, transition or review of an existing document);
 * - `null`: `If-None-Match: *` (the first save of a never-saved singleton, whose `etag` was null);
 * - `undefined`: nothing (creates, and the one-tap idempotent actions that may omit it).
 */
export function preconditionHeaders(etag: Etag | null | undefined): Readonly<Record<string, string>> {
  if (etag === undefined) return {};
  return etag === null ? { 'If-None-Match': '*' } : { 'If-Match': etag };
}

/** What a 412 says about the current version (`domain.deps.etag_mismatch`), when the route knows it. */
export interface ConflictInfo {
  /** The current etag: "Keep mine" re-saves with it. Absent when the route could not read the document. */
  readonly etag?: Etag;
  readonly updatedAt?: string;
  /** Who saved the current version: `owner` (another tab or device), `pipeline`, `system`, … */
  readonly updatedBy?: string;
  /** The article version (content changes only). */
  readonly version?: number;
  readonly status?: string;
}

/** True for a stale If-Match (412 `etag_mismatch`). Never retry it silently: show the conflict dialog. */
export function isEtagMismatch(error: unknown): error is ApiError {
  return error instanceof ApiError && error.status === 412;
}

/** True for a write sent without a precondition (428 `if_match_required`): a bug in the caller. */
export function isPreconditionRequired(error: unknown): error is ApiError {
  return error instanceof ApiError && (error.status === 428 || isProblem(error, 'if_match_required'));
}

/** The 412's `current` member as typed fields; `{}` when the problem has none; `undefined` for other errors. */
export function conflictOf(error: unknown): ConflictInfo | undefined {
  if (!isEtagMismatch(error)) return undefined;
  const current = error.problem?.extensions?.current;
  if (typeof current !== 'object' || current === null || Array.isArray(current)) return {};
  const fields = current as Record<string, unknown>;
  const text = (name: string): string | undefined => {
    const value = fields[name];
    return typeof value === 'string' && value !== '' ? value : undefined;
  };
  const version = typeof fields.version === 'number' && Number.isSafeInteger(fields.version) ? fields.version : undefined;
  const etag = text('etag');
  const updatedAt = text('updatedAt');
  const updatedBy = text('updatedBy');
  const status = text('status');
  return {
    ...(etag !== undefined ? { etag } : {}),
    ...(updatedAt !== undefined ? { updatedAt } : {}),
    ...(updatedBy !== undefined ? { updatedBy } : {}),
    ...(version !== undefined ? { version } : {}),
    ...(status !== undefined ? { status } : {}),
  };
}

/** The items of a page without their envelopes, for lists that never write (they need no etag). */
export function itemsOf<T>(page: Pick<Page<T>, 'items'>): T[] {
  return page.items.map((entry) => entry.item);
}
