import { CircleDot, CirclePause, MoreHorizontal, Pencil, Play, Plus, RefreshCw, Trash2, TriangleAlert, X } from 'lucide-react';
import { useRef, useState } from 'react';

import { EmptyState } from '@/components/shell/empty-state';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { SegmentedControl, SegmentedControlItem } from '@/components/ui/segmented-control';
import { ProblemAlert } from '@/features/data/problem-alert';
import type { Versioned } from '@/lib/api/envelope';
import { toastError } from '@/lib/api/toast';
import { apiRequest } from '@/lib/api/request';
import { useAppData } from '@/lib/app-data';
import { useApiInfiniteQuery, useApiMutation } from '@/lib/query/api-query';
import { queryKeys } from '@/lib/query/keys';

import { LastJob } from './components/last-job';
import { LiveJob } from './components/live-job';
import { BlockLoading, SectionStack, SettingsBlock } from './components/settings-block';
import { StateBadge, type StateLook } from './components/status-badge';
import { useNow } from '@/lib/use-now';
import { PillarBadge } from '@/components/ui/pillar-badge';
import { formatDateTime, formatNumber, formatRelative } from './format';
import { machineAllows, pillarNames, settingsKeys, useMachines, usePillars, useSchedules } from './queries';
import { SourceDialog, type Source } from './sources/source-dialog';

type TypeFilter = 'all' | 'feed' | 'scout_query';

const FILTERS: readonly { value: TypeFilter; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'feed', label: 'Feeds' },
  { value: 'scout_query', label: 'Scouting' },
];

const STATUS_LOOKS: Readonly<Record<Source['status'], StateLook>> = {
  active: { label: 'Active', tone: 'success', icon: CircleDot },
  paused: { label: 'Paused', tone: 'neutral', icon: CirclePause },
  failing: { label: 'Failing', tone: 'danger', icon: TriangleAlert },
};

/** Why the last read of a feed failed, from its code (`signals.crawl.fetch_source`). */
export function fetchErrorSentence(code: string): string {
  const http = /^http_(\d{3})$/.exec(code);
  if (http) return http[1] === '404' ? 'The feed’s address answered “not found”.' : `The feed answered HTTP ${http[1] ?? ''}.`;
  switch (code) {
    case 'not_a_feed':
      return 'The address didn’t return a feed.';
    case 'timeout':
      return 'The site took too long to answer.';
    case 'too_large':
      return 'The feed is larger than the download limit.';
    case 'insecure_url':
    case 'scheme_not_allowed':
      return 'The address isn’t https, so it isn’t read.';
    case 'local_host_name':
    case 'numeric_host':
    case 'invalid_host':
    case 'port_not_allowed':
    case 'credentials_in_url':
    case 'invalid_url':
      return 'The address isn’t one the app may fetch.';
    default:
      return `The last read failed (${code}).`;
  }
}

const CRAWL_LABELS: Readonly<Record<string, string>> = {
  fetch: 'Read the feeds',
  triage: 'Sort new items into ideas',
};

function SourceRow({
  entry,
  pillarName,
  now,
  canPause,
  canResume,
  onEdit,
  onRemoved,
}: {
  entry: Versioned<Source>;
  pillarName: string | undefined;
  now: number;
  canPause: boolean;
  canResume: boolean;
  onEdit: () => void;
  /** Called after the source is gone, so focus can move somewhere sensible. */
  onRemoved: () => void;
}) {
  const source = entry.item;
  const [removing, setRemoving] = useState(false);
  const refresh = { updates: (data: Versioned<Source>) => queryKeys.sources.detail(data.item.id), invalidates: [queryKeys.sources.lists()] };
  const pause = useApiMutation('post', '/api/sources/{source_id}/pause', {
    ...refresh,
    successToast: `Paused “${source.title}”.`,
    errorToast: `Couldn’t pause “${source.title}”.`,
  });
  const resume = useApiMutation('post', '/api/sources/{source_id}/resume', {
    ...refresh,
    successToast: `Resumed “${source.title}”.`,
    errorToast: `Couldn’t resume “${source.title}”.`,
  });
  const remove = useApiMutation('delete', '/api/sources/{source_id}', {
    invalidates: [queryKeys.sources.lists()],
    successToast: `Stopped following “${source.title}”.`,
    errorToast: `Couldn’t remove “${source.title}”.`,
    onSuccess: onRemoved,
  });
  const params = { params: { path: { source_id: source.id } }, ifMatch: entry.etag };
  const busy = pause.isPending || resume.isPending || remove.isPending;
  const address = source.type === 'feed' ? source.url : source.query;

  return (
    <li className="grid gap-x-6 gap-y-3 border-b border-line py-5 md:grid-cols-[minmax(0,1fr)_minmax(0,16rem)_auto]">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <p className="font-semibold text-ink">{source.title}</p>
          {source.pillar ? <PillarBadge id={source.pillar} name={pillarName} /> : null}
        </div>
        <p className="mt-1 text-sm text-ink-muted">{source.type === 'feed' ? 'Feed' : 'Weekly scouting query'}</p>
        {address ? <p className="mt-1 break-all text-sm text-ink-subtle">{address}</p> : null}
      </div>
      <dl className="grid grid-cols-[auto_minmax(0,1fr)] content-start gap-x-3 gap-y-1 text-sm">
        <dt className="text-ink-subtle">Checked</dt>
        <dd className="text-ink">
          {source.lastCrawledAt ? (
            <time dateTime={source.lastCrawledAt} title={formatDateTime(source.lastCrawledAt)}>
              {formatRelative(source.lastCrawledAt, now)}
            </time>
          ) : (
            'Not yet'
          )}
        </dd>
        <dt className="text-ink-subtle">Items</dt>
        <dd className="text-ink">{formatNumber(source.itemsSeen, { whole: true })} seen</dd>
        {source.lastError ? (
          <>
            <dt className="text-ink-subtle">Problem</dt>
            <dd className="text-ink">
              {fetchErrorSentence(source.lastError)}
              {source.consecutiveFailures > 1 ? ` ${String(source.consecutiveFailures)} times in a row.` : null}
            </dd>
          </>
        ) : null}
      </dl>
      <div className="flex items-start gap-2 md:justify-end">
        <StateBadge look={STATUS_LOOKS[source.status]} className="mt-2" />
        {/* Not modal: a dialog opened from the menu takes over focus and pointer events cleanly. */}
        <DropdownMenu modal={false}>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="icon" disabled={busy} aria-label={`Actions for “${source.title}”`}>
              <MoreHorizontal aria-hidden="true" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem onSelect={onEdit}>
              <Pencil aria-hidden="true" />
              Edit
            </DropdownMenuItem>
            {canPause ? (
              <DropdownMenuItem
                onSelect={() => {
                  pause.mutate(params);
                }}
              >
                <CirclePause aria-hidden="true" />
                Pause
              </DropdownMenuItem>
            ) : null}
            {canResume ? (
              <DropdownMenuItem
                onSelect={() => {
                  resume.mutate(params);
                }}
              >
                <Play aria-hidden="true" />
                {source.status === 'failing' ? 'Try again' : 'Resume'}
              </DropdownMenuItem>
            ) : null}
            <DropdownMenuSeparator />
            <DropdownMenuItem
              variant="danger"
              onSelect={() => {
                setRemoving(true);
              }}
            >
              <Trash2 aria-hidden="true" />
              Stop following
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
      <AlertDialog open={removing} onOpenChange={setRemoving}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{`Stop following “${source.title}”?`}</AlertDialogTitle>
            <AlertDialogDescription>
              Ideas it already found stay in your inbox. Its other records age out within 90 days.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep it</AlertDialogCancel>
            <AlertDialogAction
              variant="danger"
              onClick={() => {
                remove.mutate(params);
              }}
            >
              Stop following
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </li>
  );
}

/** The newest crawl and when the next one is due (`sched-signals-crawl`). */
function CrawlStatus({ now }: { now: number }) {
  const schedules = useSchedules();
  const crawl = schedules.data?.find((item) => item.jobType === 'signals.crawl');
  return (
    <div className="grid max-w-measure gap-1.5">
      <LastJob type="signals.crawl" label="Last crawl" steps={CRAWL_LABELS} empty="No crawl has run yet." />
      {crawl ? (
        <p className="text-sm text-ink-muted">
          {crawl.status === 'paused'
            ? 'The scheduled crawl is paused (Settings > Slots).'
            : crawl.status === 'needs_confirmation'
              ? 'A missed crawl is waiting for you in Settings > Slots.'
              : `Next crawl ${formatRelative(crawl.nextRunAt, now)}.`}
        </p>
      ) : null}
    </div>
  );
}

/**
 * Settings > Sources (`/settings/sources`): the feeds and weekly scouting queries, their health,
 * discovery of a site's feeds, and "Crawl now". Calls `/api/sources*`, `POST /api/sources/discover`
 * and `POST /api/sources/crawl` (contract section 10). Feed items only ever become ideas: nothing
 * here starts writing.
 */
export function SourcesSection() {
  const { api } = useAppData();
  const [filter, setFilter] = useState<TypeFilter>('all');
  const [dialog, setDialog] = useState<{ open: boolean; source?: Versioned<Source> }>({ open: false });
  const [dialogKey, setDialogKey] = useState(0);
  const [crawlJob, setCrawlJob] = useState<string | undefined>();
  const addRef = useRef<HTMLButtonElement>(null);
  const now = useNow();
  const pillars = usePillars();
  const machines = useMachines();
  const sources = useApiInfiniteQuery({
    queryKey: settingsKeys.sources({ type: filter }),
    path: '/api/sources',
    init: { params: { query: filter === 'all' ? {} : { type: filter } } },
    pageSize: 50,
  });
  const crawl = useApiMutation('post', '/api/sources/crawl', {
    successToast: 'Crawling the feeds now.',
    errorToast: 'Couldn’t start the crawl.',
  });
  const names = pillarNames(pillars.data?.item.pillars);
  const pillarList = pillars.data?.item.pillars ?? [];

  const openDialog = (source?: Versioned<Source>) => {
    setDialogKey((key) => key + 1);
    setDialog({ open: true, source });
  };

  const reload = async (id: string): Promise<Versioned<Source> | undefined> => {
    try {
      return await apiRequest(api, 'get', '/api/sources/{source_id}', { params: { path: { source_id: id } } });
    } catch (error) {
      toastError(error, { title: 'Couldn’t reload the source.' });
      return undefined;
    }
  };

  const list = sources.data ?? [];
  const empty = sources.isSuccess && list.length === 0;

  return (
    <SectionStack>
      <SettingsBlock
        title="Feeds and scouting"
        description="Feeds are read every few hours and scouting runs once a week; new items are sorted into your Ideas inbox by pillar and relevance. Nothing is written until you pick an idea."
        actions={
          <>
            <Button
              variant="secondary"
              disabled={crawl.isPending}
              onClick={() => {
                crawl.mutate(undefined, {
                  onSuccess: (accepted) => {
                    setCrawlJob(accepted.jobId);
                  },
                });
              }}
            >
              <RefreshCw aria-hidden="true" />
              {crawl.isPending ? 'Starting…' : 'Crawl now'}
            </Button>
            <Button
              ref={addRef}
              onClick={() => {
                openDialog();
              }}
            >
              <Plus aria-hidden="true" />
              Add a source
            </Button>
          </>
        }
      >
        <div className="space-y-6">
          <CrawlStatus now={now} />
          {crawlJob ? (
            <div className="flex items-start justify-between gap-4 rounded-lg border border-line p-4">
              <LiveJob jobId={crawlJob} title="Crawling the feeds" labels={CRAWL_LABELS} />
              <Button
                variant="ghost"
                size="icon-sm"
                aria-label="Hide the crawl progress"
                onClick={() => {
                  setCrawlJob(undefined);
                }}
              >
                <X aria-hidden="true" />
              </Button>
            </div>
          ) : null}

          <SegmentedControl
            aria-label="Show"
            value={filter}
            onValueChange={(value) => {
              const next = FILTERS.find((item) => item.value === value);
              if (next) setFilter(next.value);
            }}
          >
            {FILTERS.map((item) => (
              <SegmentedControlItem key={item.value} value={item.value}>
                {item.label}
              </SegmentedControlItem>
            ))}
          </SegmentedControl>

          {sources.isPending ? (
            <BlockLoading label="Loading the sources" />
          ) : sources.isError ? (
            <ProblemAlert
              error={sources.error}
              title="Couldn’t load the sources."
              onRetry={() => void sources.refetch()}
              retryLabel="Try loading the sources again"
            />
          ) : empty && filter === 'all' ? (
            <EmptyState title="No sources yet." yourPart="Add the feeds worth watching for each pillar, and a scouting query or two.">
              <p>
                RSS feeds and weekly scouting for each pillar. New items are sorted into the Ideas list. Feed items never
                generate content without your action.
              </p>
            </EmptyState>
          ) : empty ? (
            <p className="text-ink-muted">{filter === 'feed' ? 'No feeds yet.' : 'No scouting queries yet.'}</p>
          ) : (
            <>
              <ul aria-label="Sources" className="border-t border-line">
                {list.map((entry) => {
                  const status = entry.item.status;
                  const allows = (to: string) => machineAllows(machines.data, 'source', status, to);
                  return (
                    <SourceRow
                      key={entry.item.id}
                      entry={entry}
                      pillarName={entry.item.pillar ? names.get(entry.item.pillar) : undefined}
                      now={now}
                      canPause={allows('paused') ?? status !== 'paused'}
                      canResume={allows('active') ?? status !== 'active'}
                      onEdit={() => {
                        openDialog(entry);
                      }}
                      onRemoved={() => {
                        addRef.current?.focus();
                      }}
                    />
                  );
                })}
              </ul>
              {sources.hasNextPage ? (
                <Button
                  variant="secondary"
                  disabled={sources.isFetchingNextPage}
                  onClick={() => void sources.fetchNextPage()}
                >
                  {sources.isFetchingNextPage ? 'Loading…' : 'Show more sources'}
                </Button>
              ) : null}
            </>
          )}
        </div>
      </SettingsBlock>

      {dialog.open || dialog.source ? (
        <SourceDialog
          key={dialogKey}
          open={dialog.open}
          onOpenChange={(open) => {
            setDialog((current) => ({ ...current, open }));
          }}
          source={dialog.source}
          pillars={pillarList}
          onReload={dialog.source ? () => reload(dialog.source?.item.id ?? '') : undefined}
        />
      ) : null}
    </SectionStack>
  );
}
