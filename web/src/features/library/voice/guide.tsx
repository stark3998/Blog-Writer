import { BookOpen, CircleAlert, Pencil } from 'lucide-react';
import { useId, useState } from 'react';

import { Alert, AlertActions, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { DiffView } from '@/components/ui/diff-view';
import { isProblem, problemMessage } from '@/lib/api/problems';
import { useApiMutation, useApiQuery } from '@/lib/query/api-query';
import { queryKeys } from '@/lib/query/keys';

import { LoadError } from '../components/section-intro';
import { useMachines } from '../hooks/use-machines';
import { formatDate, plural } from '../lib/format';
import { GUIDE_STATUS_LABEL } from '../lib/labels';
import { canMove } from '../lib/machines';
import { refreshOnConflict } from '../lib/stale';
import type { Entry, GuideStatus, GuideVersionSummary, GuideView, StyleGuide } from '../lib/types';
import { GuideEditor } from './guide-editor';
import { changeCount, RULE_LIST_LABEL, rulesChange, rulesOf, type RulesChange, type RulesDraft } from './model';

const STATUS_VARIANT: Readonly<Record<GuideStatus, 'success' | 'warn' | 'neutral' | 'outline'>> = {
  active: 'success',
  proposed: 'warn',
  superseded: 'neutral',
  rejected: 'outline',
};

function wordCount(text: string): number {
  return text.trim() === '' ? 0 : text.trim().split(/\s+/).length;
}

/** The guide's prose: the first part, then the rest on request (it can run to thousands of words). */
function GuideBody({ body }: { body: string }) {
  const paragraphs = body.split(/\n{2,}/);
  const [open, setOpen] = useState(false);
  const head = paragraphs.slice(0, 3).join('\n\n');
  const tail = paragraphs.slice(3).join('\n\n');
  const prose = 'whitespace-pre-wrap break-words font-serif text-base leading-prose text-ink';
  if (tail === '') return <div className={prose}>{body}</div>;
  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <div className={prose}>{head}</div>
      <CollapsibleContent>
        <div className={`${prose} mt-6`}>{tail}</div>
      </CollapsibleContent>
      <CollapsibleTrigger asChild>
        <Button variant="link" className="mt-3">
          {open ? 'Show less' : `Show the whole guide (${plural(wordCount(body), 'word', 'words')})`}
        </Button>
      </CollapsibleTrigger>
    </Collapsible>
  );
}

function RuleList({ title, items }: { title: string; items: readonly string[] }) {
  if (items.length === 0) return null;
  return (
    <div>
      <h5 className="text-sm font-semibold text-ink">{title}</h5>
      <ul className="mt-1.5 list-disc space-y-1 pl-5 text-sm text-ink-muted">
        {items.map((item, index) => (
          <li key={index}>{item}</li>
        ))}
      </ul>
    </div>
  );
}

function RulesView({ rules }: { rules: RulesDraft }) {
  const vocabulary = rules.vocabulary.map((row) => (row.to.trim() ? `${row.from}: ${row.to}` : row.from));
  if (rules.prefer.length + rules.avoid.length + rules.bannedPhrases.length + vocabulary.length === 0) return null;
  return (
    <div className="grid gap-6 sm:grid-cols-2">
      <RuleList title="Prefer" items={rules.prefer} />
      <RuleList title="Avoid" items={rules.avoid} />
      <RuleList title="Never use" items={rules.bannedPhrases} />
      <RuleList title="Vocabulary" items={vocabulary} />
    </div>
  );
}

/** What a version adds to and takes from the rules, as kept and struck-through lines. */
function RulesChangeView({ change }: { change: RulesChange }) {
  const kinds = (Object.keys(change) as (keyof RulesChange)[]).filter(
    (kind) => change[kind].added.length + change[kind].removed.length > 0,
  );
  if (kinds.length === 0) return <p className="text-sm text-ink-muted">The rules are the same.</p>;
  return (
    <div className="grid gap-5 sm:grid-cols-2">
      {kinds.map((kind) => (
        <div key={kind}>
          <h5 className="text-sm font-semibold text-ink">{RULE_LIST_LABEL[kind]}</h5>
          <ul className="mt-1.5 space-y-1 text-sm">
            {change[kind].added.map((item, index) => (
              <li key={`added-${String(index)}`}>
                <ins className="rounded-[0.1875rem] bg-success-soft px-1 text-ink underline decoration-success decoration-2 underline-offset-2">
                  <span className="sr-only">added: </span>
                  {item}
                </ins>
              </li>
            ))}
            {change[kind].removed.map((item, index) => (
              <li key={`removed-${String(index)}`}>
                <del className="rounded-[0.1875rem] bg-danger-soft px-1 text-ink line-through decoration-danger">
                  <span className="sr-only">removed: </span>
                  {item}
                </del>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}

function GuideDetails({ guide }: { guide: StyleGuide }) {
  const exemplars = guide.exemplars ?? [];
  return (
    <div className="space-y-8">
      <GuideBody body={guide.body} />
      <RulesView rules={rulesOf(guide.rules)} />
      {exemplars.length > 0 ? (
        <div>
          <h5 className="text-sm font-semibold text-ink">{`Examples in your own words (${String(exemplars.length)})`}</h5>
          <p className="mt-1 text-sm text-ink-muted">Passages from your samples the drafter imitates. Flagged passages are never used.</p>
          <div className="mt-3 space-y-3">
            {exemplars.map((exemplar, index) => (
              <blockquote key={`${exemplar.sampleId}-${String(index)}`} className="border-l-2 border-line-strong pl-4 font-serif leading-prose text-ink-muted">
                {exemplar.excerpt}
              </blockquote>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}

interface ReviewCardProps {
  summary: GuideVersionSummary;
  active: Entry<StyleGuide> | null;
  onDone: () => void;
  /** Edit this version (a new version is saved from it). */
  onEdit: (base: Entry<StyleGuide>) => void;
}

/**
 * One version against the guide in use: what its text and rules change, and the owner's answer.
 * A proposed version is approved or rejected; an earlier one can be put back in use.
 */
function ReviewCard({ summary, active, onDone, onEdit }: ReviewCardProps) {
  const headingId = useId();
  const machines = useMachines();
  const version = useApiQuery({
    queryKey: queryKeys.styleGuides.detail(summary.id),
    path: '/api/voice/guide/{guide_id}',
    init: { params: { path: { guide_id: summary.id } } },
  });
  const activate = useApiMutation('post', '/api/voice/guide/{guide_id}/activate', {
    invalidates: [queryKeys.styleGuides.all, queryKeys.today],
    successToast: `Version ${String(summary.version)} is now your guide.`,
    errorToast: 'Couldn’t start using this version.',
    handles: ['confidential_text'],
    onError: refreshOnConflict([queryKeys.styleGuides.all]),
  });
  const reject = useApiMutation('post', '/api/voice/guide/{guide_id}/reject', {
    invalidates: [queryKeys.styleGuides.all],
    successToast: `Version ${String(summary.version)} rejected.`,
    errorToast: 'Couldn’t reject this version.',
    onError: refreshOnConflict([queryKeys.styleGuides.all]),
  });
  const proposed = summary.status === 'proposed';
  const title = proposed ? `Version ${String(summary.version)} is waiting for your approval` : `Version ${String(summary.version)}`;

  if (version.isPending) return <p className="text-sm text-ink-muted">{`Loading version ${String(summary.version)}…`}</p>;
  if (version.isError) {
    return (
      <LoadError
        error={version.error}
        title={`Couldn’t load version ${String(summary.version)}.`}
        retryLabel={`Try loading version ${String(summary.version)} again`}
        onRetry={() => void version.refetch()}
      />
    );
  }
  const entry = version.data;
  const guide = entry.item;
  const change = active ? rulesChange(rulesOf(active.item.rules), rulesOf(guide.rules)) : undefined;
  const canActivate = canMove(machines, 'style_guide', guide.status, 'active');
  const canReject = canMove(machines, 'style_guide', guide.status, 'rejected');
  const busy = activate.isPending || reject.isPending;

  return (
    <section aria-labelledby={headingId} className="space-y-5 rounded-lg border border-line-strong bg-sheet p-4 sm:p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h4 id={headingId} className="font-serif text-xl font-medium text-ink">
            {title}
          </h4>
          <p className="mt-1 text-sm text-ink-muted">
            {[
              guide.editedByOwner ? 'Your edit' : 'Built from your samples',
              guide.provisional ? 'provisional' : undefined,
              guide.basedOnVersion ? `based on version ${String(guide.basedOnVersion)}` : undefined,
              formatDate(guide.createdAt),
            ]
              .filter(Boolean)
              .join(', ')}
          </p>
        </div>
        <Badge variant={STATUS_VARIANT[guide.status]}>{GUIDE_STATUS_LABEL[guide.status]}</Badge>
      </div>

      {active && active.item.id !== guide.id ? (
        <div className="space-y-5">
          <div>
            <h5 className="text-sm font-semibold text-ink">{`Text, compared with version ${String(active.item.version)} in use`}</h5>
            <DiffView
              before={active.item.body}
              after={guide.body}
              context={1}
              label={`Changes to the guide's text in version ${String(guide.version)}`}
              className="mt-2 max-h-[28rem] overflow-y-auto rounded-md border border-line p-3 font-serif"
              tabIndex={0}
            />
          </div>
          <div>
            <h5 className="text-sm font-semibold text-ink">
              {change && changeCount(change) > 0 ? `Rules: ${plural(changeCount(change), 'change', 'changes')}` : 'Rules'}
            </h5>
            <div className="mt-2">{change ? <RulesChangeView change={change} /> : null}</div>
          </div>
        </div>
      ) : (
        <GuideDetails guide={guide} />
      )}

      {isProblem(activate.error, 'confidential_text') ? (
        <Alert variant="error">
          <AlertTitle>Part of this version matches your blocklist.</AlertTitle>
          <AlertDescription>
            <p>The guide goes into every drafting prompt, so it must clear the blocklist. Edit it, then approve the new version.</p>
          </AlertDescription>
        </Alert>
      ) : null}

      <div className="flex flex-wrap gap-2 border-t border-line pt-4">
        {canActivate ? (
          <Button
            disabled={busy}
            onClick={() => {
              activate.mutate({ params: { path: { guide_id: guide.id } }, ifMatch: entry.etag }, { onSuccess: onDone });
            }}
          >
            {activate.isPending ? 'Starting…' : proposed ? `Approve version ${String(guide.version)}` : `Use version ${String(guide.version)} again`}
          </Button>
        ) : null}
        {proposed && canReject ? (
          <Button
            variant="secondary"
            disabled={busy}
            onClick={() => {
              reject.mutate({ params: { path: { guide_id: guide.id } }, ifMatch: entry.etag }, { onSuccess: onDone });
            }}
          >
            {reject.isPending ? 'Rejecting…' : `Reject version ${String(guide.version)}`}
          </Button>
        ) : null}
        {proposed ? (
          <Button
            variant="ghost"
            disabled={busy}
            onClick={() => {
              onEdit(entry);
            }}
          >
            <Pencil aria-hidden="true" />
            Edit before approving
          </Button>
        ) : null}
        <Button variant="ghost" disabled={busy} onClick={onDone}>
          Close
        </Button>
      </div>
    </section>
  );
}

export interface GuidePanelProps {
  view: GuideView;
}

/** The guide in use, the versions waiting for approval, earlier versions and the editor. */
export function GuidePanel({ view }: GuidePanelProps) {
  const activeHeadingId = useId();
  const active = view.active;
  const versions = [...view.versions].sort((a, b) => b.version - a.version);
  const pending = versions.filter((version) => version.status === 'proposed');
  const [reviewing, setReviewing] = useState<string | undefined>();
  /** The version being edited, if any. */
  const [editing, setEditing] = useState<Entry<StyleGuide> | undefined>();
  const reviewed = versions.find((version) => version.id === (reviewing ?? pending[0]?.id));
  const shown = reviewing !== undefined || pending.length > 0 ? reviewed : undefined;
  const [closedPending, setClosedPending] = useState<string | undefined>();
  const card = shown && shown.id !== closedPending ? shown : undefined;

  const closeReview = () => {
    if (reviewing === undefined && pending[0]) setClosedPending(pending[0].id);
    setReviewing(undefined);
  };

  return (
    <div className="space-y-8">
      {pending.length > 1 ? (
        <Alert variant="warn" icon={<CircleAlert aria-hidden="true" />}>
          <AlertTitle>{`${plural(pending.length, 'version is', 'versions are')} waiting for your approval.`}</AlertTitle>
          <AlertDescription>
            <p>Review the newest first; approving one leaves the others as they are.</p>
          </AlertDescription>
        </Alert>
      ) : null}

      {card && !editing ? (
        <ReviewCard
          key={card.id}
          summary={card}
          active={active}
          onDone={closeReview}
          onEdit={(base) => {
            setEditing(base);
          }}
        />
      ) : null}
      {!card && pending[0] && closedPending === pending[0].id ? (
        <Alert variant="warn">
          <AlertTitle>{`Version ${String(pending[0].version)} is waiting for your approval.`}</AlertTitle>
          <AlertActions>
            <Button
              size="sm"
              variant="secondary"
              onClick={() => {
                setClosedPending(undefined);
              }}
            >
              Review it
            </Button>
          </AlertActions>
        </Alert>
      ) : null}

      {editing ? (
        <GuideEditor
          base={editing}
          onCancel={() => {
            setEditing(undefined);
          }}
          onSaved={(guide) => {
            setEditing(undefined);
            setClosedPending(undefined);
            setReviewing(guide.id);
          }}
        />
      ) : active ? (
        <article aria-labelledby={activeHeadingId} className="space-y-6">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h4 id={activeHeadingId} className="flex items-center gap-2 font-serif text-xl font-medium text-ink">
                <BookOpen aria-hidden="true" className="size-5 text-ink-subtle" />
                {`Version ${String(active.item.version)}, in use`}
              </h4>
              <p className="mt-1 text-sm text-ink-muted">
                {[
                  active.item.approvedAt ? `Approved ${formatDate(active.item.approvedAt) ?? ''}` : undefined,
                  active.item.sourceSampleIds?.length ? `built from ${plural(active.item.sourceSampleIds.length, 'sample', 'samples')}` : undefined,
                  active.item.editedByOwner ? 'edited by you' : undefined,
                  active.item.provisional ? 'provisional: built without your LinkedIn posts' : undefined,
                ]
                  .filter(Boolean)
                  .join(', ')}
              </p>
            </div>
            <Button
              variant="secondary"
              onClick={() => {
                setEditing(active);
              }}
            >
              <Pencil aria-hidden="true" />
              Edit the guide
            </Button>
          </div>
          <GuideDetails guide={active.item} />
        </article>
      ) : (
        <p className="max-w-measure text-ink-muted">
          No guide in use yet. Once it is built, approve it here; drafts then follow it, and every later change waits for your approval too.
        </p>
      )}

      {versions.length > 0 ? (
        <div>
          <h4 className="text-sm font-semibold text-ink">Versions</h4>
          <ol className="mt-2 divide-y divide-line border-y border-line">
            {versions.map((version) => {
              const reviewable = version.status === 'proposed' || version.status === 'superseded';
              return (
                <li key={version.id} className="flex flex-wrap items-center gap-x-4 gap-y-2 py-3">
                  <span className="min-w-24 font-medium text-ink">{`Version ${String(version.version)}`}</span>
                  <Badge variant={STATUS_VARIANT[version.status]}>{GUIDE_STATUS_LABEL[version.status]}</Badge>
                  <span className="flex-1 text-sm text-ink-muted">
                    {version.approvedAt ? `Approved ${formatDate(version.approvedAt) ?? ''}` : `Created ${formatDate(version.createdAt) ?? ''}`}
                  </span>
                  {reviewable ? (
                    <Button
                      variant="ghost"
                      size="sm"
                      aria-label={`${version.status === 'proposed' ? 'Review' : 'Compare'} version ${String(version.version)}`}
                      onClick={() => {
                        setClosedPending(undefined);
                        setReviewing(version.id);
                      }}
                    >
                      {version.status === 'proposed' ? 'Review' : 'Compare'}
                    </Button>
                  ) : null}
                </li>
              );
            })}
          </ol>
        </div>
      ) : null}
    </div>
  );
}

/** A sentence for a failed build: not enough samples, no blocklist, no AI-use confirmation. */
export function buildProblem(error: unknown): string | undefined {
  if (error === null || error === undefined) return undefined;
  if (isProblem(error, ['not_enough_samples', 'blocklist_not_configured', 'ai_use_not_confirmed', 'ai_unavailable'])) {
    return problemMessage(error);
  }
  return undefined;
}
