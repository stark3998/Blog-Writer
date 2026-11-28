import { ChevronDown, CircleCheck, Highlighter, Pencil, Plus, Trash2 } from 'lucide-react';
import { useState } from 'react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { Field } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { ConflictDialog, useConflict } from '@/features/data/conflict-dialog';
import { useApiInfiniteQuery, useApiMutation } from '@/lib/query/api-query';
import { queryKeys } from '@/lib/query/keys';
import { PillarBadge } from '@/components/ui/pillar-badge';

import { ConfirmAction } from '../components/confirm-action';
import { HighlightedText } from '../components/highlighted-text';
import { LoadError, LoadingBlock } from '../components/section-intro';
import { ShowMore } from '../components/show-more';
import { useMachines } from '../hooks/use-machines';
import { usePillarDirectory } from '../hooks/use-pillars';
import { serverFieldErrors } from '../lib/errors';
import { formatDate, formatDecimal, plural } from '../lib/format';
import { SOURCE_TYPE_LABEL } from '../lib/labels';
import { canMove } from '../lib/machines';
import { refreshOnConflict } from '../lib/stale';
import type { Entry, SampleStatus, VoiceSample, VoiceSourceType } from '../lib/types';
import { GUIDE_VIEW_KEY } from './model';
import { MAX_SAMPLE_CHARS, SampleForm } from './sample-form';

const ALL = 'all';
const SOURCE_FILTERS: readonly VoiceSourceType[] = [
  'linkedin_post',
  'linkedin_comment',
  'portfolio_post',
  'portfolio_about',
  'interview_answer',
  'final_post',
];

function sampleHeading(sample: VoiceSample): string {
  if (sample.title?.trim()) return sample.title.trim();
  const firstLine = sample.text.trim().split('\n', 1)[0] ?? '';
  return firstLine.length > 90 ? `${firstLine.slice(0, 89).trimEnd()}…` : firstLine || 'Untitled sample';
}

function flagSummary(sample: VoiceSample): string {
  const flags = sample.flags?.length ?? 0;
  if (flags === 0) return 'No AI tells';
  return `${plural(flags, 'flag', 'flags')}, ${formatDecimal(sample.aiTellScore)} per 100 words`;
}

const invalidations = [queryKeys.voiceSamples.lists(), GUIDE_VIEW_KEY, queryKeys.today];

/** The editable parts of a sample (`PUT /api/voice/samples/{id}`), in place. */
function SampleEdit({ entry, onDone }: { entry: Entry<VoiceSample>; onDone: () => void }) {
  const { item } = entry;
  const { pillars } = usePillarDirectory();
  const conflict = useConflict();
  const [text, setText] = useState(item.text);
  const [title, setTitle] = useState(item.title ?? '');
  const [pillar, setPillar] = useState(item.pillar ?? 'none');
  const [unassisted, setUnassisted] = useState(item.ownerUnassisted);
  const save = useApiMutation('put', '/api/voice/samples/{sample_id}', {
    invalidates: invalidations,
    successToast: 'Sample saved.',
    errorToast: 'Couldn’t save the sample.',
    handles: ['etag_mismatch', 'validation_failed', 'confidential_text', 'text_required'],
    // The latest text becomes "theirs" in the conflict dialog.
    onError: refreshOnConflict([queryKeys.voiceSamples.lists()]),
  });
  const errors = serverFieldErrors(save.error);

  const send = (etag: string | null) => {
    save.mutate(
      {
        params: { path: { sample_id: item.id } },
        body: { text, title: title.trim() === '' ? null : title.trim(), pillar: pillar === 'none' ? null : pillar, ownerUnassisted: unassisted },
        ifMatch: etag,
      },
      {
        onSuccess: () => {
          conflict.close();
          onDone();
        },
        onError: (error) => {
          conflict.show(error);
        },
      },
    );
  };

  return (
    <div className="space-y-4">
      <Field label="Text" error={errors.text} description={`At most ${String(MAX_SAMPLE_CHARS / 1000)},000 characters. Saving scores it again.`}>
        <Textarea
          rows={8}
          value={text}
          maxLength={MAX_SAMPLE_CHARS}
          onChange={(event) => {
            setText(event.target.value);
          }}
        />
      </Field>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Title" error={errors.title}>
          <Input
            value={title}
            maxLength={200}
            onChange={(event) => {
              setTitle(event.target.value);
            }}
          />
        </Field>
        <Field label="Pillar">
          {(control) => (
            <Select value={pillar} onValueChange={setPillar}>
              <SelectTrigger {...control}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="none">No pillar</SelectItem>
                {pillars.map((option) => (
                  <SelectItem key={option.id} value={option.id}>
                    {option.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
        </Field>
      </div>
      <Field label="I wrote this without AI help" orientation="horizontal">
        <Checkbox
          checked={unassisted}
          onCheckedChange={(checked) => {
            setUnassisted(checked === true);
          }}
        />
      </Field>
      <div className="flex flex-wrap gap-2">
        <Button
          disabled={save.isPending || text.trim() === ''}
          onClick={() => {
            send(entry.etag);
          }}
        >
          {save.isPending ? 'Saving…' : 'Save sample'}
        </Button>
        <Button variant="ghost" onClick={onDone}>
          Cancel
        </Button>
      </div>
      <ConflictDialog
        {...conflict}
        subject="This sample"
        mine={text}
        theirs={item.text !== text ? item.text : undefined}
        saving={save.isPending}
        onKeepMine={() => {
          send(conflict.conflict?.etag ?? entry.etag);
        }}
        onTakeTheirs={() => {
          setText(item.text);
          setTitle(item.title ?? '');
          setPillar(item.pillar ?? 'none');
          setUnassisted(item.ownerUnassisted);
          conflict.close();
        }}
      />
    </div>
  );
}

function SampleRow({ entry }: { entry: Entry<VoiceSample> }) {
  const { item } = entry;
  const machines = useMachines();
  const { nameOf } = usePillarDirectory();
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const status = useApiMutation('post', '/api/voice/samples/{sample_id}/status', {
    invalidates: invalidations,
    successToast: (data) => (data.item.status === 'excluded' ? 'Sample excluded from the guide.' : 'Sample included in the guide again.'),
    errorToast: 'Couldn’t change the sample.',
    onError: refreshOnConflict([queryKeys.voiceSamples.lists()]),
  });
  const remove = useApiMutation('delete', '/api/voice/samples/{sample_id}', {
    invalidates: invalidations,
    successToast: 'Sample deleted.',
    errorToast: 'Couldn’t delete the sample.',
    onError: refreshOnConflict([queryKeys.voiceSamples.lists()]),
  });
  const excluded = item.status === 'excluded';
  const target: SampleStatus = excluded ? 'active' : 'excluded';
  const flags = item.flags ?? [];
  const posted = formatDate(item.postedAt);

  return (
    <li className="border-b border-line">
      <Collapsible open={open} onOpenChange={setOpen}>
        <CollapsibleTrigger className="group flex w-full items-start gap-3 py-4 text-left transition-colors duration-150 hover:bg-wash pointer-coarse:min-h-11">
          <span className="min-w-0 flex-1">
            <span className={excluded ? 'block font-medium text-ink-muted' : 'block font-medium text-ink'}>{sampleHeading(item)}</span>
            <span className="mt-1 block text-sm text-ink-muted">
              {[SOURCE_TYPE_LABEL[item.sourceType], plural(item.wordCount, 'word', 'words'), flagSummary(item), posted ? `posted ${posted}` : undefined]
                .filter(Boolean)
                .join(', ')}
            </span>
          </span>
          <span className="flex shrink-0 flex-wrap justify-end gap-1.5">
            {item.ownerUnassisted ? <Badge variant="outline">Unassisted</Badge> : null}
            {item.exemplarEligible ? (
              <Badge variant="success">
                <CircleCheck aria-hidden="true" />
                Can be quoted
              </Badge>
            ) : null}
            {excluded ? <Badge>Excluded</Badge> : null}
          </span>
          <ChevronDown aria-hidden="true" className="mt-1 size-4 shrink-0 text-ink-subtle transition-transform duration-200 group-data-[state=open]:rotate-180" />
        </CollapsibleTrigger>
        <CollapsibleContent>
          <div className="space-y-5 pb-6">
            {editing ? (
              <SampleEdit
                entry={entry}
                onDone={() => {
                  setEditing(false);
                }}
              />
            ) : (
              <>
                <div className="grid gap-6 lg:grid-cols-[minmax(0,var(--container-measure))_minmax(0,16rem)]">
                  <HighlightedText text={item.text} spans={flags} className="font-serif text-base leading-prose text-ink" />
                  <aside aria-label="AI tells in this sample" className="self-start border-t-2 border-ink pt-3 text-sm">
                    <p className="flex items-center gap-2 font-semibold text-ink">
                      <Highlighter aria-hidden="true" className="size-4" />
                      {flags.length === 0 ? 'Nothing flagged' : plural(flags.length, 'flag', 'flags')}
                    </p>
                    {flags.length === 0 ? (
                      <p className="mt-1 text-ink-muted">
                        {item.exemplarEligible
                          ? 'It reads as yours, so the guide may quote it as an example.'
                          : 'The structural checks did not run, so it is not quoted as an example yet.'}
                      </p>
                    ) : (
                      <>
                        <p className="mt-1 text-ink-muted">Flagged passages inform topics and terms only. They are never quoted as examples.</p>
                        <ol className="mt-2 list-decimal space-y-1 pl-5 text-ink-muted">
                          {flags.slice(0, 12).map((flag, index) => (
                            <li key={index}>{flag.message}</li>
                          ))}
                        </ol>
                        {flags.length > 12 ? <p className="mt-1 text-ink-subtle">{`And ${String(flags.length - 12)} more.`}</p> : null}
                      </>
                    )}
                    {item.pillar ? (
                      <p className="mt-3">
                        <PillarBadge id={item.pillar} name={nameOf(item.pillar)} />
                      </p>
                    ) : null}
                  </aside>
                </div>
                <div className="flex flex-wrap gap-2">
                  {item.sourceType !== 'final_post' && item.sourceType !== 'interview_answer' ? (
                    <Button
                      variant="secondary"
                      size="sm"
                      onClick={() => {
                        setEditing(true);
                      }}
                    >
                      <Pencil aria-hidden="true" />
                      Edit
                    </Button>
                  ) : null}
                  <Button
                    variant="secondary"
                    size="sm"
                    disabled={status.isPending || !canMove(machines, 'voice_sample', item.status, target)}
                    onClick={() => {
                      status.mutate({ params: { path: { sample_id: item.id } }, body: { status: target }, ifMatch: entry.etag });
                    }}
                  >
                    {excluded ? 'Include in the guide' : 'Exclude from the guide'}
                  </Button>
                  <ConfirmAction
                    title="Delete this sample?"
                    description="The guide is not changed until it is built again. To keep the sample but leave it out, exclude it instead."
                    confirmLabel="Delete sample"
                    cancelLabel="Keep it"
                    onConfirm={() => {
                      remove.mutate({ params: { path: { sample_id: item.id } }, ifMatch: entry.etag });
                    }}
                  >
                    <Button variant="ghost" size="sm" className="text-danger hover:text-danger" disabled={remove.isPending}>
                      <Trash2 aria-hidden="true" />
                      Delete
                    </Button>
                  </ConfirmAction>
                </div>
              </>
            )}
          </div>
        </CollapsibleContent>
      </Collapsible>
    </li>
  );
}

/** The corpus: filters, "Add a sample", and each sample with its AI-tell flags. */
export function SamplesPanel() {
  const [source, setSource] = useState<string>(ALL);
  const [status, setStatus] = useState<string>(ALL);
  const [adding, setAdding] = useState(false);
  const filters = {
    sourceType: source === ALL ? null : (source as VoiceSourceType),
    status: status === ALL ? null : (status as SampleStatus),
  };
  const samples = useApiInfiniteQuery({
    queryKey: queryKeys.voiceSamples.list(filters),
    path: '/api/voice/samples',
    init: { params: { query: filters } },
    pageSize: 20,
  });
  const filtered = source !== ALL || status !== ALL;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end gap-3">
        <Field label="Source" className="w-full sm:w-56">
          {(control) => (
            <Select value={source} onValueChange={setSource}>
              <SelectTrigger {...control}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ALL}>All sources</SelectItem>
                {SOURCE_FILTERS.map((type) => (
                  <SelectItem key={type} value={type}>
                    {SOURCE_TYPE_LABEL[type]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
        </Field>
        <Field label="Status" className="w-full sm:w-48">
          {(control) => (
            <Select value={status} onValueChange={setStatus}>
              <SelectTrigger {...control}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ALL}>All samples</SelectItem>
                <SelectItem value="active">In the guide</SelectItem>
                <SelectItem value="excluded">Excluded</SelectItem>
              </SelectContent>
            </Select>
          )}
        </Field>
        {adding ? null : (
          <Button
            className="sm:ml-auto"
            onClick={() => {
              setAdding(true);
            }}
          >
            <Plus aria-hidden="true" />
            Add a sample
          </Button>
        )}
      </div>

      {adding ? (
        <SampleForm
          onDone={() => {
            setAdding(false);
          }}
        />
      ) : null}

      {samples.isPending ? (
        <LoadingBlock label="Loading the samples" />
      ) : samples.isError ? (
        <LoadError error={samples.error} title="Couldn’t load the samples." retryLabel="Try loading the samples again" onRetry={() => void samples.refetch()} />
      ) : samples.data.length === 0 ? (
        <p className="text-ink-muted">
          {filtered ? 'No samples match these filters.' : 'No samples yet. Start with the LinkedIn posts you wrote yourself.'}
        </p>
      ) : (
        <div>
          <ul className="border-t border-line">
            {samples.data.map((entry) => (
              <SampleRow key={entry.item.id} entry={entry} />
            ))}
          </ul>
          <ShowMore
            hasNextPage={samples.hasNextPage}
            isFetchingNextPage={samples.isFetchingNextPage}
            fetchNextPage={samples.fetchNextPage}
            label="Show more samples"
          />
        </div>
      )}
    </div>
  );
}
