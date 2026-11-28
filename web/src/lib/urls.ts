import type { paths } from '@/lib/api/schema';

/** The server refuses longer URLs (`domain.base.MAX_URL_CHARS`). */
export const MAX_URL_CHARS = 2048;

/**
 * True for an absolute https URL the server stores (`check_https_url`): a host, no credentials,
 * no whitespace, backslashes or control characters, at most 2,048 characters.
 */
export function isHttpsUrl(value: string): boolean {
  if (value.length === 0 || value.length > MAX_URL_CHARS) return false;
  if (/[\s\\\p{Cc}]/u.test(value)) return false;
  try {
    const url = new URL(value);
    return url.protocol === 'https:' && url.hostname !== '' && url.username === '' && url.password === '';
  } catch {
    return false;
  }
}

/** `GET /api/media/{name}` as the generated schema names it (a renamed route breaks the build). */
const MEDIA_ROUTE = '/api/media/{name}' satisfies keyof paths;
/** `GET /api/posts/{post_id}/carousel/preview` (server HTML: only ever an iframe's `src`). */
const CAROUSEL_PREVIEW_ROUTE = '/api/posts/{post_id}/carousel/preview' satisfies keyof paths;

/**
 * The same-origin address of a stored media file (`GET /api/media/{name}`). Names have folders
 * (`uploads/2026-11/<sha256>.png`, `posts/<postId>/<sha256>.pdf`): the route's path parameter
 * takes them with their slashes, so each segment is encoded on its own and the slashes are kept.
 */
export function mediaUrl(name: string): string {
  return MEDIA_ROUTE.replace('{name}', name.split('/').map(encodeURIComponent).join('/'));
}

/** The sandboxed carousel preview of a post (server HTML: show it only in `<iframe sandbox src>`). */
export function carouselPreviewUrl(postId: string): string {
  return CAROUSEL_PREVIEW_ROUTE.replace('{post_id}', encodeURIComponent(postId));
}
