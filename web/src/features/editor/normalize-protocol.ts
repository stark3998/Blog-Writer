/**
 * Messages between the editor and the normalize worker, and the worker-side
 * handler. Both sides validate what they receive: a message that does not
 * match the protocol is never trusted.
 */
import type { Article, ArticleCodec } from './dialect/codec';
import {
  DialectError,
  type DialectErrorCode,
  FrontmatterError,
  type FrontmatterIssue,
  toDialectError,
} from './dialect/errors';

export type DialectWorkerOperation = 'normalize' | 'parse';

export interface DialectWorkerRequest {
  id: number;
  op: DialectWorkerOperation;
  markdown: string;
}

export interface SerializedDialectError {
  code: DialectErrorCode;
  message: string;
  /** Only for `invalid-frontmatter`. */
  issues?: FrontmatterIssue[];
}

export type DialectWorkerResponse =
  | { id: number; ok: true; op: 'normalize'; markdown: string }
  | { id: number; ok: true; op: 'parse'; article: Article }
  | { id: number; ok: false; error: SerializedDialectError };

const ERROR_CODES: ReadonlySet<string> = new Set<DialectErrorCode>([
  'too-large',
  'too-deep',
  'invalid-frontmatter',
  'invalid-content',
  'timeout',
  'worker-unavailable',
  'worker-failed',
  'cancelled',
  'internal',
]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function isRequestId(value: unknown): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value > 0;
}

export function isDialectWorkerRequest(value: unknown): value is DialectWorkerRequest {
  return (
    isRecord(value) &&
    isRequestId(value.id) &&
    (value.op === 'normalize' || value.op === 'parse') &&
    typeof value.markdown === 'string'
  );
}

function isFrontmatterIssue(value: unknown): value is FrontmatterIssue {
  return isRecord(value) && typeof value.path === 'string' && typeof value.message === 'string';
}

function isSerializedError(value: unknown): value is SerializedDialectError {
  return (
    isRecord(value) &&
    typeof value.code === 'string' &&
    ERROR_CODES.has(value.code) &&
    typeof value.message === 'string' &&
    (value.issues === undefined || (Array.isArray(value.issues) && value.issues.every(isFrontmatterIssue)))
  );
}

function isArticle(value: unknown): value is Article {
  return (
    isRecord(value) &&
    isRecord(value.doc) &&
    value.doc.type === 'doc' &&
    (value.frontmatter === null || isRecord(value.frontmatter))
  );
}

export function isDialectWorkerResponse(value: unknown): value is DialectWorkerResponse {
  if (!isRecord(value) || !isRequestId(value.id)) {
    return false;
  }
  if (value.ok === false) {
    return isSerializedError(value.error);
  }
  if (value.ok !== true) {
    return false;
  }
  return (
    (value.op === 'normalize' && typeof value.markdown === 'string') ||
    (value.op === 'parse' && isArticle(value.article))
  );
}

export function serializeDialectError(error: DialectError): SerializedDialectError {
  return error instanceof FrontmatterError
    ? { code: error.code, message: error.message, issues: [...error.issues] }
    : { code: error.code, message: error.message };
}

export function deserializeDialectError(error: SerializedDialectError): DialectError {
  if (error.code === 'invalid-frontmatter' && error.issues) {
    return new FrontmatterError(error.issues);
  }
  return new DialectError(error.code, error.message);
}

/**
 * Run one request against a codec. Never throws: every failure becomes an
 * error response. `resetCodec` is true when the codec may be in a bad state
 * (a stack overflow or an unexpected error) and should be replaced.
 */
export function handleDialectWorkerRequest(
  getCodec: () => ArticleCodec,
  request: DialectWorkerRequest,
): { response: DialectWorkerResponse; resetCodec: boolean } {
  try {
    const codec = getCodec();
    if (request.op === 'parse') {
      return { response: { id: request.id, ok: true, op: 'parse', article: codec.parse(request.markdown) }, resetCodec: false };
    }
    return {
      response: { id: request.id, ok: true, op: 'normalize', markdown: codec.normalize(request.markdown) },
      resetCodec: false,
    };
  } catch (thrown) {
    const error = toDialectError(thrown);
    return {
      response: { id: request.id, ok: false, error: serializeDialectError(error) },
      resetCodec: error.code === 'too-deep' || error.code === 'internal',
    };
  }
}
