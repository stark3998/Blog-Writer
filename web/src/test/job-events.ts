/**
 * Event payloads shaped exactly like the server's (`server/src/blogwriter/jobs/sse.py`
 * `snapshot_payload`, `broker.JobEventMessage.payload`, `broker.DeltaMessage.payload`).
 */

export const T0 = '2026-11-26T15:00:00.000Z';

/** A timestamp `seconds` after T0. */
export function at(seconds: number): string {
  return new Date(Date.parse(T0) + seconds * 1000).toISOString();
}

export interface StepPayload {
  name: string;
  status: string;
  runs: number;
  startedAt: string | null;
  finishedAt: string | null;
}

export function stepPayload(name: string, status = 'pending', extra: Partial<StepPayload> = {}): StepPayload {
  return { name, status, runs: status === 'pending' ? 0 : 1, startedAt: null, finishedAt: null, ...extra };
}

export interface EventPayload {
  seq: number;
  at: string;
  type: string;
  step: string | null;
  data: Record<string, unknown>;
}

export function eventPayload(
  seq: number,
  type: string,
  step: string | null = null,
  data: Record<string, unknown> = {},
): EventPayload {
  return { seq, at: at(seq), type, step, data };
}

/** The `snapshot` event body for one job. */
export function snapshotPayload(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    jobId: 'job-1',
    type: 'research.run',
    status: 'running',
    attempt: 1,
    maxAttempts: 3,
    seq: 3,
    cancelRequested: false,
    createdAt: T0,
    updatedAt: at(3),
    startedAt: at(1),
    finishedAt: null,
    scheduledFor: null,
    steps: [
      stepPayload('plan', 'succeeded', { startedAt: at(1), finishedAt: at(2) }),
      stepPayload('search', 'running', { startedAt: at(3) }),
      stepPayload('brief'),
    ],
    awaiting: null,
    error: null,
    events: [
      eventPayload(1, 'status', null, { status: 'running', attempt: 1, resumed: false }),
      eventPayload(2, 'step.succeeded', 'plan'),
      eventPayload(3, 'step.started', 'search'),
    ],
    ...overrides,
  };
}

export interface JobEventOptions {
  step?: string | null;
  /** The job's status after the event. */
  status?: string;
  data?: Record<string, unknown>;
  jobId?: string;
}

/** A live `job` event body: one durable event plus the job's status after it. */
export function jobEventPayload(
  seq: number,
  type: string,
  { step = null, status = 'running', data = {}, jobId = 'job-1' }: JobEventOptions = {},
): Record<string, unknown> {
  return { jobId, seq, at: at(seq), type, step, status, data };
}

export function deltaPayload(
  text: string,
  { step = 'search', gap = false, jobId = 'job-1' } = {},
): Record<string, unknown> {
  return { jobId, step, text, gap };
}
