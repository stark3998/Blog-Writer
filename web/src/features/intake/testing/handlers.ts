import { http, HttpResponse } from 'msw';

import type { components } from '@/lib/api/schema';
import { apiUrl } from '@/test/msw';

import type { CheckResult, RedactionProposal } from '../types';

import { checkResult, redactionProposal } from './fixtures';

export type RedactBody = components['schemas']['RedactIn'];

/** A problem+json response with extension members (`findings`, `errors`, `current`). */
export function problemResponse(
  status: number,
  code: string,
  extensions: Readonly<Record<string, unknown>> = {},
  requestId = 'req-test-1',
): Response {
  return HttpResponse.json(
    { type: 'about:blank', title: 'Error', status, code, request_id: requestId, ...extensions },
    { status, headers: { 'Content-Type': 'application/problem+json', 'X-Request-Id': requestId } },
  );
}

export interface IntakeHandlerOptions {
  /** The redact answer for a body (a proposal, or a `Response` such as a problem). */
  redact?: (body: RedactBody, call: number) => RedactionProposal | Response;
  /** The check answer for a text. */
  check?: (text: string, call: number) => CheckResult | Response;
}

/** What the intake routes received, in order: assert that raw text only ever went to redact. */
export interface IntakeCalls {
  readonly redact: RedactBody[];
  readonly check: string[];
}

/** msw handlers for `POST /api/stories/intake/redact` and `/check`; install with `server.use(...handlers)`. */
export function intakeHandlers(options: IntakeHandlerOptions = {}) {
  const calls: IntakeCalls = { redact: [], check: [] };
  const handlers = [
    http.post(apiUrl('/api/stories/intake/redact'), async ({ request }) => {
      const body = (await request.json()) as RedactBody;
      calls.redact.push(body);
      const answer = options.redact ? options.redact(body, calls.redact.length) : redactionProposal();
      return answer instanceof Response ? answer : HttpResponse.json(answer);
    }),
    http.post(apiUrl('/api/stories/intake/check'), async ({ request }) => {
      const { text } = (await request.json()) as components['schemas']['CheckIn'];
      calls.check.push(text);
      const answer = options.check ? options.check(text, calls.check.length) : checkResult();
      return answer instanceof Response ? answer : HttpResponse.json(answer);
    }),
  ];
  return { handlers, calls };
}
