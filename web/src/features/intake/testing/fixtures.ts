import type { CheckResult, Finding, RedactionProposal } from '../types';

/**
 * Intake fixtures shaped like the server's answers (`schema.ts`). Spans are code-point offsets,
 * as the server sends them; these texts have no characters outside the BMP, so they equal the
 * browser's offsets.
 */

/** A raw answer with a blocking detail (an e-mail address) and two details to check. */
export const RAW_ANSWER = 'We moved 2,137 apps for Northwind Bank. Ask jane@northwind.example for the runbook.';

/** The anonymizer's wording for `RAW_ANSWER`. */
export const PROPOSED_WORDING = 'We moved 2,000+ apps for a regional bank. Ask the app owner for the runbook.';

export const MESSAGES = {
  email: 'Looks like an e-mail address. Remove it, or name the role instead.',
  figure: 'An exact figure. Round it into a bucket ("2,000+", "about 40%") unless it is public.',
  name: 'Possibly the name of a person or an organization. Keep it only if it is public.',
  blocklisted: 'Matches a blocklisted client name.',
} as const;

/** A finding whose span is the first occurrence of `needle` in `text`. */
export function findingAt(
  text: string,
  needle: string,
  code: string,
  severity: Finding['severity'],
  message: string,
): Finding {
  const start = text.indexOf(needle);
  if (start === -1) throw new Error(`"${needle}" is not in the text`);
  return { code, severity, message, start, end: start + needle.length, overridable: severity !== 'block' };
}

export const RAW_FINDINGS: Finding[] = [
  findingAt(RAW_ANSWER, '2,137', 'exact-figure', 'warn', MESSAGES.figure),
  findingAt(RAW_ANSWER, 'Northwind Bank', 'possible-name', 'warn', MESSAGES.name),
  findingAt(RAW_ANSWER, 'jane@northwind.example', 'email-address', 'block', MESSAGES.email),
];

export function redactionProposal(overrides: Partial<RedactionProposal> = {}): RedactionProposal {
  return {
    findings: RAW_FINDINGS,
    proposal: PROPOSED_WORDING,
    reason: null,
    anonymizerUsed: true,
    ...overrides,
  };
}

export function checkResult(overrides: Partial<CheckResult> = {}): CheckResult {
  return { ok: true, findings: [], ...overrides };
}
