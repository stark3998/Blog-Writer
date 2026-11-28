import { ArrowUpRight, Plus } from 'lucide-react';
import { useId, useState, type ReactNode } from 'react';

import { pieceKind } from '@/app/nav';
import { EmptyState } from '@/components/shell/empty-state';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { ProblemAlert } from '@/features/data/problem-alert';
import { useApiQuery } from '@/lib/query/api-query';
import { queryKeys } from '@/lib/query/keys';

import { nonEmpty } from '../piece/format';
import type { PieceTabProps } from '../piece/piece-tab';
import { useArticle, usePost } from '../piece/piece-queries';
import type { Article } from '../piece/types';

import { BriefDocument } from './brief-document';
import {
  defaultBriefId,
  defaultRecencyDays,
  failedLatestRun,
  isActiveRun,
  newestFirst,
  type ResearchRun,
} from './brief-model';
import { useBrief, useQuickSearch, useResearchRuns } from './brief-queries';
import { safeHref } from './citation-chip';
import { QuickResult, QuickSearchPanel } from './quick-search-panel';
import { ResearchInProgress, RunProblem } from './research-progress';
import { ResearchRunDialog, type RunDefaults } from './research-run-dialog';
import { RunHistory } from './run-history';

/** Two columns from `lg`: the research and the brief, and quick search beside them. */
function TabLayout({ main, aside }: { main: ReactNode; aside: ReactNode }) {
  return (
    <div data-slot="studio-tab" className="grid gap-12 lg:grid-cols-[minmax(0,1fr)_20rem] xl:grid-cols-[minmax(0,1fr)_22rem]">
      <div className="grid min-w-0 content-start gap-10">{main}</div>
      <div className="min-w-0 lg:sticky lg:top-6 lg:max-h-[calc(100dvh-3rem)] lg:self-start lg:overflow-y-auto lg:border-l lg:border-line lg:pl-8">
        {aside}
      </div>
    </div>
  );
}

function BriefSkeleton() {
  return (
    <div className="grid gap-3">
      <p role="status" className="sr-only">
        Loading the brief
      </p>
      <Skeleton className="h-8 w-40" />
      <Skeleton className="h-4 w-[min(36rem,100%)]" />
      <Skeleton className="h-4 w-[min(32rem,95%)]" />
      <Skeleton className="h-4 w-[min(28rem,90%)]" />
    </div>
  );
}

interface DialogState {
  readonly defaults: RunDefaults;
  readonly focusSources: boolean;
}

function articleDefaults(article: Article | undefined): RunDefaults {
  return {
    topic: nonEmpty(article?.meta?.title) ?? '',
    pillar: article?.meta?.pillar ?? '',
    audience: '',
    recencyDays: defaultRecencyDays(article?.meta?.format),
    ownerUrls: [],
  };
}

function runDefaults(run: ResearchRun): RunDefaults {
  return {
    topic: run.topic,
    pillar: run.pillar,
    audience: run.audience ?? '',
    recencyDays: run.recencyDays,
    ownerUrls: run.ownerUrls ?? [],
  };
}

/** Why a brief researched here may not be the one the draft uses (the pipeline links its own). */
function dialogNote(article: Article | undefined): string | undefined {
  if (!article) return undefined;
  if (article.briefId) return 'The draft keeps using its current brief. This run gives you another one to read.';
  if (article.status === 'planned') {
    return 'The draft is written from the pipeline’s own research, which “Start research” begins. Use a run here to explore first or to read your own sources.';
  }
  return undefined;
}

function ArticleBrief({ articleId }: { articleId: string }) {
  const article = useArticle(articleId);
  const runs = useResearchRuns(articleId);
  const [chosenBriefId, setChosenBriefId] = useState<string | undefined>(undefined);
  const [dialog, setDialog] = useState<DialogState | null>(null);

  const doc = article.data?.item;
  const entries = runs.data ?? [];
  const runList = newestFirst(entries.map((entry) => entry.item));
  const etagOf = (runId: string) => entries.find((entry) => entry.item.id === runId)?.etag ?? null;
  const active = runList.find(isActiveRun);
  const failed = active ? undefined : failedLatestRun(runList);
  const linkedBriefId = doc?.briefId ?? undefined;
  const shownBriefId = chosenBriefId ?? defaultBriefId(linkedBriefId, runList);
  const brief = useBrief(shownBriefId);
  const briefRun = runList.find((run) => run.briefId === shownBriefId);
  const planned = doc?.status === 'planned';

  const openDialog = (state: DialogState) => {
    setDialog(state);
  };
  const newRun = () => {
    openDialog({ defaults: articleDefaults(doc), focusSources: false });
  };

  const main = (
    <>
      {runList.length > 0 || shownBriefId ? (
        <div className="flex flex-wrap items-center gap-3">
          <Button variant="secondary" disabled={active !== undefined} onClick={newRun}>
            <Plus aria-hidden="true" />
            New research run
          </Button>
          {active ? <p className="text-sm text-ink-muted">One run at a time: this one is still working.</p> : null}
        </div>
      ) : null}

      {active ? (
        <ResearchInProgress run={active} etag={etagOf(active.id)} forPipeline={doc?.status === 'researching'} />
      ) : null}
      {failed ? (
        <RunProblem
          run={failed}
          onRetry={() => {
            openDialog({ defaults: runDefaults(failed), focusSources: false });
          }}
        />
      ) : null}

      {runs.isPending ? (
        <BriefSkeleton />
      ) : runs.isError ? (
        <ProblemAlert
          error={runs.error}
          title="Couldn’t load the research runs."
          onRetry={() => {
            void runs.refetch();
          }}
          retryLabel="Try loading the research runs again"
        />
      ) : shownBriefId ? (
        brief.isPending ? (
          <BriefSkeleton />
        ) : brief.isError ? (
          <ProblemAlert
            error={brief.error}
            title="Couldn’t load the brief."
            onRetry={() => {
              void brief.refetch();
            }}
            retryLabel="Try loading the brief again"
          />
        ) : (
          <BriefDocument
            brief={brief.data.item}
            etag={brief.data.etag}
            linked={brief.data.item.id === linkedBriefId}
            linkNote={
              linkedBriefId && brief.data.item.id !== linkedBriefId
                ? 'The draft is written from another brief: the one marked “Used for drafting” below.'
                : undefined
            }
            run={briefRun}
            onReload={() => {
              void brief.refetch();
            }}
            onAddSources={() => {
              openDialog({
                defaults: {
                  topic: brief.data.item.topic,
                  pillar: brief.data.item.pillar,
                  audience: brief.data.item.audience ?? '',
                  recencyDays: briefRun?.recencyDays ?? articleDefaults(doc).recencyDays,
                  ownerUrls: briefRun?.ownerUrls ?? [],
                },
                focusSources: true,
              });
            }}
          />
        )
      ) : active ? null : (
        <EmptyState
          title="No brief yet."
          yourPart="Start research, add sources you trust, then read the brief and remove anything weak before the interview."
        >
          <p>
            Research plans sub-questions, searches your pillar’s trusted sites and the open web, reads the best pages,
            then checks dates and wants two sources for every key number.
          </p>
          <p>
            The brief that comes back has a summary, cited findings, numbers, counterpoints, open questions, angles and
            hook ideas. The draft is written from the brief alone.
          </p>
          {planned ? (
            <p className="text-ink">
              “Start research” at the top begins it, and the interview and the draft follow on their own. To read
              sources first, research on its own here.
            </p>
          ) : null}
          <div className="pt-2">
            <Button variant={planned ? 'secondary' : 'primary'} onClick={newRun}>
              {planned ? 'Research on its own' : 'Start a research run'}
            </Button>
          </div>
        </EmptyState>
      )}

      <RunHistory
        runs={runList}
        shownBriefId={shownBriefId}
        linkedBriefId={linkedBriefId}
        onShowBrief={(briefId) => {
          setChosenBriefId(briefId);
        }}
        hasMore={runs.hasNextPage}
        loadingMore={runs.isFetchingNextPage}
        onLoadMore={() => {
          void runs.fetchNextPage();
        }}
      />

      {dialog ? (
        <ResearchRunDialog
          open
          onOpenChange={(open) => {
            if (!open) setDialog(null);
          }}
          defaults={dialog.defaults}
          focusSources={dialog.focusSources}
          articleId={articleId}
          ideaId={doc?.ideaId}
          note={dialogNote(doc)}
        />
      ) : null}
    </>
  );

  return <TabLayout main={main} aside={<QuickSearchPanel scope={{ articleId }} pillar={doc?.meta?.pillar} />} />;
}

function IdeaSummary({ ideaId }: { ideaId: string }) {
  const headingId = useId();
  const idea = useApiQuery({
    queryKey: queryKeys.ideas.detail(ideaId),
    path: '/api/ideas/{idea_id}',
    init: { params: { path: { idea_id: ideaId } } },
  });
  if (idea.isPending) return <BriefSkeleton />;
  if (idea.isError) {
    return (
      <ProblemAlert
        error={idea.error}
        title="Couldn’t load the idea this post started from."
        onRetry={() => {
          void idea.refetch();
        }}
        retryLabel="Try loading the idea again"
      />
    );
  }
  const item = idea.data.item;
  const href = safeHref(item.url);
  return (
    <section aria-labelledby={headingId}>
      <h2 id={headingId} className="font-serif text-2xl font-medium text-ink">
        The idea
      </h2>
      <p className="mt-2 max-w-measure font-serif text-xl text-ink">{item.title}</p>
      {item.angle ? <p className="mt-2 max-w-measure text-ink-muted">{`Angle: ${item.angle}`}</p> : null}
      {item.note ? <p className="mt-2 max-w-measure text-ink-muted">{item.note}</p> : null}
      {href ? (
        <a
          href={href}
          target="_blank"
          rel="noopener noreferrer"
          className="mt-3 inline-flex items-center gap-1 text-sm font-medium text-ink underline decoration-line-strong underline-offset-4 hover:decoration-ink"
        >
          {item.publisher ? `Read it on ${item.publisher}` : 'Read the original'}
          <ArrowUpRight aria-hidden="true" className="size-4" />
          <span className="sr-only"> (opens in a new tab)</span>
        </a>
      ) : null}
    </section>
  );
}

function CitedQuickSearch({ quickId }: { quickId: string }) {
  const headingId = useId();
  const search = useQuickSearch(quickId);
  if (search.isPending) return <BriefSkeleton />;
  if (search.isError) {
    return (
      <ProblemAlert
        error={search.error}
        title="Couldn’t load the search this post cites."
        onRetry={() => {
          void search.refetch();
        }}
        retryLabel="Try loading the cited search again"
      />
    );
  }
  return (
    <section aria-labelledby={headingId}>
      <h2 id={headingId} className="font-serif text-2xl font-medium text-ink">
        Cited in this post
      </h2>
      <p className="mt-1 max-w-measure text-ink-muted">{search.data.item.query}</p>
      <QuickResult search={search.data.item} />
    </section>
  );
}

function PostBrief({ postId }: { postId: string }) {
  const post = usePost(postId);
  const doc = post.data?.item;
  const briefId = doc?.briefId ?? undefined;
  const brief = useBrief(briefId);
  const ideaId = doc?.ideaId ?? undefined;
  const quickId = doc?.quickSearchId ?? undefined;

  const main = (
    <>
      {post.isPending ? <BriefSkeleton /> : null}
      {ideaId ? <IdeaSummary ideaId={ideaId} /> : null}
      {quickId ? <CitedQuickSearch quickId={quickId} /> : null}
      {briefId && brief.data ? (
        <BriefDocument
          brief={brief.data.item}
          etag={brief.data.etag}
          linked={false}
          onReload={() => {
            void brief.refetch();
          }}
        />
      ) : null}
      {briefId && brief.isError ? (
        <ProblemAlert
          error={brief.error}
          title="Couldn’t load the brief."
          onRetry={() => {
            void brief.refetch();
          }}
          retryLabel="Try loading the brief again"
        />
      ) : null}
      {doc && !ideaId && !quickId && !briefId ? (
        <EmptyState
          title="No brief yet."
          yourPart="Ask what you need to check while you review the post, and keep only claims you would stand behind."
        >
          <p>
            A LinkedIn post doesn’t get a full research run. Quick search answers one question at a time from your
            pillar’s trusted sites, with its sources, and a quick take can cite what it finds.
          </p>
        </EmptyState>
      ) : null}
    </>
  );

  return <TabLayout main={main} aside={<QuickSearchPanel scope={{ postId }} pillar={doc?.pillar} />} />;
}

/**
 * Studio > Brief & research (`/studio/:id/brief`). For an article: research runs (start, live
 * progress through the job timeline, cancel), the brief (summary, cited findings with source chips
 * and dates, numbers with the two-source marker, counterpoints, open questions, angles, hook ideas,
 * claims and quotes, sources to remove or add), and quick search beside it. For a standalone post:
 * the idea it came from, the search it cites, and quick search. Calls `GET /api/articles/{id}`,
 * `GET/POST /api/research/runs`, `POST .../cancel`, `GET/PUT /api/research/briefs/{id}`,
 * `GET/POST /api/research/quick` and the job's events (contract section 10).
 */
export function BriefTab({ pieceId }: PieceTabProps) {
  return pieceKind(pieceId) === 'post' ? <PostBrief postId={pieceId} /> : <ArticleBrief articleId={pieceId} />;
}
