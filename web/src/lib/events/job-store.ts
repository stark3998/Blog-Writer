import {
  JOB_EVENT,
  type JobAwaiting,
  type JobEventRecord,
  type JobFailure,
  type JobSnapshot,
  type JobStatus,
  type JobStepState,
  type StreamEvent,
} from './events';

/** A step's streamed text (token deltas). */
export interface StepText {
  readonly text: string;
  /** Earlier text was dropped (by the server under load, or while reconnecting): reload the output. */
  readonly gap: boolean;
  /** Text beyond the cap was dropped. */
  readonly truncated: boolean;
}

/** What the client knows about one job from the event stream. Immutable: a change is a new object. */
export interface JobView {
  readonly jobId: string;
  readonly jobType: string | undefined;
  readonly status: JobStatus | undefined;
  readonly attempt: number | undefined;
  readonly maxAttempts: number | undefined;
  /** The highest event seq applied. Events at or below it are replays and change nothing. */
  readonly seq: number;
  readonly steps: readonly JobStepState[];
  /** The job's events in `seq` order, without duplicates, at most `maxEvents` (the newest). */
  readonly events: readonly JobEventRecord[];
  readonly awaiting: JobAwaiting | null;
  readonly error: JobFailure | null;
  /** Streamed text by step name. */
  readonly text: Readonly<Record<string, StepText>>;
}

export interface JobStore {
  readonly apply: (event: StreamEvent) => void;
  readonly get: (jobId: string) => JobView | undefined;
  /** Calls `listener` whenever that job changes. */
  readonly subscribe: (jobId: string, listener: () => void) => () => void;
}

export interface JobStoreOptions {
  /** Mirrors the server's cap on the job document's event history. */
  readonly maxEvents?: number;
  /** Per step. */
  readonly maxTextLength?: number;
  /** Jobs kept in memory; the least recently updated job nobody watches is dropped first. */
  readonly maxJobs?: number;
}

export const MAX_EVENTS = 50;
export const MAX_TEXT_LENGTH = 500_000;
export const MAX_JOBS = 50;

function emptyJob(jobId: string): JobView {
  return {
    jobId,
    jobType: undefined,
    status: undefined,
    attempt: undefined,
    maxAttempts: undefined,
    seq: 0,
    steps: [],
    events: [],
    awaiting: null,
    error: null,
    text: {},
  };
}

/** Merges events by `seq`; returns `current` itself when nothing is new (no re-render). */
function mergeEvents(
  current: readonly JobEventRecord[],
  incoming: readonly JobEventRecord[],
  max: number,
): readonly JobEventRecord[] {
  const known = new Set(current.map((event) => event.seq));
  const added = incoming.filter((event) => !known.has(event.seq));
  if (added.length === 0) return current;
  const merged = [...current, ...added].sort((a, b) => a.seq - b.seq);
  return merged.length > max ? merged.slice(-max) : merged;
}

function sameStep(a: JobStepState, b: JobStepState): boolean {
  return (
    a.name === b.name &&
    a.status === b.status &&
    a.runs === b.runs &&
    a.startedAt === b.startedAt &&
    a.finishedAt === b.finishedAt
  );
}

function sameSteps(a: readonly JobStepState[], b: readonly JobStepState[]): boolean {
  return (
    a.length === b.length &&
    a.every((step, index) => {
      const other = b[index];
      return other !== undefined && sameStep(step, other);
    })
  );
}

/** Changes one step (adding it if the job did not list it yet). */
function updateStep(
  steps: readonly JobStepState[],
  name: string | null,
  change: (step: JobStepState) => JobStepState,
): readonly JobStepState[] {
  if (name === null) return steps;
  const index = steps.findIndex((step) => step.name === name);
  if (index === -1) {
    return [...steps, change({ name, status: 'pending', runs: 0, startedAt: null, finishedAt: null })];
  }
  return steps.map((step, i) => (i === index ? change(step) : step));
}

/** A job that stops (retry later, released, cancelled, quarantined) puts unfinished steps back to pending. */
function parkSteps(steps: readonly JobStepState[]): readonly JobStepState[] {
  if (!steps.some((step) => step.status === 'running' || step.status === 'waiting_input')) return steps;
  return steps.map((step) =>
    step.status === 'running' || step.status === 'waiting_input' ? { ...step, status: 'pending' } : step,
  );
}

/** Mirrors the server's step bookkeeping for one durable event (`jobs/runner.py`). */
function applyStepEvent(steps: readonly JobStepState[], event: JobEventRecord): readonly JobStepState[] {
  switch (event.type) {
    case 'step.started':
    case 'step.resumed':
      return updateStep(steps, event.step, (step) => ({
        ...step,
        status: 'running',
        runs: step.runs + 1,
        startedAt: step.startedAt ?? event.at,
        finishedAt: null,
      }));
    case 'step.succeeded':
      return updateStep(steps, event.step, (step) => ({ ...step, status: 'succeeded', finishedAt: event.at }));
    case 'step.failed':
      return updateStep(steps, event.step, (step) => ({ ...step, status: 'failed', finishedAt: event.at }));
    case 'input.requested':
      return updateStep(steps, event.step, (step) => ({ ...step, status: 'waiting_input' }));
    case 'status': {
      const next = event.data.status;
      return next === 'queued' || next === 'cancelled' || next === 'quarantined' ? parkSteps(steps) : steps;
    }
    default:
      // A step's own events (counts, short labels) only join the history.
      return steps;
  }
}

/**
 * `draft.reset`: the model router discarded what the step streamed (a refusal, retry, repair or
 * fallback), so the text shown so far is dropped and the next deltas start over. Without a step,
 * every step's text goes.
 */
function resetText(text: Readonly<Record<string, StepText>>, step: string | null): Readonly<Record<string, StepText>> {
  if (step === null) return Object.keys(text).length === 0 ? text : {};
  if (!(step in text)) return text;
  return Object.fromEntries(Object.entries(text).filter(([name]) => name !== step));
}

/** After a reconnect, deltas sent while disconnected are lost: mark every live text incomplete. */
function markGaps(text: Readonly<Record<string, StepText>>): Readonly<Record<string, StepText>> {
  const entries = Object.entries(text);
  if (entries.every(([, value]) => value.gap)) return text;
  return Object.fromEntries(entries.map(([step, value]) => [step, value.gap ? value : { ...value, gap: true }]));
}

/**
 * A small store of job state keyed by job id, fed by the event stream: a snapshot replaces a
 * job's state (keeping history it no longer carries) unless it is older than the events already
 * applied, each durable job event applies once (by `seq`), deltas append to their step's text.
 * Components read it per job through `useSyncExternalStore`.
 */
export function createJobStore({
  maxEvents = MAX_EVENTS,
  maxTextLength = MAX_TEXT_LENGTH,
  maxJobs = MAX_JOBS,
}: JobStoreOptions = {}): JobStore {
  // Map order is update order (an update moves the job to the end), which eviction relies on.
  const jobs = new Map<string, JobView>();
  const listeners = new Map<string, Set<() => void>>();

  const evict = () => {
    for (const jobId of jobs.keys()) {
      if (jobs.size <= maxJobs) return;
      if (!listeners.has(jobId)) jobs.delete(jobId);
    }
  };

  const update = (jobId: string, change: (job: JobView) => JobView) => {
    const base = jobs.get(jobId) ?? emptyJob(jobId);
    const next = change(base);
    if (next === base) return;
    jobs.delete(jobId);
    jobs.set(jobId, next);
    evict();
    for (const listener of listeners.get(jobId) ?? []) listener();
  };

  const applySnapshot = (job: JobView, snapshot: JobSnapshot): JobView => {
    const events = mergeEvents(job.events, snapshot.events, maxEvents);
    const text = jobs.has(job.jobId) ? markGaps(job.text) : job.text;
    // A snapshot older than the events already applied (a read that lagged behind the live
    // stream) only adds history. Its status and steps would turn the job back while `seq` stayed
    // ahead, so the store would then skip the very events that correct it.
    if (snapshot.seq < job.seq) {
      return events === job.events && text === job.text ? job : { ...job, events, text };
    }
    const steps = sameSteps(job.steps, snapshot.steps) ? job.steps : snapshot.steps;
    const seq = snapshot.seq;
    const jobType = snapshot.jobType ?? job.jobType;
    if (
      steps === job.steps &&
      events === job.events &&
      text === job.text &&
      seq === job.seq &&
      jobType === job.jobType &&
      snapshot.status === job.status &&
      snapshot.attempt === job.attempt &&
      snapshot.maxAttempts === job.maxAttempts &&
      JSON.stringify(snapshot.awaiting) === JSON.stringify(job.awaiting) &&
      JSON.stringify(snapshot.error) === JSON.stringify(job.error)
    ) {
      return job;
    }
    return {
      jobId: job.jobId,
      jobType,
      status: snapshot.status,
      attempt: snapshot.attempt,
      maxAttempts: snapshot.maxAttempts,
      seq,
      steps,
      events,
      awaiting: snapshot.awaiting,
      error: snapshot.error,
      text,
    };
  };

  const applyJobEvent = (job: JobView, status: JobStatus, event: JobEventRecord): JobView => {
    if (event.seq <= job.seq) return job;
    return {
      ...job,
      status,
      seq: event.seq,
      steps: applyStepEvent(job.steps, event),
      events: mergeEvents(job.events, [event], maxEvents),
      // The request is answered (or dropped) once the job is no longer waiting.
      awaiting: status === 'waiting_input' ? job.awaiting : null,
      // Errors come from snapshots; a job that runs again is no longer failed.
      error: status === 'queued' || status === 'running' ? null : job.error,
      text: event.type === JOB_EVENT.draftReset ? resetText(job.text, event.step) : job.text,
    };
  };

  const appendDelta = (job: JobView, step: string, text: string, gap: boolean): JobView => {
    const current = job.text[step] ?? { text: '', gap: false, truncated: false };
    const nextGap = current.gap || gap;
    if ((text === '' || current.truncated) && nextGap === current.gap) return job;
    const room = Math.max(0, maxTextLength - current.text.length);
    const fits = text.length <= room;
    const next: StepText = {
      text: current.truncated ? current.text : current.text + (fits ? text : text.slice(0, room)),
      gap: nextGap,
      truncated: current.truncated || !fits,
    };
    return { ...job, text: { ...job.text, [step]: next } };
  };

  return {
    apply: (event) => {
      switch (event.type) {
        case 'snapshot':
          update(event.job.jobId, (job) => applySnapshot(job, event.job));
          return;
        case 'job':
          update(event.jobId, (job) => applyJobEvent(job, event.status, event.event));
          return;
        case 'delta':
          update(event.jobId, (job) => appendDelta(job, event.step, event.text, event.gap));
          return;
        case 'close':
          // The stream reconnects by itself; nothing to store.
          return;
      }
    },
    get: (jobId) => jobs.get(jobId),
    subscribe: (jobId, listener) => {
      let set = listeners.get(jobId);
      if (!set) {
        set = new Set();
        listeners.set(jobId, set);
      }
      set.add(listener);
      return () => {
        const current = listeners.get(jobId);
        if (!current) return;
        current.delete(listener);
        if (current.size === 0) listeners.delete(jobId);
      };
    },
  };
}
