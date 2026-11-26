/**
 * The app-wide event stream's wire format (`GET /api/events`, Server-Sent Events), as the server
 * sends it (`server/src/blogwriter/jobs/sse.py`). JSON payloads are camelCase.
 *
 * - `snapshot` (id `<jobId>:<seq>`): one per active or recently updated job when a stream opens:
 *   status, steps, the pending input request, the error, and the retained job events the client
 *   has not seen (after a reconnect with `Last-Event-ID`, only the newer ones for that job).
 * - `job` (id `<jobId>:<seq>`): one durable job event (a step started or finished, a status
 *   change, a step's own event), with the job's status after it.
 * - `delta` (no id): streamed model text for a job step. Not durable; `gap` says earlier text of
 *   that step was dropped.
 * - `close`: the server is ending this stream (`max_duration`, `overflow`, `shutdown`,
 *   `unavailable`); the browser reconnects with `Last-Event-ID` and gets a snapshot.
 * - `: heartbeat` comments every 15 s, which never reach listeners.
 *
 * Payloads carry ids, statuses, step names and small event data, never step outputs. Anything
 * that does not match is dropped (and logged by event type only).
 */

export const JOB_STATUSES = [
  'queued',
  'running',
  'waiting_input',
  'succeeded',
  'failed',
  'quarantined',
  'cancelled',
] as const;
export type JobStatus = (typeof JOB_STATUSES)[number];

export const STEP_STATUSES = ['pending', 'running', 'waiting_input', 'succeeded', 'failed'] as const;
export type StepStatus = (typeof STEP_STATUSES)[number];

export const STREAM_EVENT_TYPES = ['snapshot', 'job', 'delta', 'close'] as const;
export type StreamEventType = (typeof STREAM_EVENT_TYPES)[number];

/**
 * True while a job may still send events: not known yet, or active as the server counts it
 * (`ACTIVE_STATUSES`: queued, running, waiting_input). A job waiting for the owner is live too:
 * answering it re-queues it (`input.received`, then `status: queued`), and the event stream is the
 * only way the job store hears that. Releasing the stream while it waits would leave the job
 * "waiting for you" on screen after the owner had answered.
 */
export function isLiveJobStatus(status: JobStatus | undefined): boolean {
  return status === undefined || status === 'queued' || status === 'running' || status === 'waiting_input';
}

/** One step's durable state. */
export interface JobStepState {
  /** Machine name, for example `research.search`. */
  readonly name: string;
  readonly status: StepStatus;
  /** How many times the step function was entered (more than 1 after a retry or a resume). */
  readonly runs: number;
  readonly startedAt: string | null;
  readonly finishedAt: string | null;
}

/** One entry of the job document's capped event history. */
export interface JobEventRecord {
  /** Orders the job's events and identifies replays: the SSE id is `<jobId>:<seq>`. */
  readonly seq: number;
  readonly at: string;
  /** `status`, `step.started`, `step.resumed`, `step.succeeded`, `step.failed`, `input.requested`, … */
  readonly type: string;
  readonly step: string | null;
  /** Ids, counts and short labels only. */
  readonly data: Readonly<Record<string, unknown>>;
}

/** What a `waiting_input` job is waiting for. `prompt` is owner-facing text. */
export interface JobAwaiting {
  readonly step: string;
  readonly key: string;
  readonly prompt: string;
  readonly requestedAt: string;
}

export interface JobFailure {
  readonly code: string;
  readonly step: string | null;
  /** Owner-facing text a step chose to attach. */
  readonly detail: string | null;
  readonly at: string;
}

export interface JobSnapshot {
  readonly jobId: string;
  /** The job type, for example `article.draft`. */
  readonly jobType: string | undefined;
  readonly status: JobStatus;
  readonly attempt: number | undefined;
  readonly maxAttempts: number | undefined;
  /** The job's latest event seq. */
  readonly seq: number;
  readonly steps: readonly JobStepState[];
  readonly events: readonly JobEventRecord[];
  readonly awaiting: JobAwaiting | null;
  readonly error: JobFailure | null;
}

export type StreamEvent =
  | { readonly type: 'snapshot'; readonly job: JobSnapshot }
  | { readonly type: 'job'; readonly jobId: string; readonly status: JobStatus; readonly event: JobEventRecord }
  | {
      readonly type: 'delta';
      readonly jobId: string;
      readonly step: string;
      readonly text: string;
      readonly gap: boolean;
    }
  | { readonly type: 'close'; readonly reason: string };

type Json = Record<string, unknown>;

function isRecord(value: unknown): value is Json {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function oneOf<const T extends string>(values: readonly T[], value: unknown): T | undefined {
  return values.find((candidate) => candidate === value);
}

function text(value: unknown): string | undefined {
  return typeof value === 'string' && value !== '' ? value : undefined;
}

function textOrNull(value: unknown): string | null | undefined {
  if (value === null || value === undefined) return null;
  return typeof value === 'string' ? value : undefined;
}

function count(value: unknown, min = 0): number | undefined {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= min ? value : undefined;
}

const MAX_ID_LENGTH = 256;

function jobIdOf(body: Json): string | undefined {
  const id = text(body.jobId);
  return id !== undefined && id.length <= MAX_ID_LENGTH ? id : undefined;
}

/** Drops entries that do not parse, keeping the rest. */
function parseList<T>(value: unknown, parse: (item: unknown) => T | undefined): T[] | undefined {
  if (!Array.isArray(value)) return undefined;
  return value.map(parse).filter((item) => item !== undefined);
}

export function parseStepState(value: unknown): JobStepState | undefined {
  if (!isRecord(value)) return undefined;
  const name = text(value.name);
  const status = oneOf(STEP_STATUSES, value.status);
  const runs = count(value.runs) ?? 0;
  const startedAt = textOrNull(value.startedAt);
  const finishedAt = textOrNull(value.finishedAt);
  if (name === undefined || status === undefined || startedAt === undefined || finishedAt === undefined) {
    return undefined;
  }
  return { name, status, runs, startedAt, finishedAt };
}

export function parseEventRecord(value: unknown): JobEventRecord | undefined {
  if (!isRecord(value)) return undefined;
  const seq = count(value.seq, 1);
  const at = text(value.at);
  const type = text(value.type);
  const step = textOrNull(value.step);
  const data = value.data === undefined ? {} : value.data;
  if (seq === undefined || at === undefined || type === undefined || step === undefined || !isRecord(data)) {
    return undefined;
  }
  return { seq, at, type, step, data };
}

function parseAwaiting(value: unknown): JobAwaiting | null | undefined {
  if (value === null || value === undefined) return null;
  if (!isRecord(value)) return undefined;
  const step = text(value.step);
  const key = text(value.key);
  const prompt = typeof value.prompt === 'string' ? value.prompt : undefined;
  const requestedAt = text(value.requestedAt);
  if (step === undefined || key === undefined || prompt === undefined || requestedAt === undefined) return undefined;
  return { step, key, prompt, requestedAt };
}

function parseFailure(value: unknown): JobFailure | null | undefined {
  if (value === null || value === undefined) return null;
  if (!isRecord(value)) return undefined;
  const code = text(value.code);
  const step = textOrNull(value.step);
  const detail = textOrNull(value.detail);
  const at = text(value.at);
  if (code === undefined || step === undefined || detail === undefined || at === undefined) return undefined;
  return { code, step, detail, at };
}

export function parseSnapshot(body: Json): JobSnapshot | undefined {
  const jobId = jobIdOf(body);
  const status = oneOf(JOB_STATUSES, body.status);
  const seq = count(body.seq);
  const steps = parseList(body.steps ?? [], parseStepState);
  const events = parseList(body.events ?? [], parseEventRecord);
  const awaiting = parseAwaiting(body.awaiting);
  const error = parseFailure(body.error);
  if (
    jobId === undefined ||
    status === undefined ||
    seq === undefined ||
    steps === undefined ||
    events === undefined ||
    awaiting === undefined ||
    error === undefined
  ) {
    return undefined;
  }
  return {
    jobId,
    jobType: text(body.type),
    status,
    attempt: count(body.attempt),
    maxAttempts: count(body.maxAttempts, 1),
    seq,
    steps,
    events,
    awaiting,
    error,
  };
}

/** Parses one event's `data`. Returns `undefined` for anything malformed. */
export function parseStreamEvent(type: StreamEventType, data: string): StreamEvent | undefined {
  let body: unknown;
  try {
    body = JSON.parse(data);
  } catch {
    return undefined;
  }
  if (!isRecord(body)) return undefined;

  switch (type) {
    case 'snapshot': {
      const job = parseSnapshot(body);
      return job ? { type, job } : undefined;
    }
    case 'job': {
      const jobId = jobIdOf(body);
      const status = oneOf(JOB_STATUSES, body.status);
      const event = parseEventRecord(body);
      if (jobId === undefined || status === undefined || event === undefined) return undefined;
      return { type, jobId, status, event };
    }
    case 'delta': {
      const jobId = jobIdOf(body);
      const step = text(body.step);
      const gap = body.gap === undefined ? false : body.gap;
      if (jobId === undefined || step === undefined || typeof body.text !== 'string' || typeof gap !== 'boolean') {
        return undefined;
      }
      return { type, jobId, step, text: body.text, gap };
    }
    case 'close': {
      const reason = text(body.reason);
      return reason === undefined ? undefined : { type, reason };
    }
  }
}
