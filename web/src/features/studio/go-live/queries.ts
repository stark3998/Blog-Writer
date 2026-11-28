import { useApiQuery } from '@/lib/query/api-query';
import { LINKEDIN_CONNECTION_ID, queryKeys } from '@/lib/query/keys';

/**
 * The go-live page's reads. Both sit under the post's detail key, so an `entity.changed` for the
 * post (a job, the publish tap, the kit) refreshes them with it.
 */

export function goLiveKey(postId: string) {
  return [...queryKeys.posts.detail(postId), 'go-live'] as const;
}

export function kitKey(postId: string) {
  return [...queryKeys.posts.detail(postId), 'kit'] as const;
}

/** Refresh the confirm token a minute before it lapses, so a tap normally finds it fresh. */
const TOKEN_MARGIN_MS = 60_000;
/** While an attempt is in flight elsewhere, look again every few seconds. */
const PUBLISHING_POLL_MS = 3_000;
/** Never wait longer than this between reads of an open page (and never past a timer's range). */
const MAX_REFRESH_MS = 5 * 60_000;

/**
 * `GET /api/posts/{id}/go-live`: the exact final render's ingredients, the tap's preconditions
 * and a fresh confirm token (no side effects). Always refetched on mount and when the phone
 * comes back to the app: this page is opened from a push, often after a while.
 */
export function useGoLive(postId: string) {
  return useApiQuery(
    {
      queryKey: goLiveKey(postId),
      path: '/api/posts/{post_id}/go-live',
      init: { params: { path: { post_id: postId } } },
    },
    {
      staleTime: 0,
      refetchOnWindowFocus: true,
      refetchInterval: (query) => {
        const data = query.state.data;
        if (!data) return false;
        if (data.post.item.status === 'publishing') return PUBLISHING_POLL_MS;
        if (!data.confirmExpiresAt) return false;
        const lapses = Date.parse(data.confirmExpiresAt);
        if (Number.isNaN(lapses)) return false;
        return Math.min(MAX_REFRESH_MS, Math.max(10_000, lapses - Date.now() - TOKEN_MARGIN_MS));
      },
    },
  );
}

/** `GET /api/posts/{id}/kit`: what the owner copies and downloads, the four steps, the kit state. */
export function useKit(postId: string, { enabled = true }: { enabled?: boolean } = {}) {
  return useApiQuery(
    {
      queryKey: kitKey(postId),
      path: '/api/posts/{post_id}/kit',
      init: { params: { path: { post_id: postId } } },
    },
    { enabled, staleTime: 0 },
  );
}

/** `GET /api/linkedin/connection`: whether LinkedIn is set up and connected (never the token). */
export function useConnection({ enabled = true }: { enabled?: boolean } = {}) {
  return useApiQuery(
    { queryKey: queryKeys.linkedin.detail(LINKEDIN_CONNECTION_ID), path: '/api/linkedin/connection' },
    { enabled },
  );
}
