import { CircleCheck, CirclePause, CircleX, Plus, Rss } from 'lucide-react';
import { useId, useState, type SubmitEvent } from 'react';
import { Link } from 'react-router';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Field } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { isProblem } from '@/lib/api/problems';
import { useApiInfiniteQuery, useApiMutation } from '@/lib/query/api-query';
import { queryKeys } from '@/lib/query/keys';

import { serverFieldErrors } from '../lib/errors';
import { formatDate } from '../lib/format';
import type { Source } from '../lib/types';

const STATUS: Record<Source['status'], { label: string; variant: 'success' | 'neutral' | 'warn'; icon: typeof CircleCheck }> = {
  active: { label: 'Active', variant: 'success', icon: CircleCheck },
  paused: { label: 'Paused', variant: 'neutral', icon: CirclePause },
  failing: { label: 'Failing', variant: 'warn', icon: CircleX },
};

function hostOf(url: string | null | undefined): string | undefined {
  if (!url) return undefined;
  try {
    return new URL(url).hostname;
  } catch {
    return undefined;
  }
}

/** The feeds query the Pillars tab shares between pillars (one request for every pillar). */
export function useFeeds() {
  return useApiInfiniteQuery({
    queryKey: queryKeys.sources.list({ type: 'feed' }),
    path: '/api/sources',
    init: { params: { query: { type: 'feed' } } },
    pageSize: 100,
  });
}

export interface PillarFeedsProps {
  pillarId: string;
  pillarName: string;
  /** False for a pillar added in this session: it must be saved before feeds can point at it. */
  saved: boolean;
}

/**
 * The feeds that bring ideas for one pillar (`sources` with this pillar). Adding one here is a
 * shortcut; pausing, discovering and the scouting queries live in Settings > Sources.
 */
export function PillarFeeds({ pillarId, pillarName, saved }: PillarFeedsProps) {
  const headingId = useId();
  const formId = useId();
  const feeds = useFeeds();
  const [title, setTitle] = useState('');
  const [url, setUrl] = useState('');
  const [localError, setLocalError] = useState<{ title?: string; url?: string }>({});
  const add = useApiMutation('post', '/api/sources', {
    invalidates: [queryKeys.sources.lists()],
    successToast: 'Feed added.',
    errorToast: 'Couldn’t add the feed.',
    handles: ['duplicate_source', 'validation_failed', 'confidential_text'],
  });

  const mine = (feeds.data ?? []).filter((entry) => entry.item.pillar === pillarId);
  const serverErrors = serverFieldErrors(add.error);
  const duplicate = isProblem(add.error, 'duplicate_source');

  const submit = (event: SubmitEvent<HTMLFormElement>) => {
    event.preventDefault();
    const errors: { title?: string; url?: string } = {};
    if (title.trim() === '') errors.title = 'Name the feed.';
    const host = hostOf(url.trim());
    if (!url.trim().startsWith('https://') || host === undefined) errors.url = 'Paste the feed’s full address, the one that starts with https.';
    setLocalError(errors);
    if (errors.title || errors.url) return;
    add.mutate(
      { body: { type: 'feed', title: title.trim(), url: url.trim(), pillar: pillarId } },
      {
        onSuccess: () => {
          setTitle('');
          setUrl('');
        },
      },
    );
  };

  return (
    <section aria-labelledby={headingId} className="space-y-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h4 id={headingId} className="text-sm font-semibold text-ink">
          Feeds
        </h4>
        <Link to="/settings/sources" className="text-sm text-ink underline decoration-line-strong underline-offset-4 hover:decoration-ink">
          Manage feeds and scouting
        </Link>
      </div>
      <p className="text-sm text-ink-muted">{`Feeds bring ideas for ${pillarName.trim() || 'this pillar'} into the Ideas inbox. Nothing is written from them without you.`}</p>
      {feeds.isPending ? (
        <p className="text-sm text-ink-muted">Loading feeds…</p>
      ) : feeds.isError ? (
        <p className="text-sm text-ink-muted">
          The feeds couldn’t be loaded.{' '}
          <Button variant="link" size="sm" onClick={() => void feeds.refetch()}>
            Try again
          </Button>
        </p>
      ) : mine.length === 0 ? (
        <p className="text-sm text-ink-muted">No feeds for this pillar yet.</p>
      ) : (
        <ul className="divide-y divide-line border-y border-line">
          {mine.map(({ item }) => {
            const status = STATUS[item.status];
            const Icon = status.icon;
            const lastRead = formatDate(item.lastSuccessAt);
            return (
              <li key={item.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2.5">
                <Rss aria-hidden="true" className="size-4 shrink-0 text-ink-subtle" />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm text-ink">{item.title}</span>
                  <span className="block truncate text-xs text-ink-subtle">
                    {[hostOf(item.url), lastRead ? `last read ${lastRead}` : undefined].filter(Boolean).join(', ')}
                  </span>
                </span>
                <Badge variant={status.variant}>
                  <Icon aria-hidden="true" />
                  {status.label}
                </Badge>
              </li>
            );
          })}
        </ul>
      )}
      {saved ? (
        <form id={formId} noValidate onSubmit={submit} className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)_auto] sm:items-start">
          <Field label="Feed name" error={localError.title ?? serverErrors.title}>
            <Input
              value={title}
              maxLength={200}
              onChange={(event) => {
                setTitle(event.target.value);
              }}
            />
          </Field>
          <Field
            label="Feed address"
            error={localError.url ?? serverErrors.url ?? (duplicate ? 'You already follow this feed.' : undefined)}
          >
            <Input
              type="url"
              inputMode="url"
              placeholder="https://"
              value={url}
              onChange={(event) => {
                setUrl(event.target.value);
              }}
            />
          </Field>
          <Button type="submit" variant="secondary" disabled={add.isPending} className="sm:mt-6.5">
            <Plus aria-hidden="true" />
            {add.isPending ? 'Adding…' : 'Add feed'}
          </Button>
        </form>
      ) : (
        <p className="text-sm text-ink-muted">Save the new pillar first, then add its feeds.</p>
      )}
    </section>
  );
}
