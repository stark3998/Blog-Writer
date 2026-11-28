import { useCallback, useMemo } from 'react';

import { isEtagMismatch, preconditionHeaders } from '@/lib/api/envelope';
import { isProblem } from '@/lib/api/problems';
import { apiRequest } from '@/lib/api/request';
import { useAppData } from '@/lib/app-data';
import { queryKeys } from '@/lib/query/keys';

import { draftKeys } from '../api/queries';
import type { ApproveIn, ArticleStatus, StartFrom, VersionedArticle } from '../model/types';

/**
 * The article's actions (contract W-A7, W-A8, W-B1, W-B2, W-B4), each run the same way: save
 * pending edits first, send the etag the screen holds (If-Match), put the answer in the cache
 * and refresh what depends on it. Errors are thrown for the dialog that asked to show.
 */

/** Pending edits could not be saved, so an action on the saved article would miss them. */
export class UnsavedChangesError extends Error {
  override readonly name = 'UnsavedChangesError';

  constructor() {
    super('Your latest changes aren’t saved yet. Resolve the save, then try again.');
  }
}

export interface ArticleCommands {
  readonly transition: (to: ArticleStatus, reason?: string) => Promise<void>;
  readonly startPipeline: (startFrom: StartFrom) => Promise<string>;
  readonly cancelPipeline: () => Promise<void>;
  readonly revise: (instruction: string) => Promise<string>;
  readonly approve: (body: ApproveIn) => Promise<string>;
  readonly publishNow: (body: { correctionNote?: string | null; slug?: string | null }) => Promise<string>;
  readonly unpublish: () => Promise<string>;
  readonly veto: () => Promise<void>;
  readonly attestClearance: (statement: string | undefined) => Promise<void>;
  readonly restore: (version: number) => Promise<void>;
}

export function useArticleCommands(articleId: string, ensureSaved: () => Promise<boolean>): ArticleCommands {
  const { api, queryClient } = useAppData();
  const key = draftKeys.article(articleId);

  const refresh = useCallback(async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: draftKeys.gates(articleId) }),
      queryClient.invalidateQueries({ queryKey: draftKeys.versions(articleId) }),
      queryClient.invalidateQueries({ queryKey: draftKeys.publishStatus(articleId) }),
      queryClient.invalidateQueries({ queryKey: draftKeys.veto(articleId) }),
      queryClient.invalidateQueries({ queryKey: queryKeys.articles.lists() }),
      queryClient.invalidateQueries({ queryKey: queryKeys.today }),
    ]);
  }, [articleId, queryClient]);

  const run = useCallback(
    async <T>(call: (headers: Readonly<Record<string, string>>) => Promise<T>, keep: (result: T) => VersionedArticle | undefined): Promise<T> => {
      if (!(await ensureSaved())) throw new UnsavedChangesError();
      const etag = queryClient.getQueryData<VersionedArticle>(key)?.etag ?? undefined;
      try {
        const result = await call(preconditionHeaders(etag));
        const article = keep(result);
        if (article) queryClient.setQueryData(key, article);
        else await queryClient.invalidateQueries({ queryKey: key });
        void refresh();
        return result;
      } catch (error) {
        // A stale screen (412), or a publishing run that still owns the article (409
        // `publish_in_progress`): show where it really stands.
        if (isEtagMismatch(error) || isProblem(error, 'publish_in_progress')) {
          void queryClient.invalidateQueries({ queryKey: key });
          void queryClient.invalidateQueries({ queryKey: draftKeys.publishStatus(articleId) });
        }
        throw error;
      }
    },
    [articleId, ensureSaved, key, queryClient, refresh],
  );

  return useMemo<ArticleCommands>(() => {
    const path = { article_id: articleId };
    return {
      transition: async (to, reason) => {
        await run(
          (headers) => apiRequest(api, 'post', '/api/articles/{article_id}/transition', { params: { path }, body: { to, reason: reason ?? null }, headers }),
          (result) => result,
        );
      },
      startPipeline: async (startFrom) =>
        (
          await run(
            (headers) => apiRequest(api, 'post', '/api/articles/{article_id}/pipeline', { params: { path }, body: { startFrom }, headers }),
            (result) => result.article,
          )
        ).jobId,
      cancelPipeline: async () => {
        await run(
          (headers) => apiRequest(api, 'post', '/api/articles/{article_id}/pipeline/cancel', { params: { path }, headers }),
          (result) => result,
        );
      },
      revise: async (instruction) =>
        (
          await run(
            (headers) => apiRequest(api, 'post', '/api/articles/{article_id}/revise', { params: { path }, body: { instruction }, headers }),
            (result) => result.article,
          )
        ).jobId,
      approve: async (body) =>
        (
          await run(
            (headers) => apiRequest(api, 'post', '/api/articles/{article_id}/approve', { params: { path }, body, headers }),
            (result) => result.article,
          )
        ).jobId,
      publishNow: async (body) =>
        (
          await run(
            (headers) => apiRequest(api, 'post', '/api/articles/{article_id}/publish-now', { params: { path }, body, headers }),
            (result) => result.article,
          )
        ).jobId,
      unpublish: async () =>
        (
          await run(
            (headers) => apiRequest(api, 'post', '/api/articles/{article_id}/unpublish', { params: { path }, headers }),
            () => undefined,
          )
        ).jobId,
      veto: async () => {
        await run(
          (headers) => apiRequest(api, 'post', '/api/articles/{article_id}/veto', { params: { path }, headers }),
          (result) => result,
        );
      },
      attestClearance: async (statement) => {
        await run(
          (headers) =>
            apiRequest(api, 'post', '/api/articles/{article_id}/clearance', {
              params: { path },
              body: statement ? { statement } : null,
              headers,
            }),
          (result) => result,
        );
      },
      restore: async (version) => {
        await run(
          (headers) =>
            apiRequest(api, 'post', '/api/articles/{article_id}/versions/{version}/restore', {
              params: { path: { article_id: articleId, version } },
              headers,
            }),
          (result) => result,
        );
      },
    };
  }, [api, articleId, run]);
}
