import type { ApiClient } from '@/lib/api/client';
import { apiRequest } from '@/lib/api/request';

import type { CheckResult, IntakePurpose, RedactionProposal } from './types';

/**
 * The two intake routes, called directly (`apiRequest`), never through TanStack Query: the raw
 * text must not sit in a query key, a query's data or a mutation's cached variables (contract
 * 1.9 rule 9; `web-wave.md` section 9). The server answers both `Cache-Control: no-store`.
 */

export interface RedactRequest {
  /** The owner's raw words. The only request that ever carries them. */
  readonly text: string;
  readonly purpose: IntakePurpose;
  /** The pillar the text is about, so its public vocabulary is never flagged as a name. */
  readonly pillar?: string | null;
}

/**
 * `POST /api/stories/intake/redact`: local findings in the raw text and the anonymizer's
 * public-safe proposal. Nothing is stored or logged on the server. Rejects with an `ApiError`
 * (`blocklist_not_configured` until the owner saved the blocklist once; 422 when too long).
 */
export function redactIntake(api: ApiClient, request: RedactRequest, signal?: AbortSignal): Promise<RedactionProposal> {
  const body = {
    text: request.text,
    purpose: request.purpose,
    ...(request.pillar ? { pillar: request.pillar } : {}),
  };
  return apiRequest(api, 'post', '/api/stories/intake/redact', { body, signal });
}

/**
 * `POST /api/stories/intake/check`: local findings in a wording the owner is about to confirm (no
 * model call). `ok` is false when any finding blocks a save. Never send raw text here.
 */
export function checkIntake(api: ApiClient, text: string, signal?: AbortSignal): Promise<CheckResult> {
  return apiRequest(api, 'post', '/api/stories/intake/check', { body: { text }, signal });
}
