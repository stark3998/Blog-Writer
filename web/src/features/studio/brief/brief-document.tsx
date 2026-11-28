import { CheckCheck, ChevronDown, CircleHelp, Pencil, Trash2 } from 'lucide-react';
import { useId, useState, type ReactNode } from 'react';
import { flushSync } from 'react-dom';

import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { Field } from '@/components/ui/field';
import { Textarea } from '@/components/ui/textarea';
import { ConflictDialog, useConflict } from '@/features/data/conflict-dialog';
import { ProblemAlert } from '@/features/data/problem-alert';
import { isProblem } from '@/lib/api/problems';
import { cn } from '@/lib/cn';

import { formatDate, formatPartialDate } from '../piece/format';
import { PillarBadge } from '../piece/piece-chips';
import { usePillars } from '../piece/piece-queries';
import { pillarView } from '../piece/pillars';

import {
  citationMap,
  citationsFor,
  citationUse,
  problemsByField,
  statSupport,
  type Brief,
  type BriefClaim,
  type BriefEditIn,
  type Citation,
  type ResearchRun,
} from './brief-model';
import { useEditBrief } from './brief-queries';
import { CitationChip, CitationChips, safeHref, sourceLabel } from './citation-chip';
import { ItemsEditor, type EditItem } from './items-editor';
import { sourcesRead } from './run-history';

/** The parts of a brief the owner edits, one at a time. */
type SectionId = 'summary' | 'findings' | 'counterpoints' | 'openQuestions' | 'angles' | 'hookSeeds' | 'sources' | 'claims';

/** `BriefEditIn` limits (W-A5). */
const LIMITS = {
  summary: 4000,
  finding: 600,
  findings: 40,
  counterpoint: 600,
  short: 300,
  items: 20,
} as const;

interface PendingSave {
  readonly section: SectionId;
  readonly body: BriefEditIn;
  /** The etag the save was sent with (the conflict's "theirs" is the version after it). */
  readonly sentEtag: string | null;
  /** The owner's version of the section, as text, for the conflict dialog's comparison. */
  readonly mine: string | undefined;
  readonly onSaved: (() => void) | undefined;
  readonly onDiscard: (() => void) | undefined;
}

/** A section's current text, for comparing the owner's version with the saved one. */
function sectionText(brief: Brief, section: SectionId): string | undefined {
  switch (section) {
    case 'summary':
      return brief.executiveSummary;
    case 'findings':
      return (brief.findings ?? []).map((finding) => finding.text).join('\n\n');
    case 'counterpoints':
      return (brief.counterpoints ?? []).join('\n\n');
    case 'openQuestions':
      return (brief.openQuestions ?? []).join('\n\n');
    case 'angles':
      return (brief.angles ?? []).join('\n\n');
    case 'hookSeeds':
      return (brief.hookSeeds ?? []).join('\n\n');
    case 'sources':
    case 'claims':
      return undefined;
  }
}

const SECTION_SUBJECT: Readonly<Record<SectionId, string>> = {
  summary: 'The summary',
  findings: 'The findings',
  counterpoints: 'The counterpoints',
  openQuestions: 'The open questions',
  angles: 'The angles',
  hookSeeds: 'The hook ideas',
  sources: 'The brief',
  claims: 'The brief',
};

function SectionHeading({
  id,
  title,
  count,
  action,
}: {
  id: string;
  title: string;
  count?: number | undefined;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-2">
      <h3 id={id} tabIndex={-1} className="text-lg font-semibold text-ink outline-none">
        {title}
        {count !== undefined ? <span className="ml-2 font-normal tabular-nums text-ink-subtle">{count}</span> : null}
      </h3>
      {action}
    </div>
  );
}

function EditButton({ label, onClick, disabled }: { label: string; onClick: () => void; disabled: boolean }) {
  return (
    <Button size="sm" variant="ghost" onClick={onClick} disabled={disabled}>
      <Pencil aria-hidden="true" />
      {label}
    </Button>
  );
}

interface SectionProps {
  brief: Brief;
  citations: ReadonlyMap<string, Citation>;
  /** True while another section saves: one edit at a time. */
  busy: boolean;
  saving: boolean;
  problems: Readonly<Record<string, string>>;
  /** A refused save of this section that the section explains (blocklist, validation). */
  error?: unknown;
  save: (section: SectionId, body: BriefEditIn, options: { mine?: string; onSaved?: () => void; onDiscard?: () => void }) => void;
  /** Drops a refused save's messages (the owner cancelled the edit). */
  forget: () => void;
}

/** The body that replaces one list of short texts. */
function listBody(section: 'counterpoints' | 'openQuestions' | 'angles' | 'hookSeeds', texts: string[]): BriefEditIn {
  switch (section) {
    case 'counterpoints':
      return { counterpoints: texts };
    case 'openQuestions':
      return { openQuestions: texts };
    case 'angles':
      return { angles: texts };
    case 'hookSeeds':
      return { hookSeeds: texts };
  }
}

/**
 * Leaves an editor and puts focus on its section's heading: the controls that had it are gone
 * (an action that removes the focused element moves focus somewhere sensible).
 */
function leaveTo(headingId: string, update: () => void): void {
  flushSync(update);
  document.getElementById(headingId)?.focus();
}

function SaveProblem({ error }: { error: unknown }) {
  if (error === undefined) return null;
  return <ProblemAlert error={error} title="Your change wasn’t saved." />;
}

function SummarySection({ brief, busy, saving, problems, error, save, forget }: SectionProps) {
  const headingId = useId();
  const [draft, setDraft] = useState<string | undefined>(undefined);
  const editing = draft !== undefined;
  const tooLong = (draft ?? '').length > LIMITS.summary;
  const empty = (draft ?? '').trim() === '';
  return (
    <section aria-labelledby={headingId}>
      <SectionHeading
        id={headingId}
        title="Summary"
        action={
          editing ? null : (
            <EditButton
              label="Edit the summary"
              disabled={busy}
              onClick={() => {
                setDraft(brief.executiveSummary);
              }}
            />
          )
        }
      />
      {editing ? (
        <div className="mt-3 grid gap-3">
          <Field
            label="Summary"
            hideLabel
            error={empty ? 'Write a summary.' : tooLong ? `Keep it to ${String(LIMITS.summary)} characters.` : problems.executiveSummary}
          >
            <Textarea
              value={draft}
              maxLength={LIMITS.summary}
              rows={6}
              className="font-serif text-lg leading-prose"
              onChange={(event) => {
                setDraft(event.target.value);
              }}
            />
          </Field>
          <SaveProblem error={error} />
          <div className="flex justify-end gap-2">
            <Button
              size="sm"
              variant="secondary"
              disabled={saving}
              onClick={() => {
                leaveTo(headingId, () => {
                  setDraft(undefined);
                });
                forget();
              }}
            >
              Cancel
            </Button>
            <Button
              size="sm"
              disabled={saving || empty || tooLong}
              onClick={() => {
                const text = draft.trim();
                save(
                  'summary',
                  { executiveSummary: text },
                  {
                    mine: text,
                    onSaved: () => {
                      leaveTo(headingId, () => {
                        setDraft(undefined);
                      });
                    },
                    onDiscard: () => {
                      setDraft(undefined);
                    },
                  },
                );
              }}
            >
              {saving ? 'Saving…' : 'Save'}
            </Button>
          </div>
        </div>
      ) : (
        <p className="mt-3 max-w-measure font-serif text-lg leading-prose text-ink">{brief.executiveSummary}</p>
      )}
    </section>
  );
}

function FindingsSection({ brief, citations, busy, saving, problems, error, save, forget }: SectionProps) {
  const headingId = useId();
  const [editing, setEditing] = useState(false);
  const findings = brief.findings ?? [];
  return (
    <section aria-labelledby={headingId}>
      <SectionHeading
        id={headingId}
        title="Findings"
        count={findings.length}
        action={
          editing || findings.length === 0 ? null : (
            <EditButton
              label="Edit the findings"
              disabled={busy}
              onClick={() => {
                setEditing(true);
              }}
            />
          )
        }
      />
      {editing ? (
        <div className="mt-3">
          <ItemsEditor
            noun="Finding"
            list="findings"
            items={findings.map((finding) => ({ key: finding.id, text: finding.text }))}
            maxLength={LIMITS.finding}
            maxItems={LIMITS.findings}
            canAdd={false}
            saving={saving}
            problems={problems}
            error={error}
            onCancel={() => {
              leaveTo(headingId, () => {
                setEditing(false);
              });
              forget();
            }}
            onSave={(items) => {
              save(
                'findings',
                { findings: items.map((item) => ({ id: item.key, text: item.text })) },
                {
                  mine: items.map((item) => item.text).join('\n\n'),
                  onSaved: () => {
                    leaveTo(headingId, () => {
                      setEditing(false);
                    });
                  },
                  onDiscard: () => {
                    setEditing(false);
                  },
                },
              );
            }}
          />
        </div>
      ) : findings.length === 0 ? (
        <p className="mt-3 text-ink-muted">No findings are left in this brief.</p>
      ) : (
        <ul className="mt-3 grid max-w-measure gap-4">
          {findings.map((finding) => (
            <li key={finding.id} className="border-l-2 border-line-strong pl-4">
              <p className="text-ink">{finding.text}</p>
              <CitationChips citations={citationsFor(finding.citationIds, citations)} className="mt-2" />
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function StatsSection({ brief, citations }: Pick<SectionProps, 'brief' | 'citations'>) {
  const headingId = useId();
  const stats = brief.stats ?? [];
  if (stats.length === 0) return null;
  return (
    <section aria-labelledby={headingId}>
      <SectionHeading id={headingId} title="Numbers" count={stats.length} />
      <p className="mt-1 text-sm text-ink-muted">A key number needs two independent sources before the draft leans on it.</p>
      <ul className="mt-3 grid gap-3 sm:grid-cols-2">
        {stats.map((stat) => {
          const support = statSupport(stat);
          const asOf = formatPartialDate(stat.asOf);
          return (
            <li key={stat.id} className="flex flex-col gap-1 rounded-lg border border-line bg-sheet p-4">
              <p className="text-3xl font-semibold text-ink">{stat.value}</p>
              <p className="text-sm text-ink-muted">
                {stat.label}
                {asOf ? ` (as of ${asOf})` : ''}
              </p>
              <p
                className={cn(
                  'mt-1 flex items-center gap-1.5 text-sm font-medium',
                  support.confirmed ? 'text-success' : 'text-warn',
                )}
              >
                {support.confirmed ? (
                  <CheckCheck aria-hidden="true" className="size-4 shrink-0" />
                ) : (
                  <CircleHelp aria-hidden="true" className="size-4 shrink-0" />
                )}
                {support.confirmed
                  ? 'Two independent sources'
                  : support.sources >= 2
                    ? 'Sources not independent: check it'
                    : 'One source: check it'}
              </p>
              <CitationChips citations={citationsFor(stat.citationIds, citations)} className="mt-1" />
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function TextListSection({
  section,
  title,
  noun,
  items,
  maxLength,
  empty,
  ...props
}: SectionProps & {
  section: 'counterpoints' | 'openQuestions' | 'angles' | 'hookSeeds';
  title: string;
  noun: string;
  items: readonly string[];
  maxLength: number;
  empty: string;
}) {
  const headingId = useId();
  const [editing, setEditing] = useState(false);
  return (
    <section aria-labelledby={headingId}>
      <SectionHeading
        id={headingId}
        title={title}
        count={items.length}
        action={
          editing ? null : (
            <EditButton
              label={`Edit the ${title.toLowerCase()}`}
              disabled={props.busy}
              onClick={() => {
                setEditing(true);
              }}
            />
          )
        }
      />
      {editing ? (
        <div className="mt-3">
          <ItemsEditor
            noun={noun}
            list={section}
            items={items.map((text, index): EditItem => ({ key: `item-${String(index)}`, text }))}
            maxLength={maxLength}
            maxItems={LIMITS.items}
            canAdd
            saving={props.saving}
            problems={props.problems}
            error={props.error}
            onCancel={() => {
              leaveTo(headingId, () => {
                setEditing(false);
              });
              props.forget();
            }}
            onSave={(edited) => {
              const texts = edited.map((item) => item.text);
              props.save(section, listBody(section, texts), {
                mine: texts.join('\n\n'),
                onSaved: () => {
                  leaveTo(headingId, () => {
                    setEditing(false);
                  });
                },
                onDiscard: () => {
                  setEditing(false);
                },
              });
            }}
          />
        </div>
      ) : items.length === 0 ? (
        <p className="mt-3 text-ink-muted">{empty}</p>
      ) : (
        <ul className="mt-3 grid max-w-measure list-disc gap-2 pl-5 text-ink marker:text-ink-subtle">
          {items.map((text, index) => (
            <li key={`${String(index)}-${text.slice(0, 24)}`}>{text}</li>
          ))}
        </ul>
      )}
    </section>
  );
}

const CLAIM_STATUS: Readonly<Record<BriefClaim['status'], { label: string; className: string }>> = {
  verified: { label: 'Verified', className: 'text-success' },
  unverified: { label: 'Not verified', className: 'text-ink-subtle' },
  conflict: { label: 'Sources disagree', className: 'text-warn' },
};

function ClaimsSection({ brief, citations, busy, saving, save }: SectionProps) {
  const headingId = useId();
  const triggerId = useId();
  const claims = brief.claims ?? [];
  const quotes = brief.quotes ?? [];
  if (claims.length === 0 && quotes.length === 0) return null;
  return (
    <section aria-labelledby={headingId}>
      <Collapsible>
        <h3 id={headingId} className="text-lg font-semibold text-ink">
          <CollapsibleTrigger
            id={triggerId}
            className="group inline-flex items-center gap-2 rounded-sm text-left hover:text-ink-muted"
          >
            Claims and quotes
            <span className="font-normal tabular-nums text-ink-subtle">{claims.length + quotes.length}</span>
            <ChevronDown aria-hidden="true" className="size-4 transition-transform group-data-[state=open]:rotate-180" />
          </CollapsibleTrigger>
        </h3>
        <p className="mt-1 text-sm text-ink-muted">What the draft and the fact check rest on, claim by claim.</p>
        <CollapsibleContent>
          {claims.length > 0 ? (
            <ul className="mt-3 divide-y divide-line border-y border-line">
              {claims.map((claim, index) => {
                const status = CLAIM_STATUS[claim.status];
                const citation = citations.get(claim.citationId);
                return (
                  <li key={claim.id} className="flex items-start gap-3 py-3">
                    <div className="min-w-0 flex-1">
                      <p className="text-ink">{claim.text}</p>
                      <p className="mt-1 flex flex-wrap items-center gap-2 text-sm">
                        <span className={cn('font-medium', status.className)}>{status.label}</span>
                        {claim.conflictNote ? <span className="text-ink-muted">{claim.conflictNote}</span> : null}
                        {citation ? <CitationChip citation={citation} /> : null}
                      </p>
                    </div>
                    <Button
                      size="icon-sm"
                      variant="ghost"
                      disabled={busy || saving}
                      aria-label={`Remove claim ${String(index + 1)}`}
                      onClick={() => {
                        save(
                          'claims',
                          { removeClaimIds: [claim.id] },
                          {
                            onSaved: () => {
                              document.getElementById(triggerId)?.focus();
                            },
                          },
                        );
                      }}
                    >
                      <Trash2 aria-hidden="true" />
                    </Button>
                  </li>
                );
              })}
            </ul>
          ) : null}
          {quotes.length > 0 ? (
            <ul className="mt-4 grid gap-3">
              {quotes.map((quote) => {
                const citation = citations.get(quote.citationId);
                return (
                  <li key={quote.id}>
                    <blockquote className="border-l-2 border-line-strong pl-4 font-serif text-ink">{`“${quote.text}”`}</blockquote>
                    {citation ? <CitationChip citation={citation} className="mt-1 ml-4" /> : null}
                  </li>
                );
              })}
            </ul>
          ) : null}
        </CollapsibleContent>
      </Collapsible>
    </section>
  );
}

function SourcesSection({
  brief,
  run,
  busy,
  saving,
  save,
  onAddSources,
}: SectionProps & { run: ResearchRun | undefined; onAddSources: (() => void) | undefined }) {
  const headingId = useId();
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());
  const [confirming, setConfirming] = useState(false);
  const sources = brief.citations ?? [];
  const use = citationUse(brief);
  const read = run ? sourcesRead(run) : undefined;
  const count = selected.size;

  const toggle = (id: string, on: boolean) => {
    setSelected((current) => {
      const next = new Set(current);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });
  };

  return (
    <section aria-labelledby={headingId}>
      <SectionHeading id={headingId} title="Sources" count={sources.length} />
      {read ? <p className="mt-1 text-sm text-ink-muted">{`${read} in full; the rest come from search results.`}</p> : null}
      {sources.length === 0 ? (
        <p className="mt-3 text-ink-muted">No sources are left in this brief.</p>
      ) : (
        <ul className="mt-3 divide-y divide-line border-y border-line">
          {sources.map((source) => {
            const href = safeHref(source.url);
            const uses = use.get(source.id) ?? 0;
            const checkboxId = `${headingId}-${source.id}`;
            return (
              <li key={source.id} className="flex items-start gap-3 py-3">
                <Checkbox
                  id={checkboxId}
                  className="mt-1"
                  aria-label={`Select “${source.title}” to remove it`}
                  checked={selected.has(source.id)}
                  disabled={busy || saving}
                  onCheckedChange={(state) => {
                    toggle(source.id, state === true);
                  }}
                />
                <div className="min-w-0 flex-1">
                  {href ? (
                    <a
                      href={href}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="font-medium text-ink underline decoration-line-strong underline-offset-4 hover:decoration-ink"
                    >
                      {source.title}
                      <span className="sr-only"> (opens in a new tab)</span>
                    </a>
                  ) : (
                    <span className="font-medium text-ink">{source.title}</span>
                  )}
                  <p className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-ink-muted">
                    <span>{sourceLabel(source)}</span>
                    {source.allowlisted ? <Badge variant="success">Trusted source</Badge> : null}
                    <span>{uses === 0 ? 'Not cited yet' : `Cited ${String(uses)} ${uses === 1 ? 'time' : 'times'}`}</span>
                  </p>
                </div>
              </li>
            );
          })}
        </ul>
      )}
      <div className="mt-3 flex flex-wrap gap-2">
        {sources.length > 0 ? (
          <Button
            size="sm"
            variant="secondary"
            disabled={count === 0 || busy || saving}
            onClick={() => {
              setConfirming(true);
            }}
          >
            <Trash2 aria-hidden="true" />
            {count === 0 ? 'Remove sources' : `Remove ${String(count)} ${count === 1 ? 'source' : 'sources'}…`}
          </Button>
        ) : null}
        {onAddSources ? (
          <Button size="sm" variant="ghost" onClick={onAddSources}>
            Add sources
          </Button>
        ) : null}
      </div>
      <AlertDialog open={confirming} onOpenChange={setConfirming}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{`Remove ${String(count)} ${count === 1 ? 'source' : 'sources'} from the brief?`}</AlertDialogTitle>
            <AlertDialogDescription>
              Their claims and quotes go too, and numbers that rest only on them disappear. Findings keep their words but
              lose these citations. This can’t be undone; a new research run can find them again.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep them</AlertDialogCancel>
            <Button
              variant="danger"
              disabled={saving}
              onClick={() => {
                save('sources', { removeCitationIds: [...selected] }, {
                  onSaved: () => {
                    leaveTo(headingId, () => {
                      setSelected(new Set());
                      setConfirming(false);
                    });
                  },
                  onDiscard: () => {
                    setConfirming(false);
                  },
                });
                setConfirming(false);
              }}
            >
              Remove
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </section>
  );
}

export interface BriefDocumentProps {
  brief: Brief;
  etag: string | null;
  /** True when the draft is written from this brief (`article.briefId`). */
  linked: boolean;
  /** Says whether the brief can be the draft's; `undefined` for posts. */
  linkNote?: string | undefined;
  /** The run that wrote it, for "9 of 12 sources read". */
  run?: ResearchRun | undefined;
  /** Reloads the brief (after a conflict). */
  onReload: () => void;
  /** "Add sources": a new research run with the owner's links. */
  onAddSources?: (() => void) | undefined;
}

/**
 * A research brief: summary, cited findings, numbers with the two-source marker, counterpoints,
 * open questions, angles, hook ideas, claims and quotes, and its sources. The owner edits the
 * summaries and removes items (never adds quotes or citations, W-A5); every save sends the
 * brief's etag, and a 412 opens the conflict dialog with both versions.
 */
export function BriefDocument({ brief, etag, linked, linkNote, run, onReload, onAddSources }: BriefDocumentProps) {
  const headingId = useId();
  const pillars = usePillars();
  const edit = useEditBrief(brief.id);
  const conflict = useConflict();
  const [pending, setPending] = useState<PendingSave | null>(null);
  const citations = citationMap(brief.citations);
  const pillar = pillarView(brief.pillar, pillars.data?.item.pillars);
  const researched = formatDate(brief.createdAt);
  const saving = edit.isPending;
  const failed = pending !== null && edit.isError ? edit.error : undefined;
  const problems = failed && isProblem(failed, ['validation_failed', 'confidential_text']) ? problemsByField(failed) : {};

  const send = (save: PendingSave, ifMatch: string | null) => {
    edit.mutate(
      { params: { path: { brief_id: brief.id } }, body: save.body, ...(ifMatch ? { ifMatch } : {}) },
      {
        onSuccess: () => {
          save.onSaved?.();
          conflict.close();
          setPending(null);
        },
        onError: (error) => {
          if (conflict.show(error)) onReload();
        },
      },
    );
  };

  const save: SectionProps['save'] = (section, body, options) => {
    const next: PendingSave = {
      section,
      body,
      sentEtag: etag,
      mine: options.mine,
      onSaved: options.onSaved,
      onDiscard: options.onDiscard,
    };
    setPending(next);
    send(next, etag);
  };

  const forget = () => {
    setPending(null);
    edit.reset();
  };
  const shared = { brief, citations, busy: saving, saving, problems, save, forget };
  const sectionProblems = (section: SectionId) => (pending?.section === section ? problems : {});
  const shownError =
    failed !== undefined && isProblem(failed, ['validation_failed', 'confidential_text', 'unknown_finding'])
      ? failed
      : undefined;
  const sectionError = (section: SectionId) => (pending?.section === section ? shownError : undefined);
  // Once the reload brought the newer version, the dialog compares it with the owner's.
  const theirs = pending && etag !== pending.sentEtag ? sectionText(brief, pending.section) : undefined;

  return (
    <article aria-labelledby={headingId} className="grid gap-10">
      <header>
        <h2 id={headingId} className="font-serif text-2xl font-medium text-ink">
          Brief
        </h2>
        <p className="mt-1 max-w-measure text-ink-muted">{brief.topic}</p>
        <ul aria-label="About this brief" className="mt-3 flex flex-wrap items-center gap-2 text-sm">
          {linked ? (
            <li>
              <Badge variant="success">
                <CheckCheck aria-hidden="true" />
                Used for drafting
              </Badge>
            </li>
          ) : null}
          {pillar ? (
            <li>
              <PillarBadge pillar={pillar} />
            </li>
          ) : null}
          {brief.editedByOwner ? (
            <li>
              <Badge variant="outline">Edited by you</Badge>
            </li>
          ) : null}
          {researched ? (
            <li className="text-ink-muted">
              Researched <time dateTime={brief.createdAt}>{researched}</time>
            </li>
          ) : null}
        </ul>
        {linkNote ? <p className="mt-3 max-w-measure text-sm text-ink-muted">{linkNote}</p> : null}
      </header>

      <SummarySection {...shared} problems={sectionProblems('summary')} error={sectionError('summary')} />
      <FindingsSection {...shared} problems={sectionProblems('findings')} error={sectionError('findings')} />
      <StatsSection brief={brief} citations={citations} />
      <TextListSection
        {...shared}
        problems={sectionProblems('counterpoints')}
        error={sectionError('counterpoints')}
        section="counterpoints"
        title="Counterpoints"
        noun="Counterpoint"
        items={brief.counterpoints ?? []}
        maxLength={LIMITS.counterpoint}
        empty="No counterpoints. Add the strongest case against your argument."
      />
      <TextListSection
        {...shared}
        problems={sectionProblems('openQuestions')}
        error={sectionError('openQuestions')}
        section="openQuestions"
        title="Open questions"
        noun="Open question"
        items={brief.openQuestions ?? []}
        maxLength={LIMITS.short}
        empty="No open questions."
      />
      <TextListSection
        {...shared}
        problems={sectionProblems('angles')}
        error={sectionError('angles')}
        section="angles"
        title="Angles"
        noun="Angle"
        items={brief.angles ?? []}
        maxLength={LIMITS.short}
        empty="No angles yet."
      />
      <TextListSection
        {...shared}
        problems={sectionProblems('hookSeeds')}
        error={sectionError('hookSeeds')}
        section="hookSeeds"
        title="Hook ideas"
        noun="Hook idea"
        items={brief.hookSeeds ?? []}
        maxLength={LIMITS.short}
        empty="No hook ideas yet."
      />
      <ClaimsSection {...shared} problems={{}} />
      <SourcesSection {...shared} problems={{}} run={run} onAddSources={onAddSources} />

      <ConflictDialog
        {...conflict}
        subject={pending ? SECTION_SUBJECT[pending.section] : 'The brief'}
        mine={pending?.mine}
        theirs={pending?.mine !== undefined ? theirs : undefined}
        saving={saving}
        onKeepMine={() => {
          if (!pending) return;
          const current = conflict.conflict?.etag ?? etag;
          send({ ...pending, sentEtag: current }, current);
        }}
        onTakeTheirs={() => {
          pending?.onDiscard?.();
          setPending(null);
          conflict.close();
          onReload();
        }}
      />
    </article>
  );
}
