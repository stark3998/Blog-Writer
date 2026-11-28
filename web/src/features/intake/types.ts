import type { components } from '@/lib/api/schema';

/**
 * The intake types as the server defines them (`blogwriter.stories.intake`, contract W-A3).
 * Generated from `schema.ts`, so a server change shows up here at the next `npm run gen:api`.
 */

/** One local finding: a code, `block` or `warn`, an owner-facing message and a code-point span. */
export type Finding = components['schemas']['Finding'];

/** What the text is for; the anonymizer adapts to it (`voice_sample` keeps every other word). */
export type IntakePurpose = components['schemas']['IntakePurpose'];

/** Why the anonymizer proposed nothing: no model, no AI-use confirmation, strict mode or a refusal. */
export type ProposalReason = components['schemas']['ProposalReason'];

/** `POST /api/stories/intake/redact`: findings in the raw text and the public-safe proposal. */
export type RedactionProposal = components['schemas']['RedactionProposal'];

/** `POST /api/stories/intake/check`: `ok` is false when any finding blocks a save. */
export type CheckResult = components['schemas']['CheckOut'];

/**
 * The longest raw text the redact route takes (`BW_STORIES_INTAKE_MAX_CHARS`, 8000 by default).
 * The server says so with a 422 when it is configured lower.
 */
export const INTAKE_MAX_CHARS = 8000;
