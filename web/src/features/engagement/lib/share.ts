/**
 * The Web Share Target landing (`public/manifest.webmanifest`, `share_target`): Android shares a
 * post's text or link into `/today?share=<text>&share_url=<url>`. That is other members' text in
 * the URL, so Today reads it once into component state, replaces the URL at once (it leaves the
 * address bar and the history) and saves nothing until the owner checks it and taps Save
 * (contract section 10).
 */

import { SHARE_TARGET_PARAMS } from '@/lib/api/session';
import { isHttpsUrl } from '@/lib/urls';

/**
 * The query parameters the manifest's `share_target.params` maps `text` and `url` to. They are
 * defined with the session helpers, so the sign-in redirect drops them from its way back.
 */
export const SHARE_PARAMS = SHARE_TARGET_PARAMS;

/** Other members' text is cut here, like the server does (`ItemIn.pastedText`). */
export const MAX_PASTED_CHARS = 5000;

export interface SharedPayload {
  /** The shared text, trimmed; empty when only a link came. */
  readonly text: string;
  /** An https link to the post or comment, when one came. */
  readonly url: string | null;
}

/** A link on its own, or after the text on its own line or after a space. */
const TRAILING_URL = /(?:^|\s)(https:\/\/\S+)\s*$/u;

/** Cuts text to `limit` code points (not UTF-16 units, so an emoji is never split). */
export function clipText(text: string, limit: number): string {
  const points = Array.from(text);
  return points.length <= limit ? text : points.slice(0, limit).join('');
}

/** The shared text and link from the landing URL, or `null` when nothing was shared. */
export function readSharedPayload(params: URLSearchParams): SharedPayload | null {
  if (!params.has(SHARE_PARAMS.text) && !params.has(SHARE_PARAMS.url)) return null;
  let text = (params.get(SHARE_PARAMS.text) ?? '').trim();
  let url = (params.get(SHARE_PARAMS.url) ?? '').trim();
  // Apps often put the link inside the text ("Check out this post https://…"): move it.
  const trailing = TRAILING_URL.exec(text);
  const link = trailing?.[1];
  if (link !== undefined && (url === '' || url === link)) {
    url = link;
    text = text.slice(0, text.length - (trailing?.[0].length ?? 0)).trim();
  }
  if (text === '' && url === '') return null;
  return { text: clipText(text, MAX_PASTED_CHARS), url: isHttpsUrl(url) ? url : null };
}
