import { CircleCheck, CircleDashed, CircleSlash, CircleX, Clock, ShieldAlert, type LucideIcon } from 'lucide-react';

import { confidentialFindings, fieldErrors, PROBLEM_MESSAGES } from '@/lib/api/problems';
import type { components } from '@/lib/api/schema';

import type { StatusTone } from '../piece/piece-status';

/**
 * The research side of a piece as data: run states and their sentences, which brief to show,
 * citations, the two-source marker, footnote markers in quick-search answers, and the run form's
 * rules. Pure, so it is tested in Node.
 */

type Schemas = components['schemas'];
export type ResearchRun = Schemas['ResearchRun'];
export type ResearchStatus = Schemas['ResearchStatus'];
export type Brief = Schemas['Brief'];
export type BriefFinding = Schemas['BriefFinding'];
export type BriefStat = Schemas['BriefStat'];
export type BriefClaim = Schemas['BriefClaim'];
export type Quote = Schemas['Quote'];
export type Citation = Schemas['Citation'];
export type QuickSearch = Schemas['QuickSearch'];
export type BriefEditIn = Schemas['BriefEditIn'];
export type RunIn = Schemas['RunIn'];

/** The research job's steps (`research.run`), in the owner's words. */
export const RESEARCH_STEP_LABELS: Readonly<Record<string, string>> = {
  plan: 'Plan the questions',
  search: 'Search trusted sites and the open web',
  extract: 'Read the sources',
  verify: 'Check dates, numbers and conflicts',
  brief: 'Write the brief',
  audit: 'Check everything against your blocklist',
};

export interface RunStatusMeta {
  readonly label: string;
  readonly tone: StatusTone;
  readonly icon: LucideIcon;
}

export const RUN_STATUS: Readonly<Record<ResearchStatus, RunStatusMeta>> = {
  queued: { label: 'Queued', tone: 'progress', icon: Clock },
  running: { label: 'Running', tone: 'progress', icon: CircleDashed },
  succeeded: { label: 'Done', tone: 'success', icon: CircleCheck },
  failed: { label: 'Failed', tone: 'danger', icon: CircleX },
  quarantined: { label: 'Stopped', tone: 'danger', icon: ShieldAlert },
  cancelled: { label: 'Cancelled', tone: 'muted', icon: CircleSlash },
};

export function isActiveRun(run: Pick<ResearchRun, 'status'>): boolean {
  return run.status === 'queued' || run.status === 'running';
}

const RUN_ERRORS: Readonly<Record<string, string>> = {
  cancelled: 'You cancelled this run.',
  no_sources: 'The research found no usable sources for this topic. Try a broader topic, or add sources you trust.',
  blocklist_hit:
    'A term from your blocklist turned up in the research, so the run was stopped and its brief deleted. Rephrase the topic or check the sources.',
  blocklist_not_configured: PROBLEM_MESSAGES.blocklist_not_configured ?? '',
  ai_unavailable: 'The AI models aren’t set up, so research can’t run yet.',
  run_missing: 'The run went missing while it worked. Start a new one.',
  brief_missing: 'The brief went missing before the final check. Start a new run.',
  run_state: 'The run stopped in an unexpected state. Start a new one.',
};

/** What stopped a run, in one sentence (`errorCode`, or `quarantineReason`). */
export function runErrorSentence(run: Pick<ResearchRun, 'status' | 'errorCode' | 'quarantineReason'>): string {
  const code = run.quarantineReason ?? run.errorCode ?? undefined;
  if (code) return RUN_ERRORS[code] ?? PROBLEM_MESSAGES[code] ?? 'The run stopped before it finished. Start a new one.';
  if (run.status === 'cancelled') return RUN_ERRORS.cancelled ?? '';
  if (run.status === 'quarantined') return RUN_ERRORS.blocklist_hit ?? '';
  return 'The run stopped before it finished. Start a new one.';
}

/** Newest first, by when the run was created. */
export function newestFirst<T extends Pick<ResearchRun, 'createdAt'>>(runs: readonly T[]): T[] {
  return runs.toSorted((a, b) => (a.createdAt < b.createdAt ? 1 : a.createdAt > b.createdAt ? -1 : 0));
}

/**
 * The brief to show first: the one the draft uses (`article.briefId`, set by the pipeline's own
 * research), else the newest finished run's.
 */
export function defaultBriefId(linkedBriefId: string | null | undefined, runs: readonly ResearchRun[]): string | undefined {
  if (linkedBriefId) return linkedBriefId;
  return newestFirst(runs).find((run) => run.status === 'succeeded' && run.briefId)?.briefId ?? undefined;
}

/** The newest run, when it ended without a brief and nothing newer replaced it (its problem leads the tab). */
export function failedLatestRun(runs: readonly ResearchRun[]): ResearchRun | undefined {
  const latest = newestFirst(runs)[0];
  if (!latest) return undefined;
  return latest.status === 'failed' || latest.status === 'quarantined' || latest.status === 'cancelled'
    ? latest
    : undefined;
}

export function citationMap(citations: readonly Citation[] | undefined): ReadonlyMap<string, Citation> {
  return new Map((citations ?? []).map((citation) => [citation.id, citation]));
}

/** The citations behind a finding, stat or claim, in order, without unknown ids. */
export function citationsFor(ids: readonly string[] | undefined, map: ReadonlyMap<string, Citation>): Citation[] {
  const seen = new Set<string>();
  const result: Citation[] = [];
  for (const id of ids ?? []) {
    const citation = map.get(id);
    if (!citation || seen.has(id)) continue;
    seen.add(id);
    result.push(citation);
  }
  return result;
}

/**
 * The two-source rule for numbers: a key statistic needs two independent sources (W-A5 `verify`).
 * `confirmed` is the server's verdict (`key`); `sources` counts the distinct citations.
 */
export function statSupport(stat: Pick<BriefStat, 'key' | 'citationIds'>): { confirmed: boolean; sources: number } {
  const sources = new Set(stat.citationIds ?? []).size;
  return { confirmed: stat.key && sources >= 2, sources };
}

/** How many findings, numbers and claims rest on each citation (the cost of removing it). */
export function citationUse(brief: Pick<Brief, 'findings' | 'stats' | 'claims'>): ReadonlyMap<string, number> {
  const use = new Map<string, number>();
  const add = (id: string) => use.set(id, (use.get(id) ?? 0) + 1);
  for (const finding of brief.findings ?? []) for (const id of new Set(finding.citationIds ?? [])) add(id);
  for (const stat of brief.stats ?? []) for (const id of new Set(stat.citationIds ?? [])) add(id);
  for (const claim of brief.claims ?? []) add(claim.citationId);
  return use;
}

/** A part of a quick-search answer: text, or a footnote marker (`[^src-id]`) that resolved to a citation. */
export type AnswerPart =
  | { readonly type: 'text'; readonly text: string }
  | { readonly type: 'cite'; readonly citation: Citation; readonly number: number };

const MARKER = /\[\^([a-z0-9][a-z0-9-]*)\]/g;

/**
 * Splits an answer into text and footnote markers. Markers are numbered by the citation's place in
 * `citations` (the order the sources are listed in); unknown markers are dropped.
 */
export function answerParts(answer: string, citations: readonly Citation[]): AnswerPart[] {
  const numbers = new Map(citations.map((citation, index) => [citation.id, { citation, number: index + 1 }]));
  const parts: AnswerPart[] = [];
  let last = 0;
  for (const match of answer.matchAll(MARKER)) {
    const start = match.index;
    if (start > last) parts.push({ type: 'text', text: answer.slice(last, start) });
    last = start + match[0].length;
    const found = numbers.get(match[1] ?? '');
    if (!found) continue;
    // "… as reported. [^a]" reads "… as reported.¹": the marker hugs the sentence.
    const previous = parts.at(-1);
    if (previous?.type === 'text') parts[parts.length - 1] = { type: 'text', text: previous.text.trimEnd() };
    parts.push({ type: 'cite', ...found });
  }
  if (last < answer.length) parts.push({ type: 'text', text: answer.slice(last) });
  return parts.filter((part) => part.type !== 'text' || part.text !== '');
}

/** How recent the sources may be (`recencyDays`). */
export const RECENCY_OPTIONS: readonly { readonly days: number; readonly label: string }[] = [
  { days: 30, label: 'Last 30 days' },
  { days: 90, label: 'Last 3 months' },
  { days: 180, label: 'Last 6 months' },
  { days: 365, label: 'Last year' },
  { days: 730, label: 'Last 2 years' },
  { days: 1825, label: 'Last 5 years' },
];

/** News analysis looks back 30 days, everything else a year (the pipeline's own rule). */
export function defaultRecencyDays(format: string | null | undefined): number {
  return format === 'news-analysis' ? 30 : 365;
}

/** `RunIn` limits (W-A5). */
export const TOPIC_MIN = 3;
export const TOPIC_MAX = 300;
export const AUDIENCE_MAX = 300;
export const MAX_OWNER_URLS = 10;

/** Why a source link cannot be used, or `undefined` when it can (https only, a host, no spaces). */
export function ownerUrlProblem(value: string): string | undefined {
  const text = value.trim();
  if (text === '') return undefined;
  let url: URL;
  try {
    url = new URL(text);
  } catch {
    return 'Enter the whole link, with https:// at the start.';
  }
  if (url.protocol !== 'https:') return 'Use an https:// link.';
  if (!url.hostname.includes('.')) return 'That link has no proper domain.';
  return undefined;
}

/** The owner's source links to send: trimmed, without blanks and duplicates. */
export function cleanOwnerUrls(values: readonly string[]): string[] {
  return [...new Set(values.map((value) => value.trim()).filter((value) => value !== ''))];
}

/** `ownerUrls[0]` and `ownerUrls.0` name the same field; the form uses the dotted form. */
export function fieldPath(field: string): string {
  return field.replace(/\[(\d+)\]/g, '.$1');
}

/**
 * The messages a refused write gives per field: `validation_failed` paths and `confidential_text`
 * findings (which point at the text, never the term), both as dotted paths (`ownerUrls.2`).
 */
export function problemsByField(error: unknown): Readonly<Record<string, string>> {
  const result: Record<string, string> = {};
  for (const [field, message] of Object.entries(fieldErrors(error))) result[fieldPath(field)] = message;
  for (const finding of confidentialFindings(error)) {
    const path = fieldPath(finding.field);
    result[path] ??= 'This matches a term on your blocklist. Rephrase it.';
  }
  return result;
}

/** The message for one item of an edited list (`findings.2`, `findings.2.text`). */
export function itemProblem(problems: Readonly<Record<string, string>>, list: string, index: number): string | undefined {
  return problems[`${list}.${String(index)}`] ?? problems[`${list}.${String(index)}.text`];
}
