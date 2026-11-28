// @vitest-environment node
import { describe, expect, it, vi } from 'vitest';

import { at, deltaPayload, eventPayload, jobEventPayload, snapshotPayload, stepPayload } from '@/test/job-events';

import { parseStreamEvent, type StreamEvent, type StreamEventType } from './events';
import { createJobStore } from './job-store';

/** Builds the event the stream would deliver for a server payload. */
function ev(type: StreamEventType, payload: unknown): StreamEvent {
  const event = parseStreamEvent(type, JSON.stringify(payload));
  if (!event) throw new Error(`fixture does not parse as ${type}`);
  return event;
}

const snapshot = (overrides: Record<string, unknown> = {}) => ev('snapshot', snapshotPayload(overrides));
const jobEvent = (...args: Parameters<typeof jobEventPayload>) => ev('job', jobEventPayload(...args));
const delta = (...args: Parameters<typeof deltaPayload>) => ev('delta', deltaPayload(...args));

function statuses(store: ReturnType<typeof createJobStore>, jobId = 'job-1') {
  return store.get(jobId)?.steps.map((step) => `${step.name}:${step.status}`);
}

describe('createJobStore', () => {
  it('takes a job’s state from its snapshot', () => {
    const store = createJobStore();
    store.apply(snapshot());
    expect(store.get('job-1')).toMatchObject({
      jobType: 'research.run',
      status: 'running',
      attempt: 1,
      maxAttempts: 3,
      seq: 3,
      awaiting: null,
      error: null,
    });
    expect(statuses(store)).toEqual(['plan:succeeded', 'search:running', 'brief:pending']);
    expect(store.get('job-1')?.events.map((event) => event.seq)).toEqual([1, 2, 3]);
    expect(store.get('other')).toBeUndefined();
  });

  it('applies each live job event once, by seq', () => {
    const store = createJobStore();
    const listener = vi.fn();
    store.subscribe('job-1', listener);
    store.apply(snapshot());
    store.apply(jobEvent(4, 'step.succeeded', { step: 'search' }));
    expect(statuses(store)).toEqual(['plan:succeeded', 'search:succeeded', 'brief:pending']);
    expect(store.get('job-1')?.steps[1]?.finishedAt).toBe(at(4));
    expect(store.get('job-1')?.seq).toBe(4);

    const before = store.get('job-1');
    store.apply(jobEvent(4, 'step.succeeded', { step: 'search' })); // a replay
    store.apply(jobEvent(2, 'step.succeeded', { step: 'plan' })); // older than the snapshot
    expect(store.get('job-1')).toBe(before);
    expect(listener).toHaveBeenCalledTimes(2);
  });

  it('follows the server’s step bookkeeping', () => {
    const store = createJobStore();
    store.apply(snapshot());
    store.apply(jobEvent(4, 'step.succeeded', { step: 'search' }));
    store.apply(jobEvent(5, 'step.started', { step: 'brief' }));
    expect(store.get('job-1')?.steps[2]).toEqual({
      name: 'brief',
      status: 'running',
      runs: 1,
      startedAt: at(5),
      finishedAt: null,
    });
    store.apply(jobEvent(6, 'step.failed', { step: 'brief', status: 'running', data: { code: 'step_error' } }));
    expect(statuses(store)).toContain('brief:failed');
    store.apply(jobEvent(7, 'step.resumed', { step: 'brief' }));
    expect(store.get('job-1')?.steps[2]).toMatchObject({ status: 'running', runs: 2, startedAt: at(5), finishedAt: null });
    // A step the snapshot did not list yet is added.
    store.apply(jobEvent(8, 'step.started', { step: 'extra' }));
    expect(statuses(store)?.at(-1)).toBe('extra:running');
    // A step's own events only join the history.
    store.apply(jobEvent(9, 'research.sources', { step: 'extra', data: { count: 12 } }));
    expect(statuses(store)?.at(-1)).toBe('extra:running');
    expect(store.get('job-1')?.events.at(-1)).toMatchObject({ seq: 9, type: 'research.sources', data: { count: 12 } });
  });

  it.each(['queued', 'cancelled', 'quarantined'])('puts unfinished steps back to pending when the job is %s', (status) => {
    const store = createJobStore();
    store.apply(snapshot());
    store.apply(jobEvent(4, 'status', { status, data: { status } }));
    expect(store.get('job-1')?.status).toBe(status);
    expect(statuses(store)).toEqual(['plan:succeeded', 'search:pending', 'brief:pending']);
  });

  it('marks the step that asks for input, and drops the request once the job moves on', () => {
    const store = createJobStore();
    const awaiting = { step: 'search', key: 'answer', prompt: 'Which sources?', requestedAt: at(4) };
    store.apply(snapshot({ status: 'waiting_input', awaiting, steps: [stepPayload('search', 'waiting_input')] }));
    expect(store.get('job-1')?.awaiting).toEqual(awaiting);
    store.apply(jobEvent(4, 'input.received', { step: 'search', status: 'waiting_input', data: { key: 'answer' } }));
    expect(store.get('job-1')?.awaiting).toEqual(awaiting);
    store.apply(jobEvent(5, 'status', { status: 'queued', data: { status: 'queued' } }));
    expect(store.get('job-1')?.awaiting).toBeNull();

    store.apply(jobEvent(6, 'input.requested', { step: 'search', status: 'running', data: { key: 'more' } }));
    expect(statuses(store)).toEqual(['search:waiting_input']);
  });

  it('keeps a failure from the snapshot until the job runs again', () => {
    const store = createJobStore();
    const error = { code: 'step_error', step: 'search', errorType: 'TimeoutError', detail: 'The source timed out.', at: at(3) };
    store.apply(snapshot({ status: 'failed', error }));
    expect(store.get('job-1')?.error).toMatchObject({ code: 'step_error', detail: 'The source timed out.' });
    store.apply(jobEvent(4, 'status', { status: 'queued', data: { status: 'queued', reason: 'retry' } }));
    expect(store.get('job-1')?.error).toBeNull();
  });

  it('appends token deltas per step without touching the steps', () => {
    const store = createJobStore();
    store.apply(snapshot());
    const steps = store.get('job-1')?.steps;
    store.apply(delta('Hello, '));
    store.apply(delta('world'));
    store.apply(delta('Other', { step: 'brief' }));
    expect(store.get('job-1')?.text).toEqual({
      search: { text: 'Hello, world', gap: false, truncated: false },
      brief: { text: 'Other', gap: false, truncated: false },
    });
    expect(store.get('job-1')?.steps).toBe(steps);
  });

  it('remembers that the server dropped some text, and caps the text', () => {
    const store = createJobStore({ maxTextLength: 8 });
    store.apply(delta('Hello'));
    store.apply(delta(', world', { gap: true }));
    store.apply(delta('!!!'));
    expect(store.get('job-1')?.text.search).toEqual({ text: 'Hello, w', gap: true, truncated: true });
  });

  it('merges a snapshot after a reconnect: keeps older history and marks live text incomplete', () => {
    const store = createJobStore();
    store.apply(snapshot());
    store.apply(delta('Partial'));
    // After Last-Event-ID job-1:3 the server sends only the newer events of that job.
    store.apply(
      snapshot({
        seq: 5,
        events: [eventPayload(4, 'step.succeeded', 'search'), eventPayload(5, 'step.started', 'brief')],
        steps: [
          stepPayload('plan', 'succeeded', { startedAt: at(1), finishedAt: at(2) }),
          stepPayload('search', 'succeeded', { startedAt: at(3), finishedAt: at(4) }),
          stepPayload('brief', 'running', { startedAt: at(5) }),
        ],
      }),
    );
    const job = store.get('job-1');
    expect(job?.events.map((event) => event.seq)).toEqual([1, 2, 3, 4, 5]);
    expect(statuses(store)).toEqual(['plan:succeeded', 'search:succeeded', 'brief:running']);
    expect(job?.seq).toBe(5);
    expect(job?.text.search).toEqual({ text: 'Partial', gap: true, truncated: false });
  });

  it('never turns a job back for a snapshot older than the events it already applied', () => {
    const store = createJobStore();
    store.apply(snapshot());
    store.apply(delta('Partial'));
    store.apply(jobEvent(4, 'step.succeeded', { step: 'search' }));
    store.apply(jobEvent(5, 'step.started', { step: 'brief' }));
    const before = store.get('job-1');

    // A lagging read on reconnect: seq 3, when the live stream already delivered 4 and 5.
    store.apply(snapshot({ events: [eventPayload(2, 'step.succeeded', 'plan'), eventPayload(3, 'step.started', 'search')] }));
    const after = store.get('job-1');
    expect(statuses(store)).toEqual(['plan:succeeded', 'search:succeeded', 'brief:running']);
    expect(after?.seq).toBe(5);
    expect(after?.steps).toBe(before?.steps);
    // It still came over a new connection, so the live text may have missed deltas.
    expect(after?.text.search).toEqual({ text: 'Partial', gap: true, truncated: false });

    // The next live event still applies.
    store.apply(jobEvent(6, 'step.succeeded', { step: 'brief' }));
    expect(statuses(store)).toEqual(['plan:succeeded', 'search:succeeded', 'brief:succeeded']);
  });

  it('keeps the history a stale snapshot adds, without other changes', () => {
    const store = createJobStore();
    store.apply(jobEvent(4, 'step.started', { step: 'search' }));
    const listener = vi.fn();
    store.subscribe('job-1', listener);
    store.apply(snapshot());
    expect(store.get('job-1')?.events.map((event) => event.seq)).toEqual([1, 2, 3, 4]);
    expect(store.get('job-1')).toMatchObject({ seq: 4, status: 'running', steps: [{ name: 'search', status: 'running' }] });
    expect(listener).toHaveBeenCalledTimes(1);

    store.apply(snapshot());
    expect(listener).toHaveBeenCalledTimes(1);
  });

  it('changes nothing, and tells no one, for an identical snapshot', () => {
    const store = createJobStore();
    const listener = vi.fn();
    store.subscribe('job-1', listener);
    store.apply(snapshot());
    const before = store.get('job-1');
    store.apply(snapshot());
    expect(store.get('job-1')).toBe(before);
    expect(listener).toHaveBeenCalledTimes(1);
  });

  it('keeps only the newest events beyond the cap', () => {
    const store = createJobStore({ maxEvents: 3 });
    store.apply(snapshot());
    store.apply(jobEvent(4, 'research.sources', { step: 'search' }));
    store.apply(jobEvent(5, 'research.sources', { step: 'search' }));
    expect(store.get('job-1')?.events.map((event) => event.seq)).toEqual([3, 4, 5]);
  });

  it('notifies only the listeners of the job that changed', () => {
    const store = createJobStore();
    const one = vi.fn();
    const two = vi.fn();
    store.subscribe('job-1', one);
    const unsubscribe = store.subscribe('job-2', two);
    store.apply(delta('Hi'));
    expect(one).toHaveBeenCalledTimes(1);
    expect(two).not.toHaveBeenCalled();
    unsubscribe();
    store.apply(delta('Hi', { jobId: 'job-2' }));
    expect(two).not.toHaveBeenCalled();
  });

  it('forgets the least recently updated job nobody watches when it holds too many', () => {
    const store = createJobStore({ maxJobs: 2 });
    store.subscribe('job-a', () => undefined);
    for (const jobId of ['job-a', 'job-b', 'job-c']) store.apply(snapshot({ jobId }));
    expect(store.get('job-a')).toBeDefined();
    expect(store.get('job-b')).toBeUndefined();
    expect(store.get('job-c')).toBeDefined();
  });

  it('stores nothing for a close or an empty delta', () => {
    const store = createJobStore();
    store.apply(ev('close', { reason: 'max_duration' }));
    store.apply(delta(''));
    expect(store.get('job-1')).toBeUndefined();
  });

  it('drops a step’s streamed text on draft.reset, so the next deltas start over', () => {
    const store = createJobStore();
    store.apply(jobEvent(1, 'step.started', { step: 'draft' }));
    store.apply(delta('A refused start', { step: 'draft' }));
    store.apply(delta('Brief notes', { step: 'brief' }));
    store.apply(jobEvent(2, 'draft.reset', { step: 'draft' }));
    expect(store.get('job-1')?.text.draft).toBeUndefined();
    expect(store.get('job-1')?.text.brief?.text).toBe('Brief notes');
    expect(store.get('job-1')?.events.at(-1)?.type).toBe('draft.reset');
    store.apply(delta('The real draft', { step: 'draft' }));
    expect(store.get('job-1')?.text.draft).toEqual({ text: 'The real draft', gap: false, truncated: false });

    // Without a step, every step's text goes; a replay changes nothing.
    store.apply(jobEvent(3, 'draft.reset'));
    expect(store.get('job-1')?.text).toEqual({});
    const before = store.get('job-1');
    store.apply(jobEvent(3, 'draft.reset'));
    expect(store.get('job-1')).toBe(before);
  });
});
