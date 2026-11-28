import { CalendarClock, ChevronRight, PenLine } from 'lucide-react';
import { useEffect, useId, useRef } from 'react';
import { Link, useSearchParams } from 'react-router';

import { pieceKind } from '@/app/nav';
import { EmptyState } from '@/components/shell/empty-state';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { buttonVariants } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { ProblemAlert } from '@/features/data/problem-alert';
import { cn } from '@/lib/cn';

import type { PieceTabProps } from '../piece/piece-tab';
import { PostStatusBadge, WorkingIcon } from '../posts/components/status-badge';
import { useNow } from '@/lib/use-now';
import { FORMAT_LABELS } from '../posts/model/labels';
import { formatMoment, formatSlot, relativeTime } from '../posts/model/time';
import { postsPath, type GoLiveView, type Post, type PostStatus, type PostSummary } from '../posts/model/types';
import { useArticlePosts } from '../posts/queries';

import { AttentionPanel } from './attention-panel';
import { KitPanel } from './kit-panel';
import { LivePanel } from './live-panel';
import { MissedPanel } from './missed-panel';
import { PublishPanel } from './publish-panel';
import { goLiveHeadingId, PanelHeading } from './panel-heading';
import { useGoLive } from './queries';

/**
 * Studio > Go-live (`/studio/:id/go-live?post=<postId>`, phone-first: the staging push and Today
 * open it): the publish page, the ready-to-post kit, needs-attention resolution, and editing or
 * deleting a live post. `?post=` names the post; a standalone post is its own piece, and an
 * article without `?post=` lists the posts that can go live.
 */
export function GoLiveTab({ pieceId }: PieceTabProps) {
  const [params] = useSearchParams();
  const requested = params.get('post');
  const postId = requested ?? (pieceKind(pieceId) === 'post' ? pieceId : null);
  return (
    <div data-slot="studio-tab" data-piece-id={pieceId}>
      {postId ? <GoLivePost key={postId} postId={postId} /> : <GoLivePicker articleId={pieceId} />}
    </div>
  );
}

function GoLiveSkeleton() {
  return (
    <div className="grid gap-4">
      <p role="status" className="sr-only">
        Loading the go-live page
      </p>
      <Skeleton className="h-6 w-24" />
      <Skeleton className="h-10 w-72 max-w-full" />
      <Skeleton className="h-5 w-56 max-w-full" />
      <Skeleton className="mt-4 h-96 w-full max-w-[22.5rem]" />
    </div>
  );
}

/** One post's go-live page: the panel for where it stands. */
function GoLivePost({ postId }: { postId: string }) {
  const view = useGoLive(postId);
  const status = view.data?.post.item.status;
  // When the post's state changes on this page (the tap published it, the kit was marked
  // posted), the panel is replaced: put focus on its heading so the new state is read out.
  const shown = useRef(status);
  useEffect(() => {
    if (status === undefined) return;
    if (shown.current !== undefined && shown.current !== status) {
      document.getElementById(goLiveHeadingId(postId))?.focus();
    }
    shown.current = status;
  }, [status, postId]);

  if (view.isPending) return <GoLiveSkeleton />;
  if (view.isError) {
    return (
      <ProblemAlert
        error={view.error}
        title="Couldn’t load the go-live page."
        onRetry={() => void view.refetch()}
        retryLabel="Try loading the go-live page again"
      />
    );
  }
  const refresh = () => {
    void view.refetch();
  };
  const data = view.data;
  const post = data.post.item;
  switch (post.status) {
    case 'approved':
    case 'staged':
      return <PublishPanel view={data} onRefresh={refresh} refreshing={view.isFetching} updatedAt={view.dataUpdatedAt} />;
    case 'publishing':
      return <Publishing view={data} />;
    case 'posted':
      return <LivePanel view={data} onRefresh={refresh} />;
    case 'kit':
      return <KitPanel view={data} />;
    case 'needs_attention':
      return <AttentionPanel view={data} />;
    case 'missed':
      return <MissedPanel view={data} />;
    case 'draft':
    case 'generating':
      return <NotApproved post={post} />;
    case 'expired':
    case 'deleted':
    case 'archived':
      return <Closed post={post} status={post.status} />;
  }
}

/** An attempt is in flight (this tab or another device): wait for LinkedIn's answer. */
function Publishing({ view }: { view: GoLiveView }) {
  const post = view.post.item;
  const now = useNow();
  return (
    <div className="grid gap-6">
      <header className="grid gap-2">
        <div>
          <PostStatusBadge status={post.status} />
        </div>
        <PanelHeading postId={post.id}>Publishing to LinkedIn…</PanelHeading>
        <p role="status" className="flex items-start gap-2 text-lg text-ink-muted">
          <WorkingIcon className="mt-1.5" />
          {post.intent
            ? `The tap went out ${relativeTime(post.intent.createdAt, now)}. This page updates when LinkedIn answers.`
            : 'This page updates when LinkedIn answers.'}
        </p>
      </header>
      <Alert>
        <AlertDescription>
          <p>Don’t publish it again from LinkedIn or another device: one attempt is under way, and it is never repeated.</p>
        </AlertDescription>
      </Alert>
    </div>
  );
}

/** A draft (or one the writer is still on): nothing can go live yet. */
function NotApproved({ post }: { post: Post }) {
  return (
    <EmptyState
      title="Nothing scheduled."
      yourPart={
        <>
          Approve the post in{' '}
          <Link to={postsPath(post)} className="font-medium text-ink underline decoration-line-strong underline-offset-4">
            Posts & carousel
          </Link>
          . Then it goes live from here: one tap, or the ready-to-post kit.
        </>
      }
    >
      <p>
        {post.status === 'generating'
          ? 'The writer is still working on this post.'
          : 'This post is a draft. Every LinkedIn post needs your approval and your tap.'}
      </p>
      <p>Approval binds the exact text and media; the go-live page shows them before you publish.</p>
    </EmptyState>
  );
}

const CLOSED_COPY: Readonly<Record<'expired' | 'deleted' | 'archived', { title: string; body: string }>> = {
  expired: {
    title: 'This post expired.',
    body: 'It was time-sensitive and its moment passed before it went out. Edit it in Posts & carousel to use it again as a fresh draft.',
  },
  deleted: {
    title: 'Deleted from LinkedIn.',
    body: 'The post was taken down. The app keeps its record and numbers.',
  },
  archived: {
    title: 'This post is archived.',
    body: 'It is out of the plan and Today. Nothing about it changes on LinkedIn.',
  },
};

function Closed({ post, status }: { post: Post; status: 'expired' | 'deleted' | 'archived' }) {
  const copy = CLOSED_COPY[status];
  return (
    <div className="grid gap-4">
      <div>
        <PostStatusBadge status={post.status} />
      </div>
      <PanelHeading postId={post.id}>{copy.title}</PanelHeading>
      <div className="grid max-w-measure gap-2 text-lg text-ink-muted">
        <p>{copy.body}</p>
        {post.postedAt ? <p>{`It went live ${formatMoment(post.postedAt)}.`}</p> : null}
      </div>
      {status === 'expired' ? (
        <div>
          <Link to={postsPath(post)} className={buttonVariants({ variant: 'secondary' })}>
            <PenLine aria-hidden="true" />
            Open the post
          </Link>
        </div>
      ) : null}
    </div>
  );
}

/** States that belong on a go-live page, most urgent first. */
const GO_LIVE_ORDER: readonly PostStatus[] = ['needs_attention', 'publishing', 'staged', 'approved', 'missed', 'kit', 'posted'];

function byUrgency(a: PostSummary, b: PostSummary): number {
  const rank = GO_LIVE_ORDER.indexOf(a.status) - GO_LIVE_ORDER.indexOf(b.status);
  if (rank !== 0) return rank;
  return (a.slot?.utc ?? '').localeCompare(b.slot?.utc ?? '');
}

/** An article piece without `?post=`: its posts that can go live, as whole-row links. */
function GoLivePicker({ articleId }: { articleId: string }) {
  const posts = useArticlePosts(articleId);
  const headingId = useId();
  const now = useNow();
  if (posts.isPending) return <GoLiveSkeleton />;
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
  const live = posts.data.map((entry) => entry.item).filter((post) => GO_LIVE_ORDER.includes(post.status)).sort(byUrgency);
  if (live.length === 0) {
    return (
      <EmptyState
        title="Nothing scheduled."
        yourPart="At each slot, tap Publish, or use the ready-to-post kit to post from LinkedIn yourself."
      >
        <p>
          Every LinkedIn post needs your approval and your tap. Approve a post in{' '}
          <Link
            to={`/studio/${encodeURIComponent(articleId)}/posts`}
            className="font-medium text-ink underline decoration-line-strong underline-offset-4"
          >
            Posts & carousel
          </Link>{' '}
          and it shows here.
        </p>
      </EmptyState>
    );
  }
  return (
    <nav aria-labelledby={headingId} className="grid max-w-measure gap-4">
      <h2 id={headingId} className="font-serif text-2xl font-medium text-ink">
        Posts going live
      </h2>
      <ul className="grid gap-2">
        {live.map((post) => (
          <li key={post.id}>
            <Link
              to={{ search: `?post=${encodeURIComponent(post.id)}` }}
              replace
              className={cn(
                'flex min-h-11 items-center gap-3 rounded-lg border border-line bg-sheet px-4 py-3',
                'transition-colors duration-150 hover:bg-wash',
              )}
            >
              <span className="grid min-w-0 flex-1 gap-1.5">
                <span className="line-clamp-2 text-sm font-medium text-ink">{post.hook || 'No text yet'}</span>
                <span className="flex flex-wrap items-center gap-2 text-xs text-ink-muted">
                  <PostStatusBadge status={post.status} />
                  <span>{FORMAT_LABELS[post.format]}</span>
                  {post.slot ? (
                    <span className="inline-flex items-center gap-1">
                      <CalendarClock aria-hidden="true" className="size-3.5" />
                      {`${formatSlot(post.slot)}, ${relativeTime(post.slot.utc, now)}`}
                    </span>
                  ) : null}
                </span>
              </span>
              <ChevronRight aria-hidden="true" className="size-4 shrink-0 text-ink-subtle" />
            </Link>
          </li>
        ))}
      </ul>
    </nav>
  );
}
