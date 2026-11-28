import { useApiInfiniteQuery, useApiQuery } from '@/lib/query/api-query';
import { queryKeys } from '@/lib/query/keys';

import type { ArticleStatus } from '../model/types';

/**
 * The Draft tab's reads. The article is `queryKeys.articles.detail(id)`, shared with the piece
 * header and the other Studio tabs. Its sub-resources sit under that key, so an
 * `entity.changed` for the article refreshes them too; immutable things (stored versions, the
 * state machines, rendered diagrams) sit beside it under the `articles` prefix and are never
 * refetched by events.
 */
export const draftKeys = {
  article: (id: string) => queryKeys.articles.detail(id),
  gates: (id: string) => [...queryKeys.articles.detail(id), 'gates'] as const,
  versions: (id: string) => [...queryKeys.articles.detail(id), 'versions'] as const,
  publishStatus: (id: string) => [...queryKeys.articles.detail(id), 'publish-status'] as const,
  veto: (id: string) => [...queryKeys.articles.detail(id), 'veto'] as const,
  version: (id: string, version: number) => [...queryKeys.articles.all, 'version', id, version] as const,
  diagram: (hash: string) => [...queryKeys.articles.all, 'diagram', hash] as const,
};

/** The page size for versions (lists of large documents use 20). */
const VERSIONS_PAGE = 20;

export function useGatesView(articleId: string, enabled = true) {
  return useApiQuery(
    {
      queryKey: draftKeys.gates(articleId),
      path: '/api/articles/{article_id}/gates',
      init: { params: { path: { article_id: articleId } } },
    },
    { enabled },
  );
}

export function useVersions(articleId: string, enabled: boolean) {
  return useApiInfiniteQuery(
    {
      queryKey: draftKeys.versions(articleId),
      path: '/api/articles/{article_id}/versions',
      init: { params: { path: { article_id: articleId } } },
      pageSize: VERSIONS_PAGE,
    },
    { enabled },
  );
}

/** One stored version. Versions never change, so they are cached for the session. */
export function useArticleVersion(articleId: string, version: number | null) {
  return useApiQuery(
    {
      queryKey: draftKeys.version(articleId, version ?? 0),
      path: '/api/articles/{article_id}/versions/{version}',
      init: { params: { path: { article_id: articleId, version: version ?? 0 } } },
    },
    { enabled: version !== null, staleTime: Number.POSITIVE_INFINITY },
  );
}

const PUBLISH_STATUS_STATES: readonly ArticleStatus[] = ['approved', 'publishing', 'publish_failed', 'unpublishing', 'published'];

export function usePublishStatus(articleId: string, status: ArticleStatus | undefined) {
  return useApiQuery(
    {
      queryKey: draftKeys.publishStatus(articleId),
      path: '/api/articles/{article_id}/publish-status',
      init: { params: { path: { article_id: articleId } } },
    },
    { enabled: status !== undefined && PUBLISH_STATUS_STATES.includes(status) },
  );
}

export function useVeto(articleId: string, enabled: boolean) {
  return useApiQuery(
    {
      queryKey: draftKeys.veto(articleId),
      path: '/api/articles/{article_id}/veto',
      init: { params: { path: { article_id: articleId } } },
    },
    { enabled },
  );
}

/** The research brief the article was drafted from: its sources are what citations point at. */
export function useBrief(briefId: string | null | undefined) {
  return useApiQuery(
    {
      queryKey: queryKeys.briefs.detail(briefId ?? ''),
      path: '/api/research/briefs/{brief_id}',
      init: { params: { path: { brief_id: briefId ?? '' } } },
    },
    { enabled: typeof briefId === 'string' && briefId !== '' },
  );
}
