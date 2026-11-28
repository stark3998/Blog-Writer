import { Archive, ArchiveRestore, Check, ChevronDown, Pencil, Trash2, Undo2, X } from 'lucide-react';
import { useState, type ReactNode } from 'react';

import { Button } from '@/components/ui/button';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { Field } from '@/components/ui/field';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Switch } from '@/components/ui/switch';
import { useApiInfiniteQuery, useApiMutation } from '@/lib/query/api-query';
import { queryKeys } from '@/lib/query/keys';
import { PillarBadge } from '@/components/ui/pillar-badge';

import { ConfirmAction } from '../components/confirm-action';
import { LoadError, LoadingBlock } from '../components/section-intro';
import { ShowMore } from '../components/show-more';
import { useMachines } from '../hooks/use-machines';
import { usePillarDirectory } from '../hooks/use-pillars';
import { formatDate } from '../lib/format';
import { POV_ORIGIN_LABEL, REVIEW_STATUS_LABEL, seedOriginLabel, STORY_ORIGIN_LABEL } from '../lib/labels';
import { reviewDecisions } from '../lib/machines';
import { refreshOnConflict } from '../lib/stale';
import type { Entry, PovEntry, ReviewDecision, ReviewStatus, StoryEntry } from '../lib/types';
import type { ComposerMode } from './composer';
import { ClassifierNotes, ConfidentialityBadge, ReviewStatusBadge, VerdictBadge } from './entry-meta';
import { OWNER_INPUTS_KEY } from './model';

const ALL = 'all';
const STATUSES: readonly ReviewStatus[] = ['approved', 'proposed', 'rejected', 'archived'];

const DECISION: Readonly<Record<ReviewDecision, { label: string; icon: typeof Check }>> = {
  approve: { label: 'Approve', icon: Check },
  reject: { label: 'Reject', icon: X },
  archive: { label: 'Archive', icon: Archive },
  restore: { label: 'Restore', icon: ArchiveRestore },
};

function decisionLabel(decision: ReviewDecision, status: ReviewStatus): string {
  // A rejected entry comes back as proposed: it is reviewed again, not restored to use.
  if (decision === 'restore' && status === 'rejected') return 'Review again';
  return DECISION[decision].label;
}

const invalidates = [queryKeys.stories.lists(), queryKeys.povs.lists(), OWNER_INPUTS_KEY];

interface EntryActionsProps {
  status: ReviewStatus;
  busy: boolean;
  onDecide: (decision: ReviewDecision) => void;
  onEdit: () => void;
  onDelete: () => void;
  /** "story" or "position", for the delete question. */
  noun: string;
}

function EntryActions({ status, busy, onDecide, onEdit, onDelete, noun }: EntryActionsProps) {
  const machines = useMachines();
  return (
    <div className="flex flex-wrap gap-2">
      <Button variant="secondary" size="sm" disabled={busy} onClick={onEdit}>
        <Pencil aria-hidden="true" />
        Edit
      </Button>
      {reviewDecisions(machines, status).map((decision) => {
        const Icon = decision === 'restore' && status === 'rejected' ? Undo2 : DECISION[decision].icon;
        return (
          <Button
            key={decision}
            variant={decision === 'approve' ? 'primary' : 'secondary'}
            size="sm"
            disabled={busy}
            onClick={() => {
              onDecide(decision);
            }}
          >
            <Icon aria-hidden="true" />
            {decisionLabel(decision, status)}
          </Button>
        );
      })}
      <ConfirmAction
        title={`Delete this ${noun}?`}
        description={`It is gone for good, and drafts can no longer draw on it. To keep it out of drafts but on record, archive it instead.`}
        confirmLabel={`Delete ${noun}`}
        cancelLabel="Keep it"
        onConfirm={onDelete}
      >
        <Button variant="ghost" size="sm" className="text-danger hover:text-danger" disabled={busy}>
          <Trash2 aria-hidden="true" />
          Delete
        </Button>
      </ConfirmAction>
    </div>
  );
}

function Row({ summary, children }: { summary: ReactNode; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <li className="border-b border-line">
      <Collapsible open={open} onOpenChange={setOpen}>
        <CollapsibleTrigger className="group flex w-full items-start gap-3 py-4 text-left transition-colors duration-150 hover:bg-wash pointer-coarse:min-h-11">
          <span className="min-w-0 flex-1">{summary}</span>
          <ChevronDown aria-hidden="true" className="mt-1 size-4 shrink-0 text-ink-subtle transition-transform duration-200 group-data-[state=open]:rotate-180" />
        </CollapsibleTrigger>
        <CollapsibleContent>
          <div className="space-y-4 pb-5">{children}</div>
        </CollapsibleContent>
      </Collapsible>
    </li>
  );
}

function StoryRow({ entry, onEdit }: { entry: Entry<StoryEntry>; onEdit: (mode: ComposerMode) => void }) {
  const story = entry.item;
  const { nameOf } = usePillarDirectory();
  const review = useApiMutation('post', '/api/stories/{story_id}/review', {
    invalidates,
    successToast: (data) => `${REVIEW_STATUS_LABEL[data.item.status]}: “${data.item.title}”.`,
    errorToast: 'Couldn’t change the story.',
    onError: refreshOnConflict([queryKeys.stories.lists()]),
  });
  const remove = useApiMutation('delete', '/api/stories/{story_id}', {
    invalidates,
    successToast: 'Story deleted.',
    errorToast: 'Couldn’t delete the story.',
    onError: refreshOnConflict([queryKeys.stories.lists()]),
  });
  const metrics = story.metrics ?? [];
  const origin = seedOriginLabel(story.seedSource) ?? STORY_ORIGIN_LABEL[story.origin];
  return (
    <Row
      summary={
        <>
          <span className="block font-serif text-lg leading-snug text-ink">{story.title}</span>
          <span className="mt-2 flex flex-wrap gap-1.5">
            {(story.pillars ?? []).map((id) => (
              <PillarBadge key={id} id={id} name={nameOf(id)} />
            ))}
            <ReviewStatusBadge status={story.status} />
            <VerdictBadge check={story.classifier} />
          </span>
        </>
      }
    >
      <dl className="grid max-w-measure gap-3 text-ink">
        {story.problem ? (
          <div>
            <dt className="text-xs font-semibold text-ink-subtle">Problem</dt>
            <dd className="mt-0.5 whitespace-pre-wrap">{story.problem}</dd>
          </div>
        ) : null}
        {story.actions ? (
          <div>
            <dt className="text-xs font-semibold text-ink-subtle">What you did</dt>
            <dd className="mt-0.5 whitespace-pre-wrap">{story.actions}</dd>
          </div>
        ) : null}
        {story.decisions ? (
          <div>
            <dt className="text-xs font-semibold text-ink-subtle">Decisions and trade-offs</dt>
            <dd className="mt-0.5 whitespace-pre-wrap">{story.decisions}</dd>
          </div>
        ) : null}
        {metrics.length > 0 ? (
          <div>
            <dt className="text-xs font-semibold text-ink-subtle">Metrics</dt>
            <dd className="mt-0.5">
              <ul className="list-disc space-y-0.5 pl-5">
                {metrics.map((metric, index) => (
                  <li key={index}>{`${metric.label}: ${metric.value}${metric.bucketed ? '' : ' (public)'}`}</li>
                ))}
              </ul>
            </dd>
          </div>
        ) : null}
        {story.lessons ? (
          <div>
            <dt className="text-xs font-semibold text-ink-subtle">What you learned</dt>
            <dd className="mt-0.5 whitespace-pre-wrap">{story.lessons}</dd>
          </div>
        ) : null}
      </dl>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 text-sm text-ink-muted">
        <ConfidentialityBadge value={story.confidentiality} />
        <span>{[origin, story.approvedAt ? `approved ${formatDate(story.approvedAt) ?? ''}` : undefined].filter(Boolean).join(', ')}</span>
        {(story.tags ?? []).length > 0 ? <span>{(story.tags ?? []).map((tag) => `#${tag}`).join(' ')}</span> : null}
      </div>
      <ClassifierNotes check={story.classifier} />
      <EntryActions
        status={story.status}
        busy={review.isPending || remove.isPending}
        noun="story"
        onEdit={() => {
          onEdit({ kind: 'edit-story', entry });
        }}
        onDecide={(decision) => {
          review.mutate({ params: { path: { story_id: story.id } }, body: { decision }, ifMatch: entry.etag });
        }}
        onDelete={() => {
          remove.mutate({ params: { path: { story_id: story.id } }, ifMatch: entry.etag });
        }}
      />
    </Row>
  );
}

function PovRow({ entry, onEdit }: { entry: Entry<PovEntry>; onEdit: (mode: ComposerMode) => void }) {
  const pov = entry.item;
  const { nameOf } = usePillarDirectory();
  const review = useApiMutation('post', '/api/povs/{pov_id}/review', {
    invalidates,
    successToast: (data) => `${REVIEW_STATUS_LABEL[data.item.status]}: the position.`,
    errorToast: 'Couldn’t change the position.',
    onError: refreshOnConflict([queryKeys.povs.lists()]),
  });
  const remove = useApiMutation('delete', '/api/povs/{pov_id}', {
    invalidates,
    successToast: 'Position deleted.',
    errorToast: 'Couldn’t delete the position.',
    onError: refreshOnConflict([queryKeys.povs.lists()]),
  });
  return (
    <Row
      summary={
        <>
          <span className="block border-l-2 border-line-strong pl-3 font-serif text-lg leading-snug text-ink">{pov.statement}</span>
          <span className="mt-2 flex flex-wrap gap-1.5">
            <PillarBadge id={pov.pillar} name={nameOf(pov.pillar)} />
            <ReviewStatusBadge status={pov.status} />
            <VerdictBadge check={pov.classifier} />
          </span>
        </>
      }
    >
      {pov.context ? <p className="max-w-measure text-ink-muted">{pov.context}</p> : null}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 text-sm text-ink-muted">
        <ConfidentialityBadge value={pov.confidentiality} />
        <span>{[POV_ORIGIN_LABEL[pov.origin], pov.approvedAt ? `approved ${formatDate(pov.approvedAt) ?? ''}` : undefined].filter(Boolean).join(', ')}</span>
      </div>
      <ClassifierNotes check={pov.classifier} />
      <EntryActions
        status={pov.status}
        busy={review.isPending || remove.isPending}
        noun="position"
        onEdit={() => {
          onEdit({ kind: 'edit-pov', entry });
        }}
        onDecide={(decision) => {
          review.mutate({ params: { path: { pov_id: pov.id } }, body: { decision }, ifMatch: entry.etag });
        }}
        onDelete={() => {
          remove.mutate({ params: { path: { pov_id: pov.id } }, ifMatch: entry.etag });
        }}
      />
    </Row>
  );
}

function Filters({
  status,
  onStatus,
  pillar,
  onPillar,
  children,
}: {
  status: string;
  onStatus: (value: string) => void;
  pillar: string;
  onPillar: (value: string) => void;
  children?: ReactNode;
}) {
  const { pillars } = usePillarDirectory();
  return (
    <div className="flex flex-wrap items-end gap-3">
      <Field label="Status" className="w-full sm:w-48">
        {(control) => (
          <Select value={status} onValueChange={onStatus}>
            <SelectTrigger {...control}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>Every status</SelectItem>
              {STATUSES.map((value) => (
                <SelectItem key={value} value={value}>
                  {REVIEW_STATUS_LABEL[value]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
      </Field>
      <Field label="Pillar" className="w-full sm:w-56">
        {(control) => (
          <Select value={pillar} onValueChange={onPillar}>
            <SelectTrigger {...control}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>Every pillar</SelectItem>
              {pillars.map((item) => (
                <SelectItem key={item.id} value={item.id}>
                  {item.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
      </Field>
      {children}
    </div>
  );
}

/** The story bank with its filters. */
export function StoriesList({ onEdit }: { onEdit: (mode: ComposerMode) => void }) {
  const [status, setStatus] = useState<string>(ALL);
  const [pillar, setPillar] = useState<string>(ALL);
  const [usableOnly, setUsableOnly] = useState(false);
  const filters = {
    status: status === ALL ? null : (status as ReviewStatus),
    pillar: pillar === ALL ? null : pillar,
    usable: usableOnly ? true : null,
  };
  const stories = useApiInfiniteQuery({
    queryKey: queryKeys.stories.list(filters),
    path: '/api/stories',
    init: { params: { query: filters } },
    pageSize: 20,
  });
  const filtered = status !== ALL || pillar !== ALL || usableOnly;
  return (
    <div className="space-y-5">
      <Filters status={status} onStatus={setStatus} pillar={pillar} onPillar={setPillar}>
        <Field label="Ready for drafts only" orientation="horizontal" className="sm:pb-2">
          <Switch checked={usableOnly} onCheckedChange={setUsableOnly} />
        </Field>
      </Filters>
      {stories.isPending ? (
        <LoadingBlock label="Loading the stories" />
      ) : stories.isError ? (
        <LoadError error={stories.error} title="Couldn’t load the stories." retryLabel="Try loading the stories again" onRetry={() => void stories.refetch()} />
      ) : stories.data.length === 0 ? (
        <p className="text-ink-muted">{filtered ? 'No stories match these filters.' : 'No stories yet.'}</p>
      ) : (
        <div>
          <ul className="border-t border-line">
            {stories.data.map((entry) => (
              <StoryRow key={entry.item.id} entry={entry} onEdit={onEdit} />
            ))}
          </ul>
          <ShowMore
            hasNextPage={stories.hasNextPage}
            isFetchingNextPage={stories.isFetchingNextPage}
            fetchNextPage={stories.fetchNextPage}
            label="Show more stories"
          />
        </div>
      )}
    </div>
  );
}

/** The position bank with its filters. */
export function PovsList({ onEdit }: { onEdit: (mode: ComposerMode) => void }) {
  const [status, setStatus] = useState<string>(ALL);
  const [pillar, setPillar] = useState<string>(ALL);
  const filters = { status: status === ALL ? null : (status as ReviewStatus), pillar: pillar === ALL ? null : pillar };
  const povs = useApiInfiniteQuery({
    queryKey: queryKeys.povs.list(filters),
    path: '/api/povs',
    init: { params: { query: filters } },
    pageSize: 20,
  });
  const filtered = status !== ALL || pillar !== ALL;
  return (
    <div className="space-y-5">
      <Filters status={status} onStatus={setStatus} pillar={pillar} onPillar={setPillar} />
      {povs.isPending ? (
        <LoadingBlock label="Loading the positions" />
      ) : povs.isError ? (
        <LoadError error={povs.error} title="Couldn’t load the positions." retryLabel="Try loading the positions again" onRetry={() => void povs.refetch()} />
      ) : povs.data.length === 0 ? (
        <p className="text-ink-muted">{filtered ? 'No positions match these filters.' : 'No positions yet.'}</p>
      ) : (
        <div>
          <ul className="border-t border-line">
            {povs.data.map((entry) => (
              <PovRow key={entry.item.id} entry={entry} onEdit={onEdit} />
            ))}
          </ul>
          <ShowMore
            hasNextPage={povs.hasNextPage}
            isFetchingNextPage={povs.isFetchingNextPage}
            fetchNextPage={povs.fetchNextPage}
            label="Show more positions"
          />
        </div>
      )}
    </div>
  );
}
