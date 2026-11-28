/**
 * Typed errors for the article dialect. Every failure the codec or the
 * normalize worker can report is a `DialectError` with a stable `code`, so the
 * editor can show a clear message and never has to parse error text.
 *
 * Messages describe the problem in plain English and never quote the
 * article's content: they may end up in the console or an error report.
 */

export type DialectErrorCode =
  /** The source is larger than the editor opens (`EDITOR_MAX_BYTES`). */
  | 'too-large'
  /** Lists, quotes or callouts nest deeper than the editor parses (`MAX_NESTING_DEPTH`). */
  | 'too-deep'
  /** The frontmatter block is not valid YAML or breaks the frontmatter rules. */
  | 'invalid-frontmatter'
  /** The parsed content does not fit the editor's schema. */
  | 'invalid-content'
  /** The normalize worker did not answer in time; it was stopped. */
  | 'timeout'
  /** No worker could be started (unsupported, or blocked by the content security policy). */
  | 'worker-unavailable'
  /** The worker crashed or sent a message the client cannot read. */
  | 'worker-failed'
  /** The worker client was disposed while the request was waiting. */
  | 'cancelled'
  /** An unexpected error inside the dialect code (a bug). */
  | 'internal';

export class DialectError extends Error {
  readonly code: DialectErrorCode;

  constructor(code: DialectErrorCode, message: string, options?: ErrorOptions) {
    super(message, options);
    this.name = 'DialectError';
    this.code = code;
  }
}

/** One problem in the frontmatter: the key path (`title`, `tags[1]`, `series.part`) and what is wrong. */
export interface FrontmatterIssue {
  path: string;
  message: string;
}

export class FrontmatterError extends DialectError {
  readonly issues: readonly FrontmatterIssue[];

  constructor(issues: readonly FrontmatterIssue[]) {
    super(
      'invalid-frontmatter',
      `Invalid frontmatter: ${issues.map((issue) => `${issue.path}: ${issue.message}`).join('; ')}`,
    );
    this.name = 'FrontmatterError';
    this.issues = issues;
  }
}

/** Engines name a stack overflow differently: V8 and JavaScriptCore throw a RangeError, Firefox an InternalError. */
const STACK_OVERFLOW = /call stack|too much recursion/i;
/** TipTap wraps ProseMirror's content check (`errorOnInvalidContent`) in this error. */
const INVALID_CONTENT = /^\[tiptap error\]: Invalid (?:JSON|HTML) content/;

/**
 * Map anything thrown while parsing or serializing to a DialectError. A stack
 * overflow means the input nests too deeply (stock `marked` has no nesting
 * limit); ProseMirror's content check means the schema cannot hold the result.
 */
export function toDialectError(error: unknown): DialectError {
  if (error instanceof DialectError) {
    return error;
  }

  // The error and its causes: TipTap wraps ProseMirror's errors, and a stack
  // overflow can surface wrapped in either.
  const chain: Error[] = [];
  for (let current: unknown = error; current instanceof Error && chain.length < 5; current = current.cause) {
    chain.push(current);
  }

  if (chain.some((item) => STACK_OVERFLOW.test(item.message))) {
    return new DialectError(
      'too-deep',
      'The article nests lists, quotes or callouts too deeply to open in the editor.',
      { cause: error },
    );
  }
  if (
    chain.some(
      (item) => INVALID_CONTENT.test(item.message) || (item instanceof RangeError && item.message.startsWith('Invalid content')),
    )
  ) {
    return new DialectError('invalid-content', 'The article holds content the editor cannot represent.', { cause: error });
  }

  return new DialectError('internal', 'The article could not be processed (unexpected error in the editor).', {
    cause: error,
  });
}
