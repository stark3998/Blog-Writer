import { ApiError, describeError, isAbortError } from './errors';

/**
 * Problems (contract section 1.6) as the web uses them: one owner-facing sentence per error, and
 * typed readers for the extension members a screen acts on. Nothing here ever shows response
 * content beyond the server's `detail` (owner-facing by contract: it never quotes content, prompts,
 * confidential terms or URLs).
 */

/** The problem `code` of an error, when it is an API error that has one. */
export function problemCode(error: unknown): string | undefined {
  return error instanceof ApiError ? error.code : undefined;
}

/** True for an `ApiError` whose problem `code` is `code` (or one of `codes`). */
export function isProblem(error: unknown, code: string | readonly string[]): error is ApiError {
  const actual = problemCode(error);
  if (actual === undefined) return false;
  return typeof code === 'string' ? actual === code : code.includes(actual);
}

/**
 * Owner-facing sentences for the codes the contract names (sections 1.6, 10 and 12, and the
 * route codes of the work orders). The server's `detail`, when it sends one, wins over these.
 */
export const PROBLEM_MESSAGES: Readonly<Record<string, string>> = {
  // Section 1.6
  not_found: 'It isn’t there any more, or the link is wrong.',
  conflict: 'It already exists.',
  invalid_transition: 'That step isn’t possible from where it stands now. Reload to see its current state.',
  live_article: 'This article is live. Unpublish it first.',
  eval_article: 'Evaluation articles can’t be approved or published.',
  read_only_state: 'It’s read-only while a background job writes it. Try again when the job is done.',
  blocklist_not_configured: 'Save the blocklist once in Settings > Blocklist & policy first. An empty list is fine.',
  ai_use_not_confirmed: 'Confirm your employer’s AI-use policy in Settings > Blocklist & policy first.',
  meta_incomplete: 'Fill in the missing article details first.',
  etag_mismatch: 'It changed since you loaded it. Reload, compare and save again.',
  too_large: 'That file is too large.',
  markdown_too_large: 'The article is over the 512 KB limit. Shorten it and save again.',
  unsupported_media_type: 'That file type isn’t supported.',
  validation_failed: 'Some of what you entered needs a fix.',
  confidential_text: 'Some text matches the blocklist. Rephrase it and try again.',
  invalid_cursor: 'The list changed while you were paging. Reload it.',
  if_match_required: 'The save was sent without a version. Reload and try again.',
  internal_error: 'The server had a problem. Try again in a moment.',
  invalid_document: 'The server couldn’t store it. Try again; if it keeps failing, check Settings > System.',
  store_unavailable: 'The database is busy. Try again in a moment.',
  csrf_failed: 'Your session needs a refresh. Reload the page and try again.',
  // Owner inputs and setup (section 12)
  not_enough_samples: 'Add 5 to 10 LinkedIn posts you wrote yourself first, in Library > Voice guide.',
  voice_guide_missing: 'Build the voice guide first, in Library > Voice guide.',
  brand_incomplete: 'Add your author name in Library > Brand kit first.',
  reconnect_required: 'Reconnect LinkedIn in Settings > Integrations, or use the ready-to-post kit.',
  linkedin_not_configured: 'LinkedIn isn’t set up for this app yet.',
  portfolio_not_configured: 'Publishing to the portfolio isn’t set up yet.',
  judge_uncalibrated: 'Label the evaluation set first, in Settings > System.',
  ai_unavailable: 'The AI models aren’t set up, so this can’t run yet.',
  storage_unavailable: 'File storage isn’t set up, so uploads and renders can’t run yet.',
  unknown_export_format: 'That export’s layout isn’t recognised. Use the snapshot form instead.',
  // Route codes the web acts on
  gates_blocking: 'Some gates still block it. Fix or override them first.',
  not_calibrated: 'Auto-publish needs a longer clean streak first.',
  slot_conflict: 'Another post already has that slot. Pick another one.',
  confirm_invalid: 'This page is out of date. Reload it, check the post and tap again.',
  not_approved: 'Approve it first.',
  approval_stale: 'It changed after you approved it. Review and approve it again.',
  meta_invalid: 'Some article details aren’t valid. Check the title, slug and description.',
  transition_not_allowed_here: 'That step happens elsewhere in the app, not here.',
  pillar_in_use: 'Something still uses this pillar. Move it first.',
  duplicate_source: 'That source is already on the list.',
  not_overridable: 'This finding can’t be overridden. Fix it instead.',
  report_stale: 'The text changed since the gates ran. Run them again first.',
  invalid_image: 'That image couldn’t be read. Try another file.',
  too_many_streams: 'Too many open tabs are following live updates. Close a few and reload.',
  publish_in_progress: 'A publishing run is still working on this article. Wait for it, or stop it in Settings > System.',
  mermaid_error: 'The diagram couldn’t be drawn. Check its Mermaid syntax.',
  diagram_too_long: 'The diagram is too long to draw. Shorten it.',
  plan_slot_taken: 'Another post already fills that slot of the plan.',
};

/** Sentences by status, for codes the table above does not know. */
function messageForStatus(status: number): string | undefined {
  switch (status) {
    case 404:
      return PROBLEM_MESSAGES.not_found;
    case 409:
      return 'It can’t be done in its current state. Reload and try again.';
    case 412:
      return PROBLEM_MESSAGES.etag_mismatch;
    case 413:
      return PROBLEM_MESSAGES.too_large;
    case 415:
      return PROBLEM_MESSAGES.unsupported_media_type;
    case 422:
      return PROBLEM_MESSAGES.validation_failed;
    case 428:
      return PROBLEM_MESSAGES.if_match_required;
    case 429:
      return 'Too many requests at once. Wait a moment and try again.';
    default:
      return status >= 500 ? 'The server had a problem. Try again in a moment.' : undefined;
  }
}

/**
 * One owner-facing sentence for any error, for toasts and inline error states: the server's
 * `detail`, else a sentence for the problem `code`, else one for the status. Errors that are not
 * API errors (bugs) get a generic sentence; nothing quotes response content.
 */
export function problemMessage(error: unknown): string {
  if (!(error instanceof ApiError)) return describeError(error);
  switch (error.kind) {
    case 'network':
    case 'unauthenticated':
    case 'forbidden':
      return describeError(error);
    case 'csrf':
    case 'not-found':
    case 'client':
    case 'server':
      break;
  }
  const detail = error.problem?.detail;
  if (detail) return detail;
  const code = error.code;
  if (code !== undefined) {
    const known = PROBLEM_MESSAGES[code];
    if (known) return known;
    if (code.endsWith('_unavailable')) return 'That service isn’t available right now. Try again in a moment.';
  }
  return messageForStatus(error.status) ?? describeError(error);
}

/** True when an error is not worth showing: a cancelled request. */
export function isSilentError(error: unknown): boolean {
  return isAbortError(error);
}

/**
 * True when the session handling already shows a screen for this error (sign-in, "Not
 * authorised"), so no toast or inline error should repeat it.
 */
export function isSessionError(error: unknown): boolean {
  return error instanceof ApiError && (error.kind === 'unauthenticated' || error.kind === 'forbidden');
}

// ------------------------------------------------------------------------------------------------
// Extension members
// ------------------------------------------------------------------------------------------------

function extension(error: unknown, name: string): unknown {
  return error instanceof ApiError ? error.problem?.extensions?.[name] : undefined;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [];
}

/**
 * `validation_failed` (422): the message per field, keyed by the field's path in the request body
 * (`title`, `meta.description`, `slots.0.time`). The first message wins when a field has several.
 */
export function fieldErrors(error: unknown): Readonly<Record<string, string>> {
  if (!isProblem(error, 'validation_failed')) return {};
  const errors = extension(error, 'errors');
  const result: Record<string, string> = {};
  if (!Array.isArray(errors)) return result;
  for (const entry of errors) {
    if (!isRecord(entry) || !Array.isArray(entry.loc)) continue;
    const loc = entry.loc.filter((part): part is string | number => typeof part === 'string' || typeof part === 'number');
    // FastAPI puts where the value came from first: body, query, path or header.
    const path = (['body', 'query', 'path', 'header'].includes(String(loc[0])) ? loc.slice(1) : loc).join('.');
    const message = typeof entry.msg === 'string' ? entry.msg : 'Invalid value.';
    if (path !== '' && !(path in result)) result[path] = message;
  }
  return result;
}

/** One blocklist finding of a `confidential_text` problem. It points at the text; it never holds the term. */
export interface ConfidentialFinding {
  /** The request field the text was in: `instruction`, or for interview answers `answers[2].text` (older servers: `answers.2`). */
  readonly field: string;
  readonly code: string;
  readonly severity: string;
  readonly message: string;
  /** Character offsets into that field's text, when the check knows them. */
  readonly start?: number;
  readonly end?: number;
  /** The blocklist term's id (never the term). */
  readonly ref?: string;
}

/** `confidential_text` (422): what to highlight, by field and span. */
export function confidentialFindings(error: unknown): readonly ConfidentialFinding[] {
  if (!isProblem(error, 'confidential_text')) return [];
  const findings = extension(error, 'findings');
  if (!Array.isArray(findings)) return [];
  return findings.flatMap((entry): ConfidentialFinding[] => {
    if (!isRecord(entry) || typeof entry.field !== 'string') return [];
    return [
      {
        field: entry.field,
        code: typeof entry.code === 'string' ? entry.code : 'blocklist',
        severity: typeof entry.severity === 'string' ? entry.severity : 'block',
        message: typeof entry.message === 'string' ? entry.message : PROBLEM_MESSAGES.confidential_text ?? '',
        ...(typeof entry.start === 'number' ? { start: entry.start } : {}),
        ...(typeof entry.end === 'number' ? { end: entry.end } : {}),
        ...(typeof entry.ref === 'string' ? { ref: entry.ref } : {}),
      },
    ];
  });
}

/** `meta_incomplete` (409): the frontmatter fields still unset. */
export function missingFields(error: unknown): readonly string[] {
  return isProblem(error, 'meta_incomplete') ? strings(extension(error, 'missing')) : [];
}

/** `invalid_transition` (409): the state machine, the edge that was refused, and the edges that exist. */
export interface RefusedTransition {
  readonly machine: string | undefined;
  readonly from: string | undefined;
  readonly to: string | undefined;
  readonly allowed: readonly string[];
}

export function refusedTransition(error: unknown): RefusedTransition | undefined {
  if (!isProblem(error, 'invalid_transition')) return undefined;
  const text = (name: string) => {
    const value = extension(error, name);
    return typeof value === 'string' ? value : undefined;
  };
  return { machine: text('machine'), from: text('from'), to: text('to'), allowed: strings(extension(error, 'allowed')) };
}

/**
 * `read_only_state` (409): the document's status (`state`) and who writes it now (`pipeline`,
 * `publish`, …). The member is `state` because `status` is the problem's HTTP status.
 */
export function readOnlyState(error: unknown): { readonly state?: string; readonly writer?: string } | undefined {
  if (!isProblem(error, 'read_only_state')) return undefined;
  const state = extension(error, 'state');
  const writer = extension(error, 'writer');
  return {
    ...(typeof state === 'string' ? { state } : {}),
    ...(typeof writer === 'string' ? { writer } : {}),
  };
}
