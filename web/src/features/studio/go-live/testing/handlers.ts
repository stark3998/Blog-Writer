import { http, HttpResponse, type JsonBodyType } from 'msw';

import { apiUrl } from '@/test/msw';

import { CONFIRM_TOKEN, goLiveView, kitView, SHA256, T0 } from '../../posts/testing/fixtures';
import { postsServer, problemWith } from '../../posts/testing/handlers';
import type { ConnectionView, GoLiveView, KitView, Post } from '../../posts/model/types';

/**
 * The go-live routes over the posts test store: the view (with a confirm token while approved),
 * the one publish tap, re-slotting, the kit, resolving an attempt, and the live post's edit and
 * delete. Every request is recorded, so tests can count POSTs (exactly one per tap).
 */

export type PublishAnswer =
  | 'posted'
  | 'needs_attention'
  | { rejected: string }
  | { problem: Response }
  | 'network';

export interface GoLiveServerOptions {
  /** What the go-live view says besides the post (reasons, connection, cover, alt texts). */
  view?: (post: Post) => Partial<GoLiveView>;
  /** How the single publish attempt ends. */
  publish?: PublishAnswer;
  /** What the kit view adds (downloads, alt texts). */
  kit?: (post: Post) => Partial<KitView>;
  /** A problem for re-slotting (`slot_conflict`, …). */
  reslotProblem?: Response;
  connection?: Partial<ConnectionView>;
}

export const POST_URL = 'https://www.linkedin.com/feed/update/urn:li:share:7212345678901234567/';

export function goLiveServer(initial: Post, options: GoLiveServerOptions = {}) {
  const base = postsServer([initial]);
  const json = (value: unknown) => HttpResponse.json(value as JsonBodyType);
  const id = initial.id;
  const path = (suffix: string) => `/api/posts/${id}${suffix}`;
  const read = () => {
    const current = base.current(id);
    if (!current) throw new Error('the test post is missing');
    return current;
  };
  /** How often the page read the go-live view (a re-read is a GET, never a second POST). */
  const reads = { goLive: 0 };

  const handlers = [
    ...base.handlers,
    http.get(apiUrl('/api/linkedin/connection'), () =>
      json({ status: 'connected', scopes: [], bannerDue: false, configured: true, apiVersion: '202611', tokenDay: 12, ...options.connection }),
    ),
    http.get(apiUrl('/api/posts/:postId/go-live'), ({ params }) => {
      if (params.postId !== id) return problemWith(404, 'not_found');
      reads.goLive += 1;
      const { item, etag } = read();
      return json(goLiveView(item, etag, options.view?.(item)));
    }),
    http.post(apiUrl('/api/posts/:postId/publish'), async ({ request }) => {
      const body = (await base.record(request, path('/publish'))) as { approvalSha256: string; confirmToken: string };
      const answer = options.publish ?? 'posted';
      if (answer === 'network') return HttpResponse.error();
      if (typeof answer === 'object' && 'problem' in answer) return answer.problem;
      const refused = base.precondition(request, id);
      if (refused) return refused;
      if (body.confirmToken !== CONFIRM_TOKEN || body.approvalSha256 !== SHA256) return problemWith(409, 'confirm_invalid');
      if (answer === 'posted') {
        return json(
          base.change(id, {
            status: 'posted',
            postedAt: '2026-11-27T14:00:01.000000Z',
            postUrl: POST_URL,
            linkedinUrn: 'urn:li:share:7212345678901234567',
            finalText: read().item.displayText,
            intent: { id: 'intent-1', createdAt: T0, approvalSha256: SHA256, status: 'succeeded' },
          }),
        );
      }
      if (answer === 'needs_attention') {
        return json(
          base.change(id, {
            status: 'needs_attention',
            lastError: 'timeout',
            intent: { id: 'intent-1', createdAt: T0, approvalSha256: SHA256, status: 'unknown', errorCode: 'timeout' },
          }),
        );
      }
      return json(
        base.change(id, {
          status: 'approved',
          lastError: answer.rejected,
          intent: { id: 'intent-1', createdAt: T0, approvalSha256: SHA256, status: 'failed', errorCode: answer.rejected },
        }),
      );
    }),
    http.post(apiUrl('/api/posts/:postId/reslot'), async ({ request }) => {
      const body = (await base.record(request, path('/reslot'))) as {
        action: 'snooze' | 'next_slot' | 'set';
        slot?: { localDate: string; localTime: string };
      };
      const refused = base.precondition(request, id);
      if (refused) return refused;
      if (options.reslotProblem) return options.reslotProblem;
      const slot =
        body.action === 'snooze'
          ? { localDate: '2030-12-03', localTime: '09:30', utc: '2030-12-03T14:30:00.000000Z' }
          : body.action === 'next_slot'
            ? { localDate: '2030-12-05', localTime: '09:00', utc: '2030-12-05T14:00:00.000000Z' }
            : { localDate: body.slot?.localDate ?? '', localTime: body.slot?.localTime ?? '', utc: `${body.slot?.localDate ?? ''}T15:00:00.000000Z` };
      return json(base.change(id, { status: 'approved', slot: { ...slot, timezone: 'America/New_York', kind: 'custom' } }));
    }),
    http.post(apiUrl('/api/posts/:postId/mode'), async ({ request }) => {
      const body = (await base.record(request, path('/mode'))) as { mode: 'kit' | 'one_click' };
      const refused = base.precondition(request, id);
      if (refused) return refused;
      return json(
        body.mode === 'kit'
          ? base.change(id, { status: 'kit', goLiveMode: 'kit', kit: { state: 'prepared', preparedAt: T0 } })
          : base.change(id, { status: 'approved', goLiveMode: 'one_click', kit: null }),
      );
    }),
    http.get(apiUrl('/api/posts/:postId/kit'), () => {
      const { item } = read();
      return json(kitView(item, options.kit?.(item)));
    }),
    http.post(apiUrl('/api/posts/:postId/kit'), async ({ request }) => {
      const body = (await base.record(request, path('/kit'))) as {
        to: 'native_draft_saved' | 'natively_scheduled' | 'posted';
        scheduledAt?: string;
        postUrl?: string;
        finalText?: string;
      };
      const refused = base.precondition(request, id);
      if (refused) return refused;
      const kit = read().item.kit ?? { state: 'prepared' as const, preparedAt: T0 };
      switch (body.to) {
        case 'native_draft_saved':
          return json(base.change(id, { kit: { ...kit, state: 'native_draft_saved', draftSavedAt: T0 } }));
        case 'natively_scheduled':
          return json(base.change(id, { kit: { ...kit, state: 'natively_scheduled', scheduledAt: body.scheduledAt ?? null } }));
        case 'posted':
          return json(
            base.change(id, {
              status: 'posted',
              kit: { ...kit, state: 'posted', postedAt: T0 },
              postUrl: body.postUrl ?? null,
              finalText: body.finalText ?? null,
              postedAt: T0,
            }),
          );
      }
    }),
    http.post(apiUrl('/api/posts/:postId/resolve'), async ({ request }) => {
      const body = (await base.record(request, path('/resolve'))) as { outcome: 'posted' | 'not_posted'; postUrl?: string };
      const refused = base.precondition(request, id);
      if (refused) return refused;
      return json(
        body.outcome === 'posted'
          ? base.change(id, { status: 'posted', postUrl: body.postUrl ?? null, postedAt: T0, finalText: read().item.displayText })
          : base.change(id, { status: 'approved', lastError: null }),
      );
    }),
    http.patch(apiUrl('/api/posts/:postId/linkedin'), async ({ request }) => {
      const body = (await base.record(request, path('/linkedin'))) as { displayText: string; confirmToken: string };
      const refused = base.precondition(request, id);
      if (refused) return refused;
      if (body.displayText.includes('http')) {
        return problemWith(409, 'lint_failed', {
          findings: [{ code: 'link', severity: 'block', message: 'Remove the link: posts carry no links.', overridable: false }],
        });
      }
      return json(base.change(id, { finalText: body.displayText, payload: body.displayText }));
    }),
    http.post(apiUrl('/api/posts/:postId/linkedin/delete'), async ({ request }) => {
      await base.record(request, path('/linkedin/delete'));
      const refused = base.precondition(request, id);
      if (refused) return refused;
      return json(base.change(id, { status: 'deleted' }));
    }),
  ];

  return { ...base, handlers, path, reads };
}
