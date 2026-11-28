import { CalendarClock, Sparkles } from 'lucide-react';
import { useId, useState, type ReactNode } from 'react';
import { Link, useSearchParams } from 'react-router';

import { pieceKind } from '@/app/nav';
import { EmptyState } from '@/components/shell/empty-state';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { ProblemAlert } from '@/features/data/problem-alert';
import { JobTimeline } from '@/features/jobs/job-timeline';
import { useApiMutation } from '@/lib/query/api-query';
import { queryKeys } from '@/lib/query/keys';
import { cn } from '@/lib/cn';
import { useArticle } from '@/features/studio/piece';

import type { PieceTabProps } from '../piece/piece-tab';

import { PostStatusBadge } from './components/status-badge';
import { PostComposer } from './composer/post-composer';
import { FORMAT_LABELS, LANE_LABELS } from './model/labels';
import { formatSlot } from './model/time';
import type { Article, PostSummary } from './model/types';
import { useArticlePosts } from './queries';

/**
 * Studio > Posts & carousel (`/studio/:id/posts`): the LinkedIn composer. A standalone post
 * (`post-…`) opens straight in it; an article (`art-…`) lists the posts drafted from it (pick one
 * with `?post=`), or offers to draft them once the article is approved.
 */
export function PostsTab({ pieceId }: PieceTabProps) {
  return pieceKind(pieceId) === 'post' ? (
    <div data-slot="studio-tab" data-piece-id={pieceId}>
      <PostComposer postId={pieceId} />
    </div>
  ) : (
    <div data-slot="studio-tab" data-piece-id={pieceId}>
      <ArticlePosts articleId={pieceId} />
    </div>
  );
}

/** Article states posts may be drafted from (`linkedin.posts.derive.DERIVABLE_STATUSES`). */
function derivable(article: Article | undefined): boolean {
  return article?.status === 'approved' || article?.status === 'publishing' || article?.status === 'published';
}

function ArticlePosts({ articleId }: { articleId: string }) {
  const [params] = useSearchParams();
  const posts = useArticlePosts(articleId);
  const article = useArticle(articleId);

  if (posts.isPending) {
    return (
      <div className="grid gap-4">
        <p role="status" className="sr-only">
          Loading the posts
        </p>
        <Skeleton className="h-16 w-full max-w-measure" />
        <Skeleton className="h-16 w-full max-w-measure" />
      </div>
    );
  }
  if (posts.isError) {
    return (
      <ProblemAlert
        error={posts.error}
        title="Couldn’t load the posts."
        onRetry={() => void posts.refetch()}
        retryLabel="Try loading the posts again"
      />
    );
  }

  const entries = posts.data.filter((entry) => entry.item.status !== 'archived');
  if (entries.length === 0) {
    return (
      <EmptyState
        title="No posts yet."
        yourPart="Pick a hook and approve each post. Approval covers the exact text and images you saw."
      >
        <p>
          Up to two LinkedIn posts come from each article, each with three hook options and a lint check: no links, no
          bait phrases, at most three hashtags.
        </p>
        <p>Carousels use your brand kit: 8 to 12 slides at 1080 × 1350.</p>
        <DerivePanel articleId={articleId} article={article.data?.item} articleError={article.error} first />
      </EmptyState>
    );
  }

  const requested = params.get('post');
  const selected = entries.find((entry) => entry.item.id === requested)?.item.id ?? entries[0]?.item.id ?? '';

  return (
    <div className="grid gap-10">
      <PostPicker
        entries={entries.map((entry) => entry.item)}
        selected={selected}
        hasMore={posts.hasNextPage}
        loadingMore={posts.isFetchingNextPage}
        onMore={() => void posts.fetchNextPage()}
        footer={
          derivable(article.data?.item) ? (
            <DerivePanel articleId={articleId} article={article.data?.item} articleError={null} first={false} />
          ) : null
        }
      />
      <PostComposer key={selected} postId={selected} />
    </div>
  );
}

/** The article's posts as whole-row links (`?post=`), the open one marked. */
function PostPicker({
  entries,
  selected,
  hasMore,
  loadingMore,
  onMore,
  footer,
}: {
  entries: readonly PostSummary[];
  selected: string;
  hasMore: boolean;
  loadingMore: boolean;
  onMore: () => void;
  footer: ReactNode;
}) {
  const headingId = useId();
  return (
    <nav aria-labelledby={headingId} className="grid gap-3">
      <h2 id={headingId} className="font-serif text-2xl font-medium text-ink">
        Posts from this article
      </h2>
      <ul className="grid gap-2 sm:grid-cols-2">
        {entries.map((post) => {
          const current = post.id === selected;
          return (
            <li key={post.id}>
              <Link
                to={{ search: `?post=${encodeURIComponent(post.id)}` }}
                replace
                preventScrollReset
                aria-current={current ? 'page' : undefined}
                className={cn(
                  'grid min-h-11 gap-2 rounded-lg border px-4 py-3 transition-colors duration-150',
                  current ? 'border-ink bg-sheet' : 'border-line bg-sheet hover:bg-wash',
                )}
              >
                <span className="line-clamp-2 text-sm font-medium text-ink">{post.hook || 'No text yet'}</span>
                <span className="flex flex-wrap items-center gap-2 text-xs text-ink-muted">
                  <PostStatusBadge status={post.status} />
                  <span>{FORMAT_LABELS[post.format]}</span>
                  <span>{LANE_LABELS[post.lane]}</span>
                  {post.slot ? (
                    <span className="inline-flex items-center gap-1">
                      <CalendarClock aria-hidden="true" className="size-3.5" />
                      {formatSlot(post.slot)}
                    </span>
                  ) : null}
                </span>
              </Link>
            </li>
          );
        })}
      </ul>
      <div className="flex flex-wrap items-center gap-3">
        {hasMore ? (
          <Button variant="secondary" size="sm" disabled={loadingMore} onClick={onMore}>
            {loadingMore ? 'Loading…' : 'Show more posts'}
          </Button>
        ) : null}
        {footer}
      </div>
    </nav>
  );
}

/**
 * Drafting posts from the article (`POST /api/articles/{id}/posts/derive`, a job): offered once
 * the article is approved or published; the list fills in as the job writes them.
 */
function DerivePanel({
  articleId,
  article,
  articleError,
  first,
}: {
  articleId: string;
  article: Article | undefined;
  articleError: unknown;
  first: boolean;
}) {
  const [jobId, setJobId] = useState<string | null>(null);
  const derive = useApiMutation('post', '/api/articles/{article_id}/posts/derive', {
    invalidates: [queryKeys.posts.lists()],
    handles: ['article_not_ready', 'meta_incomplete', 'ai_use_not_confirmed', 'blocklist_not_configured', 'ai_unavailable'],
    onSuccess: (accepted) => {
      setJobId(accepted.jobId);
    },
  });

  if (!derivable(article)) {
    if (!first) return null;
    return (
      <p>
        {articleError
          ? 'Posts come from an approved article.'
          : 'Posts come from the article once it is approved.'}{' '}
        <Link
          to={`/studio/${encodeURIComponent(articleId)}/draft`}
          className="font-medium text-ink underline decoration-line-strong underline-offset-4 hover:decoration-ink"
        >
          Open the draft
        </Link>
      </p>
    );
  }

  return (
    <div className="grid gap-3">
      <div>
        <Button
          variant={first ? 'primary' : 'ghost'}
          size={first ? 'md' : 'sm'}
          disabled={derive.isPending}
          onClick={() => {
            derive.mutate({ params: { path: { article_id: articleId } } });
          }}
        >
          <Sparkles aria-hidden="true" />
          {derive.isPending ? 'Starting…' : first ? 'Draft posts from this article' : 'Draft again from the article'}
        </Button>
      </div>
      {derive.error ? <ProblemAlert error={derive.error} title="Couldn’t draft the posts." /> : null}
      {jobId ? (
        <JobTimeline jobId={jobId} title="Drafting the posts" labels={{ derive: 'Write the posts and hooks', check: 'Check them' }} />
      ) : null}
    </div>
  );
}
