import { confidentialFindings, fieldErrors } from '@/lib/api/problems';

/** Error messages by request path (`pillars.0.hashtags.1`, `metrics.2.label`, `title`). */
export type ErrorMap = Readonly<Record<string, string>>;

export const NO_ERRORS: ErrorMap = Object.freeze({});

/** `metrics[0].label` (how some routes name fields) as `metrics.0.label`. */
export function normalizePath(field: string): string {
  return field.replace(/\[(\d+)\]/g, '.$1');
}

/**
 * What a failed save said about individual fields: `validation_failed` messages and the
 * blocklist's `confidential_text` findings (which point at the field, never at the term).
 */
export function serverFieldErrors(error: unknown): ErrorMap {
  const result: Record<string, string> = { ...fieldErrors(error) };
  for (const finding of confidentialFindings(error)) {
    const path = normalizePath(finding.field);
    if (!(path in result)) result[path] = finding.message;
  }
  return result;
}

/** The errors whose path starts with `prefix.`, keyed by the rest of the path. */
export function errorsUnder(errors: ErrorMap, prefix: string): ErrorMap {
  const start = `${prefix}.`;
  const result: Record<string, string> = {};
  for (const [path, message] of Object.entries(errors)) {
    if (path === prefix) result[''] = message;
    else if (path.startsWith(start)) result[path.slice(start.length)] = message;
  }
  return result;
}

/** One map from several; the first message for a path wins. */
export function mergeErrors(...maps: readonly ErrorMap[]): ErrorMap {
  const result: Record<string, string> = {};
  for (const map of maps) {
    for (const [path, message] of Object.entries(map)) {
      if (!(path in result)) result[path] = message;
    }
  }
  return result;
}

/** Per-item errors of a list field (`hashtags.1` and the like) by item index. */
export function itemErrors(errors: ErrorMap, field: string): Readonly<Record<number, string>> {
  const result: Record<number, string> = {};
  for (const [path, message] of Object.entries(errorsUnder(errors, field))) {
    const index = /^(\d+)(?:\.|$)/.exec(path)?.[1];
    if (index !== undefined && !(Number(index) in result)) result[Number(index)] = message;
  }
  return result;
}

export function hasErrors(errors: ErrorMap): boolean {
  return Object.keys(errors).length > 0;
}
