import { useQueryClient } from '@tanstack/react-query';

import { useApiMutation, useApiQuery, type ApiQueryHookOptions } from '@/lib/query/api-query';
import type { ApiData } from '@/lib/api/request';
import { KB_SINGLETONS, queryKeys } from '@/lib/query/keys';

/**
 * The piece queries every Studio tab shares. They use the contract keys
 * (`queryKeys.articles.detail(id)`, `queryKeys.posts.detail(id)`), so the header, the tabs and the
 * event stream all read and refresh one cache entry per piece.
 */

/** `GET /api/articles/{id}` as `useApiQuery` arguments (also for `apiQueryOptions` and prefetching). */
export function articleQuery(id: string) {
  return {
    queryKey: queryKeys.articles.detail(id),
    path: '/api/articles/{article_id}',
    init: { params: { path: { article_id: id } } },
  } as const;
}

/** `GET /api/posts/{id}` as `useApiQuery` arguments. */
export function postQuery(id: string) {
  return {
    queryKey: queryKeys.posts.detail(id),
    path: '/api/posts/{post_id}',
    init: { params: { path: { post_id: id } } },
  } as const;
}

type ArticleData = ApiData<'get', '/api/articles/{article_id}'>;
type PostData = ApiData<'get', '/api/posts/{post_id}'>;

/** One article and its etag (`{item, etag}`). */
export function useArticle(
  id: string,
  options?: ApiQueryHookOptions<ArticleData, ArticleData, ReturnType<typeof queryKeys.articles.detail>>,
) {
  return useApiQuery(articleQuery(id), options);
}

/** One post and its etag (`{item, etag}`). */
export function usePost(
  id: string,
  options?: ApiQueryHookOptions<PostData, PostData, ReturnType<typeof queryKeys.posts.detail>>,
) {
  return useApiQuery(postQuery(id), options);
}

/** The pillars (saved, or the plan's defaults while never saved). Shared with Library > Pillars. */
export function usePillars() {
  return useApiQuery({ queryKey: queryKeys.kb.detail(KB_SINGLETONS.pillars), path: '/api/kb/pillars' });
}

/** The article format packs. Shared with Library > Format packs. */
export function useFormatPacks() {
  return useApiQuery({ queryKey: queryKeys.kb.detail(KB_SINGLETONS.formats), path: '/api/kb/formats' });
}

/** Problem codes the pipeline start explains in place (with a link to the fix) instead of a toast. */
export const START_RESEARCH_PROBLEMS = [
  'voice_guide_missing',
  'ai_use_not_confirmed',
  'blocklist_not_configured',
  'meta_incomplete',
  'pipeline_active',
  'brief_missing',
  'etag_mismatch',
  'invalid_transition',
] as const;

/**
 * `POST /api/articles/{id}/pipeline {startFrom: "research"}`: moves the article to researching and
 * starts the pipeline (research, the interview, then the draft). The answer's article becomes the
 * cache entry, so the header shows the new status at once.
 */
export function useStartResearch(articleId: string) {
  const queryClient = useQueryClient();
  return useApiMutation('post', '/api/articles/{article_id}/pipeline', {
    successToast: 'Research started.',
    errorToast: 'Couldn’t start research.',
    handles: START_RESEARCH_PROBLEMS,
    invalidates: [queryKeys.articles.lists(), queryKeys.researchRuns.lists(), queryKeys.today],
    onSuccess: (data) => {
      queryClient.setQueryData(queryKeys.articles.detail(articleId), data.article);
    },
  });
}
