import { Check, Inbox, Pencil, X } from 'lucide-react';
import { useId, useRef, useState, type ReactNode } from 'react';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { isEtagMismatch } from '@/lib/api/envelope';
import { toastSuccess } from '@/lib/api/toast';
import { useApiInfiniteQuery, useApiMutation } from '@/lib/query/api-query';
import { queryKeys } from '@/lib/query/keys';
import { PillarBadge } from '@/components/ui/pillar-badge';

import { LoadError } from '../components/section-intro';
import { useMachines } from '../hooks/use-machines';
import { usePillarDirectory } from '../hooks/use-pillars';
import { POV_ORIGIN_LABEL, seedOriginLabel, STORY_ORIGIN_LABEL } from '../lib/labels';
import { reviewDecisions } from '../lib/machines';
import type { PovEntry, ReviewDecision, StoryEntry } from '../lib/types';
import type { ComposerMode } from './composer';
import { ClassifierNotes, ConfidentialityBadge, VerdictBadge } from './entry-meta';
import { mergeQueue, OWNER_INPUTS_KEY, type QueueEntry } from './model';

function Part({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <dt className="text-xs font-semibold text-ink-subtle">{label}</dt>
      <dd className="mt-0.5 whitespace-pre-wrap break-words text-ink">{children}</dd>
    </div>
  );
}

function StoryBody({ story }: { story: StoryEntry }) {
  const metrics = story.metrics ?? [];
  return (
    <dl className="space-y-3">
      {story.problem ? <Part label="Problem">{story.problem}</Part> : null}
      {story.actions ? <Part label="What you did">{story.actions}</Part> : null}
      {story.decisions ? <Part label="Decisions and trade-offs">{story.decisions}</Part> : null}
      {metrics.length > 0 ? (
        <Part label="Metrics">
          <ul className="list-disc space-y-0.5 pl-5">
            {metrics.map((metric, index) => (
              <li key={index}>{`${metric.label}: ${metric.value}${metric.bucketed ? '' : ' (public)'}`}</li>
            ))}
          </ul>
        </Part>
      ) : null}
      {story.lessons ? <Part label="What you learned">{story.lessons}</Part> : null}
    </dl>
  );
}

function originOf(next: QueueEntry): string {
  if (next.kind === 'story') return seedOriginLabel(next.entry.item.seedSource) ?? STORY_ORIGIN_LABEL[next.entry.item.origin];
  return POV_ORIGIN_LABEL[next.entry.item.origin];
}

/**
 * Proposed stories and positions (seeded from the resume or portfolio, or taken from an
 * interview), one at a time, built for one hand on a phone: the entry, then Approve, Reject or
 * Edit at the bottom of the screen. Nothing is used in a draft until it is approved here and the
 * classifier passed it.
 */
export function ReviewQueue({ onEdit }: { onEdit: (mode: ComposerMode) => void }) {
  const headingId = useId();
  const headingRef = useRef<HTMLHeadingElement>(null);
  const machines = useMachines();
  const { nameOf } = usePillarDirectory();
  const [changed, setChanged] = useState<string | undefined>();
  const [deciding, setDeciding] = useState<ReviewDecision | undefined>();
  const stories = useApiInfiniteQuery({
    queryKey: queryKeys.stories.list({ status: 'proposed' }),
    path: '/api/stories',
    init: { params: { query: { status: 'proposed' } } },
    pageSize: 50,
  });
  const povs = useApiInfiniteQuery({
    queryKey: queryKeys.povs.list({ status: 'proposed' }),
    path: '/api/povs',
    init: { params: { query: { status: 'proposed' } } },
    pageSize: 50,
  });
  const invalidates = [queryKeys.stories.lists(), queryKeys.povs.lists(), OWNER_INPUTS_KEY];
  const reviewStory = useApiMutation('post', '/api/stories/{story_id}/review', {
    invalidates,
    errorToast: 'Couldn’t record your answer.',
    handles: ['etag_mismatch'],
  });
  const reviewPov = useApiMutation('post', '/api/povs/{pov_id}/review', {
    invalidates,
    errorToast: 'Couldn’t record your answer.',
    handles: ['etag_mismatch'],
  });

  if (stories.isPending || povs.isPending) {
    return (
      <p role="status" className="text-sm text-ink-muted">
        Looking for entries to review…
      </p>
    );
  }
  if (stories.isError || povs.isError) {
    return (
      <LoadError
        error={stories.error ?? povs.error}
        title="Couldn’t load the entries to review."
        retryLabel="Try loading the entries to review again"
        onRetry={() => {
          void stories.refetch();
          void povs.refetch();
        }}
      />
    );
  }

  const queue = mergeQueue(stories.data, povs.data);
  const more = stories.hasNextPage || povs.hasNextPage;
  const current = queue[0];
  const busy = reviewStory.isPending || reviewPov.isPending;

  const afterDecision = () => {
    requestAnimationFrame(() => {
      headingRef.current?.focus();
    });
  };

  const decide = (next: QueueEntry, decision: ReviewDecision) => {
    const name = next.kind === 'story' ? `“${next.entry.item.title}”` : 'the position';
    const done = decision === 'approve' ? `Approved ${name}.` : `Rejected ${name}.`;
    setDeciding(decision);
    const callbacks = {
      onSuccess: () => {
        setChanged(undefined);
        toastSuccess(done);
        afterDecision();
      },
      onError: (error: unknown) => {
        if (!isEtagMismatch(error)) return;
        // Someone (the classifier, another tab) changed it: show the latest version first.
        setChanged(next.entry.item.id);
        void stories.refetch();
        void povs.refetch();
      },
      onSettled: () => {
        setDeciding(undefined);
      },
    };
    if (next.kind === 'story') {
      reviewStory.mutate({ params: { path: { story_id: next.entry.item.id } }, body: { decision }, ifMatch: next.entry.etag }, callbacks);
    } else {
      reviewPov.mutate({ params: { path: { pov_id: next.entry.item.id } }, body: { decision }, ifMatch: next.entry.etag }, callbacks);
    }
  };

  if (!current) {
    return (
      <section aria-labelledby={headingId} className="flex items-start gap-3 rounded-lg border border-line px-4 py-4">
        <Inbox aria-hidden="true" className="mt-0.5 size-5 shrink-0 text-ink-subtle" />
        <div>
          <h3 id={headingId} ref={headingRef} tabIndex={-1} className="font-semibold text-ink outline-none">
            Nothing to review
          </h3>
          <p className="mt-1 text-sm text-ink-muted">
            Entries proposed from your resume, your portfolio or an interview wait here for your approval.
          </p>
        </div>
      </section>
    );
  }

  const decisions = reviewDecisions(machines, current.entry.item.status);
  const item: StoryEntry | PovEntry = current.entry.item;
  const pillarIds = current.kind === 'story' ? (current.entry.item.pillars ?? []) : [current.entry.item.pillar];
  const count = `${String(queue.length)}${more ? '+' : ''}`;

  return (
    <section aria-labelledby={headingId} className="overflow-clip rounded-xl border border-line-strong bg-sheet">
      <div className="flex flex-wrap items-baseline justify-between gap-2 border-b border-line px-4 py-3">
        <h3 id={headingId} ref={headingRef} tabIndex={-1} className="font-semibold text-ink outline-none">
          {`To review: ${count}`}
        </h3>
        <p className="text-sm text-ink-muted">{current.kind === 'story' ? 'Story' : 'Position'}</p>
      </div>

      <article aria-label={current.kind === 'story' ? current.entry.item.title : 'Proposed position'} className="space-y-4 px-4 py-5">
        <p className="text-sm text-ink-muted">{originOf(current)}</p>
        {current.kind === 'story' ? (
          <>
            <h4 className="font-serif text-xl font-medium leading-snug text-ink">{current.entry.item.title}</h4>
            <StoryBody story={current.entry.item} />
          </>
        ) : (
          <>
            <blockquote className="border-l-2 border-ink pl-4 font-serif text-xl leading-snug text-ink">
              {current.entry.item.statement}
            </blockquote>
            {current.entry.item.context ? <p className="text-ink-muted">{current.entry.item.context}</p> : null}
          </>
        )}
        <div className="flex flex-wrap gap-1.5">
          {pillarIds.map((id) => (
            <PillarBadge key={id} id={id} name={nameOf(id)} />
          ))}
          <ConfidentialityBadge value={item.confidentiality} />
          <VerdictBadge check={item.classifier} />
        </div>
        <ClassifierNotes check={item.classifier} />
        {changed === item.id ? (
          <Alert variant="info">
            <AlertTitle>This entry changed while you were reading it.</AlertTitle>
            <AlertDescription>
              <p>It now shows the latest version. Read it again, then decide.</p>
            </AlertDescription>
          </Alert>
        ) : null}
      </article>

      <div className="sticky bottom-[calc(4rem+env(safe-area-inset-bottom))] grid grid-cols-[1fr_auto_1fr] gap-2 border-t border-line bg-sheet p-3 md:static md:flex md:justify-end">
        {decisions.includes('reject') ? (
          <Button
            variant="secondary"
            disabled={busy}
            className="pointer-coarse:min-h-11"
            onClick={() => {
              decide(current, 'reject');
            }}
          >
            <X aria-hidden="true" />
            {deciding === 'reject' ? 'Rejecting…' : 'Reject'}
          </Button>
        ) : (
          <span />
        )}
        <Button
          variant="ghost"
          disabled={busy}
          aria-label="Edit before deciding"
          className="pointer-coarse:min-h-11"
          onClick={() => {
            onEdit(current.kind === 'story' ? { kind: 'edit-story', entry: current.entry } : { kind: 'edit-pov', entry: current.entry });
          }}
        >
          <Pencil aria-hidden="true" />
          <span className="max-md:sr-only">Edit</span>
        </Button>
        {decisions.includes('approve') ? (
          <Button
            disabled={busy}
            className="pointer-coarse:min-h-11"
            onClick={() => {
              decide(current, 'approve');
            }}
          >
            <Check aria-hidden="true" />
            {deciding === 'approve' ? 'Approving…' : 'Approve'}
          </Button>
        ) : (
          <span />
        )}
      </div>
    </section>
  );
}
