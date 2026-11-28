import { Plus } from 'lucide-react';
import { useId, useState } from 'react';
import { useSearchParams } from 'react-router';

import { STUDIO_TABS } from '@/app/nav';
import { EmptyState } from '@/components/shell/empty-state';
import { Page, PageHeader } from '@/components/shell/page';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { ProblemAlert } from '@/features/data/problem-alert';
import { useLiveEvents } from '@/features/jobs/use-job';

import { useFormatPacks, usePillars } from '../piece/piece-queries';
import type { FormatPack, Pillar } from '../piece/types';

import { groupPieces, matchesSearch, parseKind, parseShow, type KindFilter, type PieceGroup, type ShowFilter } from './list-model';
import { NewPieceDialog } from './new-piece-dialog';
import { PieceRow } from './piece-row';
import { StudioFilters } from './studio-filters';
import { usePrefetchPiece, useStudioPieces } from './use-studio-pieces';

function LoadingRows() {
  return (
    <div className="mt-8">
      <p role="status" className="sr-only">
        Loading pieces
      </p>
      <div aria-hidden="true" className="divide-y divide-line border-y border-line">
        {[0, 1, 2, 3].map((index) => (
          <div key={index} className="flex gap-4 py-4">
            <Skeleton className="h-12 w-1" />
            <div className="grid flex-1 gap-2">
              <Skeleton className="h-5 w-[min(26rem,80%)]" />
              <Skeleton className="h-4 w-[min(18rem,60%)]" />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function GroupSection({
  group,
  pillars,
  packs,
  onIntent,
}: {
  group: PieceGroup;
  pillars: readonly Pillar[] | undefined;
  packs: readonly FormatPack[] | undefined;
  onIntent: ReturnType<typeof usePrefetchPiece>;
}) {
  const headingId = useId();
  const count = group.pieces.length;
  return (
    <section aria-labelledby={headingId} data-group={group.group} className="mt-10 first:mt-8">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h2 id={headingId} className="font-serif text-2xl font-medium text-ink">
          {group.label}
          <span className="sr-only">{`, ${String(count)} ${count === 1 ? 'piece' : 'pieces'}`}</span>
        </h2>
        <span aria-hidden="true" className="tabular-nums text-ink-subtle">
          {count}
        </span>
        <p className="basis-full text-sm text-ink-muted sm:basis-auto">{group.description}</p>
      </div>
      <ul className="mt-3 divide-y divide-line border-y border-line">
        {group.pieces.map((piece) => (
          <PieceRow key={`${piece.kind}-${piece.id}`} piece={piece} pillars={pillars} packs={packs} onIntent={onIntent} />
        ))}
      </ul>
    </section>
  );
}

/**
 * Studio (`/studio`): every piece in progress, articles and standalone posts, grouped by what they
 * need (you first), with filters for status, kind and pillar, a title search and "New piece".
 * Calls `GET /api/articles`, `GET /api/posts?lane=native&lane=quick_take&lane=repurpose`,
 * `POST /api/articles`, `POST /api/posts` (contract section 10).
 */
export function StudioListPage() {
  const [params, setParams] = useSearchParams();
  const show = parseShow(params.get('show'));
  const kind = parseKind(params.get('kind'));
  const pillarParam = params.get('pillar');
  const pillar = pillarParam === null || pillarParam === '' ? undefined : pillarParam;
  const search = params.get('q') ?? '';
  const [creating, setCreating] = useState(false);

  const pillars = usePillars();
  const packs = useFormatPacks();
  const list = useStudioPieces({ show, kind, pillar });
  const prefetch = usePrefetchPiece();

  const visible = list.pieces.filter((piece) => matchesSearch(piece, search));
  const groups = groupPieces(visible);
  useLiveEvents(visible.some((piece) => piece.group === 'working'));

  const update = (changes: Readonly<Record<string, string | undefined>>) => {
    setParams(
      (current) => {
        const next = new URLSearchParams(current);
        for (const [name, value] of Object.entries(changes)) {
          if (value === undefined || value === '') next.delete(name);
          else next.set(name, value);
        }
        return next;
      },
      { replace: true, preventScrollReset: true },
    );
  };

  const filtered = show !== 'active' || kind !== 'all' || pillar !== undefined || search.trim() !== '';
  const errors = [
    list.articles?.isError ? { key: 'articles', error: list.articles.error, retry: list.articles.refetch, noun: 'articles' } : undefined,
    list.posts?.isError ? { key: 'posts', error: list.posts.error, retry: list.posts.refetch, noun: 'posts' } : undefined,
  ].filter((entry) => entry !== undefined);
  const settled = !list.loading && !list.offline;
  const pillarList = pillars.data?.item.pillars;
  const packList = packs.data?.item.packs;

  const newPiece = (
    <Button
      onClick={() => {
        setCreating(true);
      }}
    >
      <Plus aria-hidden="true" />
      New piece
    </Button>
  );

  return (
    <Page>
      <PageHeader
        title="Studio"
        standfirst="One workspace per piece, from the first brief to go-live. Articles and their LinkedIn posts are written here."
      >
        <div className="mt-6">{newPiece}</div>
      </PageHeader>

      <StudioFilters
        show={show}
        kind={kind}
        pillar={pillar}
        search={search}
        pillars={pillarList}
        onShowChange={(value: ShowFilter) => {
          update({ show: value === 'active' ? undefined : value });
        }}
        onKindChange={(value: KindFilter) => {
          update({ kind: value === 'all' ? undefined : value });
        }}
        onPillarChange={(value) => {
          update({ pillar: value });
        }}
        onSearchChange={(value) => {
          update({ q: value });
        }}
      />

      {errors.map((entry) => (
        <ProblemAlert
          key={entry.key}
          className="mt-6"
          error={entry.error}
          title={`Couldn’t load the ${entry.noun}.`}
          onRetry={() => {
            void entry.retry();
          }}
          retryLabel={`Try loading the ${entry.noun} again`}
        />
      ))}

      {list.offline && list.pieces.length === 0 ? (
        <Alert className="mt-6" role="status">
          <AlertTitle>You’re offline.</AlertTitle>
          <AlertDescription>
            <p>Your pieces load when your connection is back.</p>
          </AlertDescription>
        </Alert>
      ) : null}

      {list.loading && list.pieces.length === 0 ? <LoadingRows /> : null}

      {settled && errors.length === 0 && list.pieces.length === 0 && !filtered ? (
        <EmptyState
          className="mt-10"
          title="No pieces in progress."
          yourPart="Answer the interview, then review the draft and its posts. Research, drafting and critique run in the background."
        >
          <p>
            A piece starts when you approve the weekly plan, or when you start one here from an idea or a blank page.
            Each piece moves through five stages, each with its own tab:
          </p>
          <ol className="border-t border-line">
            {STUDIO_TABS.map((tab) => (
              <li key={tab.id} className="border-b border-line py-3 font-medium text-ink">
                {tab.label}
              </li>
            ))}
          </ol>
        </EmptyState>
      ) : null}

      {settled && list.pieces.length > 0 && visible.length === 0 ? (
        <p role="status" className="mt-10 text-ink-muted">
          {`No piece matches “${search.trim()}”.`}{' '}
          <Button
            variant="link"
            onClick={() => {
              update({ q: undefined });
            }}
          >
            Clear the search
          </Button>
        </p>
      ) : null}

      {settled && errors.length === 0 && list.pieces.length === 0 && filtered ? (
        <p role="status" className="mt-10 text-ink-muted">
          No pieces match these filters.{' '}
          <Button
            variant="link"
            onClick={() => {
              update({ show: undefined, kind: undefined, pillar: undefined, q: undefined });
            }}
          >
            Show every piece in progress
          </Button>
        </p>
      ) : null}

      <div aria-busy={list.stale || undefined} className={list.stale ? 'opacity-60 transition-opacity' : undefined}>
        {groups.map((group) => (
          <GroupSection key={group.group} group={group} pillars={pillarList} packs={packList} onIntent={prefetch} />
        ))}
      </div>

      {list.hasNextPage ? (
        <div className="mt-8 flex flex-wrap items-center gap-3">
          <Button
            variant="secondary"
            disabled={list.fetchingNextPage}
            onClick={() => {
              list.fetchNextPage();
            }}
          >
            {list.fetchingNextPage ? 'Loading more…' : 'Show more pieces'}
          </Button>
          {search.trim() !== '' ? (
            <p className="text-sm text-ink-muted">The search covers the pieces loaded so far.</p>
          ) : null}
        </div>
      ) : null}

      <NewPieceDialog open={creating} onOpenChange={setCreating} />
    </Page>
  );
}
