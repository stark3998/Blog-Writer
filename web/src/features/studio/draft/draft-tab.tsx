import { Skeleton } from '@/components/ui/skeleton';
import { ProblemAlert } from '@/features/data/problem-alert';
import { isProblem } from '@/lib/api/problems';
import { useArticle } from '@/features/studio/piece';

import type { PieceTabProps } from '../piece/piece-tab';

import { DraftWorkspace } from './draft-workspace';

/**
 * Studio > Draft (`/studio/:id/draft`, articles only): the article editor on the dialect
 * library (`@/features/editor`), read-only with the live pipeline text while the pipeline
 * writes, autosave with If-Match plus `baseVersion` and the conflict dialog, versions, the
 * seven gates with live checks, selection AI, the portfolio preview, and the review actions
 * the state machine allows (contract sections 2, 9 and 10; W-A7, W-A8, W-B1, W-B2, W-B4).
 */
export function DraftTab({ pieceId }: PieceTabProps) {
  const article = useArticle(pieceId);

  if (article.isPending) {
    return (
      <div data-slot="studio-tab" data-piece-id={pieceId} aria-busy="true" className="grid gap-6">
        <p role="status" className="sr-only">
          Loading the draft
        </p>
        <div className="flex flex-wrap items-center gap-4">
          <Skeleton className="h-7 w-28" />
          <Skeleton className="h-5 w-40" />
          <Skeleton className="ml-auto h-10 w-40" />
        </div>
        <div className="grid gap-10 lg:grid-cols-[minmax(0,1fr)_20rem]">
          <div className="grid gap-4">
            <Skeleton className="h-12 w-full" />
            <Skeleton className="h-8 w-2/3" />
            <Skeleton className="h-4 w-full" />
            <Skeleton className="h-4 w-11/12" />
            <Skeleton className="h-4 w-4/5" />
          </div>
          <div className="grid content-start gap-3">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-24 w-full" />
          </div>
        </div>
      </div>
    );
  }

  if (article.isError) {
    return (
      <div data-slot="studio-tab" data-piece-id={pieceId} className="max-w-measure">
        <ProblemAlert
          error={article.error}
          title={isProblem(article.error, 'not_found') ? 'There’s no article here.' : 'Couldn’t load the draft.'}
          onRetry={isProblem(article.error, 'not_found') ? undefined : () => void article.refetch()}
          retryLabel="Try loading the draft again"
        />
      </div>
    );
  }

  return <DraftWorkspace key={pieceId} articleId={pieceId} data={article.data} refetch={article.refetch} />;
}
