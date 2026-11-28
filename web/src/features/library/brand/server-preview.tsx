import { RefreshCw } from 'lucide-react';
import { useState } from 'react';
import { Link } from 'react-router';

import { Button } from '@/components/ui/button';
import { useApiInfiniteQuery, useApiQuery } from '@/lib/query/api-query';
import { queryKeys } from '@/lib/query/keys';
import { carouselPreviewUrl } from '@/lib/urls';

import type { PostSummary } from '../lib/types';

/** Posts that can have slides worth previewing (a post being written has none yet). */
function previewable(post: PostSummary): boolean {
  return post.format === 'carousel' && post.status !== 'generating' && post.status !== 'deleted';
}

/**
 * Your newest carousel as the server renders it with the saved kit
 * (`GET /api/posts/{id}/carousel/preview`): the real fonts, sizes and layout. Server HTML is
 * only ever shown in a sandboxed iframe with no scripts and no same-origin access (contract
 * section 10). There is no route that renders a kit before it is saved, so this shows the
 * saved one; "Refresh" reloads it after a save.
 */
export function ServerPreview({ dirty }: { dirty: boolean }) {
  const posts = useApiInfiniteQuery({ queryKey: queryKeys.posts.list({}), path: '/api/posts', pageSize: 50 });
  const candidate = posts.data?.find((entry) => previewable(entry.item))?.item;
  const postId = candidate?.id ?? '';
  const detail = useApiQuery(
    { queryKey: queryKeys.posts.detail(postId), path: '/api/posts/{post_id}', init: { params: { path: { post_id: postId } } } },
    { enabled: candidate !== undefined },
  );
  const [reloads, setReloads] = useState(0);
  const hasSlides = detail.data?.item.carouselSpec !== undefined && detail.data.item.carouselSpec !== null;

  if (posts.isPending || (candidate && detail.isPending)) {
    return <p className="text-sm text-ink-muted">Looking for a carousel to render…</p>;
  }
  if (posts.isError || detail.isError) {
    return (
      <p className="text-sm text-ink-muted">
        The carousel to preview couldn’t be loaded.{' '}
        <Button
          variant="link"
          size="sm"
          onClick={() => {
            void (posts.isError ? posts.refetch() : detail.refetch());
          }}
        >
          Try again
        </Button>
      </p>
    );
  }
  if (!candidate || !hasSlides) {
    return (
      <p className="text-sm text-ink-muted">
        No carousel yet. Once a post has slides, it shows here as the renderer draws it with your saved kit. You can make one in{' '}
        <Link to="/studio" className="text-ink underline decoration-line-strong underline-offset-4 hover:decoration-ink">
          Studio
        </Link>
        .
      </p>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="min-w-0 text-sm text-ink-muted">
          {dirty ? 'Shows the saved kit. Save your changes, then refresh.' : 'Your newest carousel, rendered with the saved kit.'}
        </p>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => {
            setReloads((count) => count + 1);
          }}
        >
          <RefreshCw aria-hidden="true" />
          Refresh
        </Button>
      </div>
      <iframe
        key={reloads}
        title={`Your newest carousel, “${candidate.hook}”, rendered with the saved brand kit`}
        src={carouselPreviewUrl(candidate.id)}
        sandbox=""
        referrerPolicy="no-referrer"
        loading="lazy"
        className="h-[32rem] w-full rounded-md border border-line bg-sheet"
      />
    </div>
  );
}
