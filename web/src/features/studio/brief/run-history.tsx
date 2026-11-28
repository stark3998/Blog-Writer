import { ChevronDown } from 'lucide-react';
import { useId } from 'react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { cn } from '@/lib/cn';

import { formatCost, formatDateTime } from '../piece/format';
import type { StatusTone } from '../piece/piece-status';

import { RUN_STATUS, type ResearchRun } from './brief-model';

const TONE_BADGE = {
  neutral: 'neutral',
  progress: 'outline',
  attention: 'warn',
  success: 'success',
  danger: 'danger',
  muted: 'neutral',
} as const satisfies Record<StatusTone, 'neutral' | 'outline' | 'warn' | 'success' | 'danger'>;

export function RunStatusBadge({ run }: { run: Pick<ResearchRun, 'status'> }) {
  const meta = RUN_STATUS[run.status];
  const Icon = meta.icon;
  return (
    <Badge variant={TONE_BADGE[meta.tone]}>
      <Icon aria-hidden="true" />
      {meta.label}
    </Badge>
  );
}

/** "9 of 12 sources read": how much of what the search found was read in full. */
export function sourcesRead(run: Pick<ResearchRun, 'sources'>): string | undefined {
  const sources = run.sources ?? [];
  if (sources.length === 0) return undefined;
  const read = sources.filter((source) => source.fetched).length;
  return `${String(read)} of ${String(sources.length)} sources read`;
}

function RunDetails({ run }: { run: ResearchRun }) {
  const questions = run.subQuestions ?? [];
  const queries = run.queries ?? [];
  if (questions.length === 0 && queries.length === 0 && (run.ownerUrls ?? []).length === 0) {
    return <p className="text-sm text-ink-muted">Nothing recorded yet.</p>;
  }
  return (
    <div className="grid gap-4 text-sm">
      {questions.length > 0 ? (
        <div>
          <p className="font-semibold text-ink">Questions it asked</p>
          <ul className="mt-1 list-disc space-y-1 pl-5 text-ink-muted">
            {questions.map((question) => (
              <li key={question}>{question}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {queries.length > 0 ? (
        <div>
          <p className="font-semibold text-ink">Searches</p>
          <ul className="mt-1 space-y-1 text-ink-muted">
            {queries.map((query, index) => (
              <li key={`${query.text}-${String(index)}`}>
                {query.text}
                <span className="text-ink-subtle">{query.phase === 'allowlist' ? ' (trusted sites)' : ' (open web)'}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {(run.ownerUrls ?? []).length > 0 ? (
        <div>
          <p className="font-semibold text-ink">Your sources</p>
          <ul className="mt-1 space-y-1 break-all text-ink-muted">
            {(run.ownerUrls ?? []).map((url) => (
              <li key={url}>{url}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

function RunItem({
  run,
  shown,
  linked,
  onShow,
}: {
  run: ResearchRun;
  shown: boolean;
  linked: boolean;
  onShow: (briefId: string) => void;
}) {
  const titleId = useId();
  const when = formatDateTime(run.createdAt);
  const read = sourcesRead(run);
  const cost = formatCost(run.costUsd);
  const briefId = run.status === 'succeeded' ? (run.briefId ?? undefined) : undefined;
  return (
    <li className={cn('py-4', shown && 'bg-wash -mx-3 rounded-md px-3')} data-shown={shown || undefined}>
      <Collapsible>
        <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
          <div className="min-w-0 flex-1 basis-64">
            <p id={titleId} className="font-medium text-ink">
              {run.topic}
            </p>
            <p className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-ink-muted">
              <RunStatusBadge run={run} />
              {linked ? <Badge variant="outline">Used for drafting</Badge> : null}
              {when ? <time dateTime={run.createdAt}>{when}</time> : null}
              {read ? <span>{read}</span> : null}
              {cost ? <span>{cost}</span> : null}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {briefId && !shown ? (
              <Button
                size="sm"
                variant="secondary"
                aria-describedby={titleId}
                onClick={() => {
                  onShow(briefId);
                }}
              >
                Show this brief
              </Button>
            ) : null}
            {shown ? <span className="text-sm font-medium text-ink">Shown above</span> : null}
            <CollapsibleTrigger asChild>
              <Button size="sm" variant="ghost" aria-describedby={titleId} className="group">
                How it searched
                <ChevronDown aria-hidden="true" className="transition-transform group-data-[state=open]:rotate-180" />
              </Button>
            </CollapsibleTrigger>
          </div>
        </div>
        <CollapsibleContent>
          <div className="pt-3">
            <RunDetails run={run} />
          </div>
        </CollapsibleContent>
      </Collapsible>
    </li>
  );
}

export interface RunHistoryProps {
  runs: readonly ResearchRun[];
  /** The brief on screen. */
  shownBriefId: string | undefined;
  /** The brief the draft uses (`article.briefId`). */
  linkedBriefId: string | null | undefined;
  onShowBrief: (briefId: string) => void;
  hasMore: boolean;
  loadingMore: boolean;
  onLoadMore: () => void;
}

/** Every research run of the piece, newest first: status, when, sources read, cost, and its brief. */
export function RunHistory({
  runs,
  shownBriefId,
  linkedBriefId,
  onShowBrief,
  hasMore,
  loadingMore,
  onLoadMore,
}: RunHistoryProps) {
  const headingId = useId();
  if (runs.length === 0) return null;
  return (
    <section aria-labelledby={headingId}>
      <h2 id={headingId} className="font-serif text-2xl font-medium text-ink">
        Research runs
      </h2>
      <ul className="mt-3 divide-y divide-line border-y border-line">
        {runs.map((run) => (
          <RunItem
            key={run.id}
            run={run}
            shown={run.briefId !== undefined && run.briefId !== null && run.briefId === shownBriefId}
            linked={run.briefId !== undefined && run.briefId !== null && run.briefId === linkedBriefId}
            onShow={onShowBrief}
          />
        ))}
      </ul>
      {hasMore ? (
        <Button variant="ghost" className="mt-3" disabled={loadingMore} onClick={onLoadMore}>
          {loadingMore ? 'Loading more…' : 'Show older runs'}
        </Button>
      ) : null}
    </section>
  );
}
