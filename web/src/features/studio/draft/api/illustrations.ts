import { useCallback, useEffect, useEffectEvent, useMemo, useRef } from 'react';

import { useJobError, useJobEvents, useJobStatus, useLiveEvents } from '@/features/jobs/use-job';
import { isEtagMismatch, preconditionHeaders } from '@/lib/api/envelope';
import { apiRequest } from '@/lib/api/request';
import { useAppData } from '@/lib/app-data';
import { parseSnapshot, type JobFailure, type JobStatus } from '@/lib/events/events';
import { useApiQuery } from '@/lib/query/api-query';
import { KB_SINGLETONS, queryKeys } from '@/lib/query/keys';

import { isNewerArticle, runOutcome, type RunOutcome } from '../model/illustrations';
import type { VersionedArticle } from '../model/types';
import { UnsavedChangesError } from '../review/use-article-commands';

import { draftKeys } from './queries';

/**
 * The AI illustrations' reads and writes (contract revision 5; `blogwriter.illustrations`):
 * whether images can be made at all (`GET /api/kb/models`, the same cache entry Settings > Models
 * uses), the article's last run (`illustrationJobId`, followed on the event stream), and the
 * owner's actions. Every action saves pending edits first and sends the etag the cache holds
 * now (If-Match); the answer becomes the cache unless a newer copy is already there.
 */

/** `GET /api/kb/models`: the image route, the owner's switch (`imagesEnabled`) and the prices. */
export function useImageSettings() {
  return useApiQuery({ queryKey: queryKeys.kb.detail(KB_SINGLETONS.models), path: '/api/kb/models' });
}

// ------------------------------------------------------------------------------------------------
// The run
// ------------------------------------------------------------------------------------------------

const ACTIVE: readonly JobStatus[] = ['queued', 'running', 'waiting_input'];

export interface IllustrationRun {
  /** The article's last `illustrations.generate` run (`illustrationJobId`). */
  readonly jobId: string | null;
  readonly status: JobStatus | undefined;
  /** Queued or running: a new run can't start until it ends. */
  readonly active: boolean;
  /** Why it stopped, when it failed. */
  readonly error: JobFailure | null;
  /** What it kept, once its last step reported it (also for a run that kept nothing). */
  readonly outcome: RunOutcome | undefined;
  /** A run this screen started (or was told about): its end reads the article again, even if it ends before the stream says it began. */
  readonly expect: (jobId: string) => void;
}

export interface RunEnded {
  readonly jobId: string;
  readonly status: JobStatus;
  readonly error: JobFailure | null;
  readonly outcome: RunOutcome | undefined;
}

/**
 * Follows the article's last illustrations run: its record (`GET /api/jobs/{id}`, put in the job
 * store like a snapshot, so a run that ended before the page opened shows how it ended), then the
 * event stream while it is active. A run this screen saw active that ends reads the article again
 * (its candidates) and calls `onEnded` once.
 */
export function useIllustrationRun(
  articleId: string,
  jobId: string | null | undefined,
  onEnded?: (ended: RunEnded) => void,
): IllustrationRun {
  const { jobs, queryClient } = useAppData();
  const id = jobId ?? null;
  const record = useApiQuery(
    {
      queryKey: queryKeys.jobs.detail(id ?? 'none'),
      path: '/api/jobs/{job_id}',
      init: { params: { path: { job_id: id ?? 'none' } } },
    },
    {
      enabled: id !== null,
      retry: false,
      // The stream refreshes it on every status change; while a run is active, ask now and then
      // too, in case the stream is down.
      refetchInterval: (query) => (query.state.data && ACTIVE.includes(query.state.data.status) ? 10_000 : false),
    },
  );
  const job = record.data;
  useEffect(() => {
    if (!job) return;
    const snapshot = parseSnapshot(job);
    if (snapshot) jobs.apply({ type: 'snapshot', job: snapshot });
  }, [jobs, job]);

  const status = useJobStatus(id ?? '');
  const error = useJobError(id ?? '');
  const events = useJobEvents(id ?? '');
  const outcome = useMemo(() => (id === null ? undefined : runOutcome(events)), [id, events]);
  const active = id !== null && status !== undefined && ACTIVE.includes(status);
  useLiveEvents(active);

  const ended = useEffectEvent((outcome: RunEnded) => {
    void queryClient.invalidateQueries({ queryKey: draftKeys.article(articleId), exact: true });
    onEnded?.(outcome);
  });
  const watched = useRef<string | null>(null);
  useEffect(() => {
    if (id === null || status === undefined) return;
    if (ACTIVE.includes(status)) {
      watched.current = id;
      return;
    }
    if (watched.current !== id) return;
    watched.current = null;
    ended({ jobId: id, status, error, outcome });
  }, [id, status, error, outcome]);

  const expect = useCallback((started: string) => {
    watched.current = started;
  }, []);

  return { jobId: id, status, active, error, outcome, expect };
}

// ------------------------------------------------------------------------------------------------
// The owner's actions
// ------------------------------------------------------------------------------------------------

/**
 * Keeps the editor still while an action rewrites the body (an insert, an alt text): no edit may
 * land between the save that went first and the new version arriving in the editor.
 */
export interface EditorHold {
  /** The action starts: the editor stops taking edits. */
  readonly begin: () => void;
  /** It ended: with the version it saved (the editor takes edits again once it shows it), or `null`. */
  readonly end: (version: number | null) => void;
}

export interface GenerateRequest {
  readonly count: number;
  readonly direction: string | null;
}

/** Each action takes the etag to send when the conflict dialog's "Keep mine" retries it. */
export interface IllustrationCommands {
  /** `POST .../images`: starts a run; resolves with its job id. */
  readonly generate: (request: GenerateRequest, etag?: string) => Promise<string>;
  /** `POST .../images/{id}/hero`. */
  readonly chooseHero: (illustrationId: string, etag?: string) => Promise<void>;
  /** `DELETE .../hero`: back to the title card. */
  readonly clearHero: (etag?: string) => Promise<void>;
  /** `POST .../images/{id}/insert`: a new version with the image after `heading`. */
  readonly insert: (illustrationId: string, heading: string, etag?: string) => Promise<void>;
  /** `PUT .../images/{id}/alt`: the candidate's alt text, and wherever it is used. */
  readonly editAlt: (illustrationId: string, altText: string, etag?: string) => Promise<void>;
  /** `DELETE .../images/{id}`: an unused candidate. */
  readonly discard: (illustrationId: string, etag?: string) => Promise<void>;
}

interface RunOptions {
  /** The action may rewrite the body. */
  readonly body?: boolean;
  /** The etag to send instead of the cached one ("Keep mine"). */
  readonly etag?: string | undefined;
}

export function useIllustrationCommands(
  articleId: string,
  ensureSaved: () => Promise<boolean>,
  hold: EditorHold,
): IllustrationCommands {
  const { api, queryClient } = useAppData();

  const keep = useCallback(
    (next: VersionedArticle) => {
      const key = draftKeys.article(articleId);
      const cached = queryClient.getQueryData<VersionedArticle>(key);
      if (cached && isNewerArticle(cached.item, next.item)) {
        // A newer copy got here first (an autosave answered after): read it again, never step back.
        void queryClient.invalidateQueries({ queryKey: key, exact: true });
        return;
      }
      queryClient.setQueryData<VersionedArticle>(key, { item: next.item, etag: next.etag });
    },
    [articleId, queryClient],
  );

  const run = useCallback(
    async <T>(
      call: (headers: Readonly<Record<string, string>>) => Promise<T>,
      articleOf: (result: T) => VersionedArticle,
      { body = false, etag }: RunOptions = {},
    ): Promise<T> => {
      const key = draftKeys.article(articleId);
      if (body) hold.begin();
      let saved: number | null = null;
      try {
        if (!(await ensureSaved())) throw new UnsavedChangesError();
        const current = etag ?? queryClient.getQueryData<VersionedArticle>(key)?.etag ?? undefined;
        const result = await call(preconditionHeaders(current));
        const article = articleOf(result);
        keep(article);
        saved = article.item.version;
        void queryClient.invalidateQueries({ queryKey: queryKeys.articles.lists() });
        if (body) {
          void queryClient.invalidateQueries({ queryKey: draftKeys.gates(articleId) });
          void queryClient.invalidateQueries({ queryKey: draftKeys.versions(articleId) });
        }
        return result;
      } catch (error) {
        // A stale screen: read where the article stands now (the dialog offers to retry).
        if (isEtagMismatch(error)) void queryClient.invalidateQueries({ queryKey: key, exact: true });
        throw error;
      } finally {
        if (body) hold.end(saved);
      }
    },
    [articleId, ensureSaved, hold, keep, queryClient],
  );

  return useMemo<IllustrationCommands>(() => {
    const article_id = articleId;
    const item = (illustration_id: string) => ({ path: { article_id, illustration_id } });
    return {
      generate: async ({ count, direction }, etag) =>
        (
          await run(
            (headers) =>
              apiRequest(api, 'post', '/api/articles/{article_id}/images', {
                params: { path: { article_id } },
                body: { count, direction },
                headers,
              }),
            (result) => result.article,
            { etag },
          )
        ).jobId,
      chooseHero: async (illustrationId, etag) => {
        await run(
          (headers) =>
            apiRequest(api, 'post', '/api/articles/{article_id}/images/{illustration_id}/hero', {
              params: item(illustrationId),
              headers,
            }),
          (result) => result,
          { etag },
        );
      },
      clearHero: async (etag) => {
        await run(
          (headers) => apiRequest(api, 'delete', '/api/articles/{article_id}/hero', { params: { path: { article_id } }, headers }),
          (result) => result,
          { etag },
        );
      },
      insert: async (illustrationId, heading, etag) => {
        await run(
          (headers) =>
            apiRequest(api, 'post', '/api/articles/{article_id}/images/{illustration_id}/insert', {
              params: item(illustrationId),
              body: { heading },
              headers,
            }),
          (result) => result,
          { body: true, etag },
        );
      },
      editAlt: async (illustrationId, altText, etag) => {
        await run(
          (headers) =>
            apiRequest(api, 'put', '/api/articles/{article_id}/images/{illustration_id}/alt', {
              params: item(illustrationId),
              body: { altText },
              headers,
            }),
          (result) => result,
          { body: true, etag },
        );
      },
      discard: async (illustrationId, etag) => {
        await run(
          (headers) =>
            apiRequest(api, 'delete', '/api/articles/{article_id}/images/{illustration_id}', {
              params: item(illustrationId),
              headers,
            }),
          (result) => result,
          { etag },
        );
      },
    };
  }, [api, articleId, run]);
}
