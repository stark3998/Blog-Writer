import { keepPreviousData, useQueryClient } from '@tanstack/react-query';
import { useCallback, useMemo } from 'react';

import { useAppData } from '@/lib/app-data';
import { apiQueryOptions, useApiInfiniteQuery } from '@/lib/query/api-query';
import { queryKeys } from '@/lib/query/keys';

import { articleQuery, postQuery } from '../piece/piece-queries';

import {
  articleStatusesFor,
  mergePieces,
  postStatusesFor,
  STANDALONE_LANES,
  type KindFilter,
  type ListPiece,
  type ShowFilter,
} from './list-model';

/** Lists of large documents load 20 at a time (contract 1.5). */
export const STUDIO_PAGE_SIZE = 20;

export interface StudioFilters {
  readonly show: ShowFilter;
  readonly kind: KindFilter;
  readonly pillar: string | undefined;
}

/**
 * The Studio's two lists (`GET /api/articles`, `GET /api/posts` for the standalone lanes), filtered
 * on the server by status and pillar, merged into one list of pieces. Each list is an infinite
 * query on its contract key, so `entity.changed` refreshes it.
 */
export function useStudioPieces({ show, kind, pillar }: StudioFilters) {
  const articleStatus = articleStatusesFor(show);
  const postStatus = postStatusesFor(show);
  const lanes = [...STANDALONE_LANES];

  const articles = useApiInfiniteQuery(
    {
      queryKey: queryKeys.articles.list({ status: articleStatus, pillar, purpose: 'flagship' }),
      path: '/api/articles',
      init: { params: { query: { status: articleStatus, pillar, purpose: 'flagship' } } },
      pageSize: STUDIO_PAGE_SIZE,
    },
    // A filter change keeps the rows on screen until the new list arrives.
    { enabled: kind !== 'post', placeholderData: keepPreviousData },
  );
  const posts = useApiInfiniteQuery(
    {
      queryKey: queryKeys.posts.list({ lane: lanes, status: postStatus, pillar }),
      path: '/api/posts',
      init: { params: { query: { lane: lanes, status: postStatus, pillar } } },
      pageSize: STUDIO_PAGE_SIZE,
    },
    { enabled: kind !== 'article', placeholderData: keepPreviousData },
  );

  const articleItems = kind === 'post' ? undefined : articles.data;
  const postItems = kind === 'article' ? undefined : posts.data;
  const pieces: ListPiece[] = useMemo(
    () =>
      mergePieces(
        (articleItems ?? []).map((entry) => entry.item),
        (postItems ?? []).map((entry) => entry.item),
      ),
    [articleItems, postItems],
  );

  const queries = [kind === 'post' ? undefined : articles, kind === 'article' ? undefined : posts].filter(
    (query) => query !== undefined,
  );
  const loading = queries.some((query) => query.isPending && query.fetchStatus !== 'paused');
  const offline = queries.some((query) => query.isPending && query.fetchStatus === 'paused');
  const hasNextPage = queries.some((query) => query.hasNextPage);
  const fetchingNextPage = queries.some((query) => query.isFetchingNextPage);
  /** True while the rows on screen belong to the previous filters. */
  const stale = queries.some((query) => query.isPlaceholderData);

  const fetchNextPage = () => {
    if (kind !== 'post' && articles.hasNextPage) void articles.fetchNextPage();
    if (kind !== 'article' && posts.hasNextPage) void posts.fetchNextPage();
  };

  return {
    pieces,
    articles: kind === 'post' ? undefined : articles,
    posts: kind === 'article' ? undefined : posts,
    loading,
    offline,
    hasNextPage,
    fetchingNextPage,
    stale,
    fetchNextPage,
  };
}

/** Loads a piece's document when its row is pointed at or focused, so opening it is instant. */
export function usePrefetchPiece(): (piece: Pick<ListPiece, 'kind' | 'id'>) => void {
  const queryClient = useQueryClient();
  const { api } = useAppData();
  return useCallback(
    (piece) => {
      // Cached and fresh data is not fetched again; a failed prefetch is the page's to show later.
      const ignore = () => undefined;
      if (piece.kind === 'article') {
        queryClient.query(apiQueryOptions(api, articleQuery(piece.id))).catch(ignore);
      } else {
        queryClient.query(apiQueryOptions(api, postQuery(piece.id))).catch(ignore);
      }
    },
    [api, queryClient],
  );
}
