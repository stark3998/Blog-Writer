import { ArrowLeft } from 'lucide-react';
import type { ReactNode } from 'react';
import { Link, useParams } from 'react-router';

import type { PieceKind } from '@/app/nav';
import { PageHeader } from '@/components/shell/page';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { ProblemAlert } from '@/features/data/problem-alert';
import { useLiveEvents } from '@/features/jobs/use-job';
import { ApiError } from '@/lib/api/errors';
import { useApiQuery } from '@/lib/query/api-query';
import { queryKeys } from '@/lib/query/keys';
import { cn } from '@/lib/cn';
import { useMachineViews } from '@/lib/query/machines';

import { nonEmpty } from './format';
import { NextStep } from './next-step';
import { FactChip, PillarBadge, ReviewOnlyBadge, ServedByBadge, StatusBadge } from './piece-chips';
import { PieceMenu } from './piece-menu';
import { useArticle, useFormatPacks, usePillars, usePost } from './piece-queries';
import { firstLine, isWorkingStatus, LANE_LABEL, POST_FORMAT_LABEL, type PieceFacts } from './piece-status';
import { formatName, pillarView } from './pillars';
import type { Article, Post } from './types';

export interface PieceHeaderProps {
  /** `art-…` or `post-…` (or any id before the API answers). */
  pieceId: string;
  kind: PieceKind;
  /** The open tab's label, for the document title ("Draft – Studio"). */
  tabLabel: string;
}

/** The article's title, or "Untitled piece" while it has none. */
export function articleTitle(article: Pick<Article, 'meta'> | undefined): string {
  return nonEmpty(article?.meta?.title) ?? 'Untitled piece';
}

/** A post's title: the first line of its text (its hook), or "Untitled post". */
export function postTitle(post: Pick<Post, 'displayText'> | undefined): string {
  return firstLine(post?.displayText) ?? 'Untitled post';
}

function BackLink() {
  return (
    <Link
      to="/studio"
      aria-label="Back to Studio"
      className="-ml-1 mb-6 inline-flex items-center gap-1.5 rounded-sm px-1 text-sm font-medium text-ink-muted hover:text-ink"
    >
      <ArrowLeft className="size-4" aria-hidden="true" />
      Studio
    </Link>
  );
}

interface FrameProps {
  title: string;
  documentTitle: string;
  /** The pillar's thread class, drawn beside the title. */
  thread?: string | undefined;
  standfirst?: ReactNode;
  /** The "More actions" menu, at the title's right. */
  menu?: ReactNode;
  children?: ReactNode;
}

/** Title with its pillar thread, then the facts and the next step. */
function Frame({ title, documentTitle, thread, standfirst, menu, children }: FrameProps) {
  return (
    <div className="flex gap-4 md:gap-5">
      <span
        aria-hidden="true"
        data-slot="piece-thread"
        className={cn('mt-1.5 h-9 w-1 shrink-0 rounded-full md:mt-2 md:h-11', thread ?? 'bg-line-strong')}
      />
      <div className="min-w-0 flex-1">
        <div className="flex items-start gap-3">
          <div className="min-w-0 flex-1">
            <PageHeader title={title} documentTitle={documentTitle} standfirst={standfirst}>
              {children}
            </PageHeader>
          </div>
          {menu ? <div className="mt-1 shrink-0 md:mt-2">{menu}</div> : null}
        </div>
      </div>
    </div>
  );
}

/** The header while the piece loads, or when it could not be loaded. */
function UnknownPiece({
  kind,
  tabLabel,
  error,
  loading,
  offline,
  onRetry,
}: {
  kind: PieceKind;
  tabLabel: string;
  error: unknown;
  loading: boolean;
  /** Waiting for a connection (the query is paused). */
  offline: boolean;
  onRetry: () => void;
}) {
  const noun = kind === 'post' ? 'post' : 'article';
  if (offline) {
    return (
      <Frame title={kind === 'post' ? 'Untitled post' : 'Untitled piece'} documentTitle={`${tabLabel} – Studio`}>
        <Alert className="mt-5" role="status">
          <AlertTitle>You’re offline.</AlertTitle>
          <AlertDescription>
            <p>{`The ${noun} loads when your connection is back.`}</p>
          </AlertDescription>
        </Alert>
      </Frame>
    );
  }
  if (loading) {
    return (
      <Frame title={`Loading the ${noun}…`} documentTitle={`${tabLabel} – Studio`}>
        <p role="status" className="sr-only">
          {`Loading the ${noun}`}
        </p>
      </Frame>
    );
  }
  if (error instanceof ApiError && error.kind === 'not-found') {
    return (
      <Frame title="Piece not found" documentTitle={`${tabLabel} – Studio`}>
        <Alert variant="warn" className="mt-5">
          <AlertTitle>{`This ${noun} isn’t there any more, or the link is wrong.`}</AlertTitle>
          <AlertDescription>
            <p>
              Find it on the <Link to="/studio" className="underline underline-offset-4">Studio list</Link>, or check
              Archived.
            </p>
          </AlertDescription>
        </Alert>
      </Frame>
    );
  }
  return (
    <Frame title={kind === 'post' ? 'Untitled post' : 'Untitled piece'} documentTitle={`${tabLabel} – Studio`}>
      <ProblemAlert
        className="mt-5"
        error={error}
        title={`Couldn’t load this ${noun}.`}
        onRetry={onRetry}
        retryLabel={`Try loading the ${noun} again`}
      />
    </Frame>
  );
}

function ArticleHeader({ articleId, tabLabel, currentTab }: { articleId: string; tabLabel: string; currentTab: string }) {
  const article = useArticle(articleId);
  const pillars = usePillars();
  const packs = useFormatPacks();
  const machines = useMachineViews();
  const doc = article.data?.item;
  useLiveEvents(doc !== undefined && isWorkingStatus(doc.status, 'article'));

  // A plan-approved article has its research start queued for later: say when.
  const queuedJobId = doc?.status === 'planned' ? (doc.pipelineJobId ?? undefined) : undefined;
  const queuedJob = useApiQuery(
    {
      queryKey: queryKeys.jobs.detail(queuedJobId ?? 'none'),
      path: '/api/jobs/{job_id}',
      init: { params: { path: { job_id: queuedJobId ?? 'none' } } },
    },
    { enabled: queuedJobId !== undefined, retry: false },
  );

  if (!doc) {
    return (
      <UnknownPiece
        kind="article"
        tabLabel={tabLabel}
        error={article.error}
        loading={article.isPending}
        offline={article.isPending && article.fetchStatus === 'paused'}
        onRetry={() => {
          void article.refetch();
        }}
      />
    );
  }

  const title = articleTitle(doc);
  const pillar = pillarView(doc.meta?.pillar, pillars.data?.item.pillars);
  const format = formatName(doc.meta?.format, packs.data?.item.packs);
  const job = queuedJob.data;
  const facts: PieceFacts = {
    kind: 'article',
    id: doc.id,
    status: doc.status,
    publishAt: doc.publish?.publishAt ?? null,
    autoPublish: doc.publish?.auto ?? false,
    liveUrl: doc.publish?.url ?? null,
    lastError: doc.lastError ?? doc.publish?.lastError ?? null,
    researchStartsAt: job?.status === 'queued' ? (job.scheduledFor ?? null) : null,
  };
  const refresh = () => {
    void article.refetch();
  };

  return (
    <Frame
      title={title}
      documentTitle={`${tabLabel} – ${title} – Studio`}
      thread={pillar?.thread}
      standfirst={nonEmpty(doc.meta?.excerpt)}
      menu={
        <PieceMenu
          facts={facts}
          etag={article.data?.etag}
          machines={machines.data}
          machinesUnavailable={machines.isError}
          onRefresh={refresh}
        />
      }
    >
      <ul aria-label="About this article" className="mt-4 flex flex-wrap items-center gap-2">
        <li>
          <StatusBadge piece={facts} />
        </li>
        {pillar ? (
          <li>
            <PillarBadge pillar={pillar} />
          </li>
        ) : null}
        <li>
          <FactChip>{format ? `${format} article` : 'Article'}</FactChip>
        </li>
        {doc.wordCount > 0 ? (
          <li>
            <FactChip>{`${doc.wordCount.toLocaleString()} words`}</FactChip>
          </li>
        ) : null}
        {doc.reviewOnly ? (
          <li>
            <ReviewOnlyBadge reasons={doc.reviewOnlyReasons} />
          </li>
        ) : null}
        {doc.servedBy && doc.servedBy.length > 0 ? (
          <li>
            <ServedByBadge records={doc.servedBy} />
          </li>
        ) : null}
      </ul>
      <NextStep
        facts={facts}
        currentTab={currentTab}
        etag={article.data?.etag}
        machines={machines.data}
        machinesUnavailable={machines.isError}
        onRefresh={refresh}
      />
    </Frame>
  );
}

function PostHeader({ postId, tabLabel, currentTab }: { postId: string; tabLabel: string; currentTab: string }) {
  const post = usePost(postId);
  const pillars = usePillars();
  const machines = useMachineViews();
  const doc = post.data?.item;
  useLiveEvents(doc !== undefined && isWorkingStatus(doc.status, 'post'));

  if (!doc) {
    return (
      <UnknownPiece
        kind="post"
        tabLabel={tabLabel}
        error={post.error}
        loading={post.isPending}
        offline={post.isPending && post.fetchStatus === 'paused'}
        onRetry={() => {
          void post.refetch();
        }}
      />
    );
  }

  const title = postTitle(doc);
  const pillar = pillarView(doc.pillar, pillars.data?.item.pillars);
  const facts: PieceFacts = {
    kind: 'post',
    id: doc.id,
    status: doc.status,
    slot: doc.slot ?? null,
    postedAt: doc.postedAt ?? null,
    postUrl: doc.postUrl ?? null,
    lastError: doc.lastError ?? null,
  };
  const refresh = () => {
    void post.refetch();
  };

  return (
    <Frame
      title={title}
      documentTitle={`${tabLabel} – ${title} – Studio`}
      thread={pillar?.thread}
      menu={
        <PieceMenu
          facts={facts}
          etag={post.data?.etag}
          machines={machines.data}
          machinesUnavailable={machines.isError}
          onRefresh={refresh}
        />
      }
    >
      <ul aria-label="About this post" className="mt-4 flex flex-wrap items-center gap-2">
        <li>
          <StatusBadge piece={facts} />
        </li>
        {pillar ? (
          <li>
            <PillarBadge pillar={pillar} />
          </li>
        ) : null}
        <li>
          <FactChip>{POST_FORMAT_LABEL[doc.format]}</FactChip>
        </li>
        <li>
          <FactChip>{LANE_LABEL[doc.lane]}</FactChip>
        </li>
        {doc.reviewOnly ? (
          <li>
            <ReviewOnlyBadge reasons={doc.reviewOnlyReasons} />
          </li>
        ) : null}
        {doc.servedBy && doc.servedBy.length > 0 ? (
          <li>
            <ServedByBadge records={doc.servedBy} />
          </li>
        ) : null}
      </ul>
      <NextStep
        facts={facts}
        currentTab={currentTab}
        etag={post.data?.etag}
        machines={machines.data}
        machinesUnavailable={machines.isError}
        onRefresh={refresh}
      />
    </Frame>
  );
}

/**
 * The top of a Studio piece: back to the list, the title with its pillar thread, the facts
 * (status from the state machine, pillar, format, lane, review-only and the models that served
 * it) and the next step. It renders inside the page, above the tabs, from
 * `GET /api/articles/{id}` or `GET /api/posts/{id}` (`queryKeys.articles.detail(id)`), the same
 * cache entries the tabs read.
 */
export function PieceHeader({ pieceId, kind, tabLabel }: PieceHeaderProps) {
  const { tab } = useParams();
  const currentTab = tab ?? 'brief';
  return (
    <>
      <BackLink />
      {kind === 'post' ? (
        <PostHeader postId={pieceId} tabLabel={tabLabel} currentTab={currentTab} />
      ) : (
        <ArticleHeader articleId={pieceId} tabLabel={tabLabel} currentTab={currentTab} />
      )}
    </>
  );
}
