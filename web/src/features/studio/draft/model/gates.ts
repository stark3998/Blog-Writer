import type { Finding, GateName, GateResult, GatesView, Severity } from './types';

/**
 * The seven gates as the owner reads them (contract W-A8). The rules mirror
 * `domain.articles.GateResult` (`passed`, `overridable`): the server decides, the panel only
 * offers what it would accept.
 */

export const GATE_ORDER: readonly GateName[] = [
  'rubric',
  'fact_check',
  'confidentiality',
  'originality',
  'lint',
  'owner_signal',
  'outbound',
];

export interface GateInfo {
  /** The gate's name in the panel. */
  readonly name: string;
  /** What it checks, in one sentence. */
  readonly checks: string;
}

export const GATES: Readonly<Record<GateName, GateInfo>> = {
  rubric: {
    name: 'Quality',
    checks: 'A critique scores the draft: 8 overall, at least 6 on every dimension and 7 on first-hand detail.',
  },
  fact_check: {
    name: 'Facts',
    checks: 'Every claim maps to the brief or one of your stories, and each cited source is read again.',
  },
  confidentiality: {
    name: 'Confidentiality',
    checks: 'The blocklist, the classifier and the mosaic check, on the exact text that ships.',
  },
  originality: {
    name: 'Originality',
    checks: 'At most 200 quoted characters per source, few slop patterns, enough specifics, and something new.',
  },
  lint: {
    name: 'Formatting',
    checks: 'The article dialect: headings, alt text, citations, links that resolve and diagrams that render.',
  },
  owner_signal: {
    name: 'Your signal',
    checks: 'At least one of your stories, positions or interview answers.',
  },
  outbound: {
    name: 'Outbound links',
    checks: 'Links only to allowed domains, no tracking parameters or raw HTML, and clearance when required.',
  },
};

/** Gates that can never be overridden after a skipped or failed run (`domain.articles._HARD_GATES`). */
const HARD_GATES: ReadonlySet<GateName> = new Set<GateName>(['confidentiality', 'lint', 'outbound']);

/** Passed on its own (`pass` or `warn`). An override does not count. */
export function gatePassed(result: GateResult): boolean {
  return result.verdict === 'pass' || result.verdict === 'warn';
}

/** The owner may override it: it failed only on overridable findings (or a soft gate could not run). */
export function gateOverridable(result: GateResult): boolean {
  if (gatePassed(result)) return false;
  if (result.verdict === 'skipped' || result.verdict === 'error') return !HARD_GATES.has(result.gate);
  const blockers = (result.findings ?? []).filter((finding) => finding.severity === 'block');
  return blockers.length > 0 && blockers.every((finding) => finding.overridable);
}

/** Clears the gate for a manual publish: passed, or overridden where overriding is allowed. */
export function gateCleared(result: GateResult): boolean {
  return gatePassed(result) || ((result.override ?? null) !== null && gateOverridable(result));
}

export type GateState = 'pass' | 'warn' | 'fail' | 'skipped' | 'error' | 'overridden' | 'not-run';

export function gateState(result: GateResult | undefined): GateState {
  if (!result) return 'not-run';
  if (!gatePassed(result) && (result.override ?? null) !== null && gateOverridable(result)) return 'overridden';
  return result.verdict;
}

export const GATE_STATE_LABELS: Readonly<Record<GateState, string>> = {
  pass: 'Passed',
  warn: 'Passed with warnings',
  fail: 'Failed',
  skipped: 'Didn’t run',
  error: 'Couldn’t run',
  overridden: 'Overridden',
  'not-run': 'Not run yet',
};

/** The rubric's dimensions and the score each needs (gate 1). */
export const RUBRIC_OVERALL_MIN = 8;
export const RUBRIC_DIMENSIONS: readonly { readonly id: string; readonly label: string; readonly min: number }[] = [
  { id: 'clarity', label: 'Clarity', min: 6 },
  { id: 'structure', label: 'Structure', min: 6 },
  { id: 'accuracy', label: 'Accuracy', min: 6 },
  { id: 'depth', label: 'Depth', min: 6 },
  { id: 'first_hand_specificity', label: 'First-hand detail', min: 7 },
  { id: 'voice', label: 'Voice', min: 6 },
  { id: 'originality', label: 'Originality', min: 6 },
  { id: 'actionability', label: 'Actionability', min: 6 },
];

export const SEVERITY_LABELS: Readonly<Record<Severity, string>> = {
  block: 'Blocks',
  warn: 'Warning',
  info: 'Note',
};

/** Severity first (blockers on top), then position in the text. */
export function sortFindings(findings: readonly Finding[]): Finding[] {
  const rank: Record<Severity, number> = { block: 0, warn: 1, info: 2 };
  return [...findings].sort(
    (a, b) => rank[a.severity] - rank[b.severity] || (a.start ?? Number.MAX_SAFE_INTEGER) - (b.start ?? Number.MAX_SAFE_INTEGER),
  );
}

/** True when the finding points at a span of the body. */
export function hasSpan(finding: Finding): finding is Finding & { start: number; end: number } {
  return typeof finding.start === 'number' && typeof finding.end === 'number' && finding.end >= finding.start;
}

export interface GatesSummary {
  readonly tone: 'success' | 'warn' | 'danger' | 'neutral';
  readonly title: string;
  readonly detail: string;
}

/** One sentence about the whole report: what the owner can do with this version. */
export function summarizeGates(view: GatesView | undefined): GatesSummary {
  if (!view?.report) {
    return {
      tone: 'neutral',
      title: 'The gates haven’t run on this article yet.',
      detail: 'They run at the end of every pipeline run. Run them yourself after editing.',
    };
  }
  if (view.stale) {
    return {
      tone: 'warn',
      title: 'The text changed since the gates ran.',
      detail: 'The report is for an older version. Run the gates again before approving.',
    };
  }
  if (view.autoPublishReady) {
    return {
      tone: 'success',
      title: 'All seven gates passed.',
      detail: 'This version can be approved, and it would qualify for auto-publish.',
    };
  }
  if (view.manualPublishReady) {
    return {
      tone: 'success',
      title: 'Ready to approve.',
      detail: 'Every gate passed or was overridden, so you can approve it by hand.',
    };
  }
  const names = view.blocking.map((gate) => GATES[gate].name);
  return {
    tone: 'danger',
    title: names.length === 1 ? '1 gate blocks approval.' : `${String(names.length)} gates block approval.`,
    detail: `${joinNames(names)} ${names.length === 1 ? 'needs' : 'need'} a fix or, where allowed, an override.`,
  };
}

function joinNames(names: readonly string[]): string {
  if (names.length <= 1) return names[0] ?? '';
  return `${names.slice(0, -1).join(', ')} and ${names.at(-1) ?? ''}`;
}
