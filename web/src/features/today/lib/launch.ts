import { readSharedPayload, SHARE_PARAMS, type SharedPayload } from '@/features/engagement/lib/share';

/**
 * Links that open something on Today itself (contract section 10; the planner's `links.py`):
 *
 * - `/today?share=<text>&share_url=<url>`: the Android share target (the Engage panel, to check
 *   and save other members' text);
 * - `/today?engage=<postId>`: a reply reminder (the Engage panel, replying on that post);
 * - `/today?snapshot=<postId>`: a snapshot reminder (the +48 h / +7 d numbers form).
 *
 * Today reads them once and then removes them from the URL, so a reload does not open the panel
 * again and shared text never stays in the address bar or the history.
 */

export type TodayLaunch =
  | ({ readonly kind: 'share' } & SharedPayload)
  | { readonly kind: 'engage'; readonly postId: string | null }
  | { readonly kind: 'snapshot'; readonly postId: string };

export const LAUNCH_PARAMS: readonly string[] = [SHARE_PARAMS.text, SHARE_PARAMS.url, 'engage', 'snapshot'];

/** A document id as the server makes them (`post-…`); anything else is ignored. */
const DOC_ID = /^[a-z][a-z0-9]{0,11}-[A-Za-z0-9_-]{1,64}$/;

function docId(value: string | null): string | null {
  return value !== null && DOC_ID.test(value) ? value : null;
}

/** What the URL asks Today to open, or `null`. A share wins over the reminders. */
export function readLaunch(params: URLSearchParams): TodayLaunch | null {
  const shared = readSharedPayload(params);
  if (shared) return { kind: 'share', ...shared };
  const snapshot = docId(params.get('snapshot'));
  if (snapshot) return { kind: 'snapshot', postId: snapshot };
  if (params.has('engage')) return { kind: 'engage', postId: docId(params.get('engage')) };
  return null;
}

/** True when the URL holds any launch parameter (even an unusable one: it is removed too). */
export function hasLaunchParams(params: URLSearchParams): boolean {
  return LAUNCH_PARAMS.some((name) => params.has(name));
}

/** The same parameters without the launch ones (other parameters stay). */
export function withoutLaunchParams(params: URLSearchParams): URLSearchParams {
  const next = new URLSearchParams(params);
  for (const name of LAUNCH_PARAMS) next.delete(name);
  return next;
}
