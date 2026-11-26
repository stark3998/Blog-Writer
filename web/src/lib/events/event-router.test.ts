// @vitest-environment node
import { QueryClient } from '@tanstack/react-query';
import { describe, expect, it, vi } from 'vitest';

import { queryKeys } from '@/lib/query/keys';
import { deltaPayload, jobEventPayload, snapshotPayload } from '@/test/job-events';

import { createEventRouter, invalidationsFor } from './event-router';
import { parseStreamEvent, type StreamEvent, type StreamEventType } from './events';
import { createJobStore } from './job-store';

function ev(type: StreamEventType, payload: unknown): StreamEvent {
  const event = parseStreamEvent(type, JSON.stringify(payload));
  if (!event) throw new Error(`fixture does not parse as ${type}`);
  return event;
}

const step = (seq: number, jobId = 'job-1') =>
  ev('job', jobEventPayload(seq, 'step.started', { step: `s${String(seq)}`, jobId }));
const succeeded = (seq: number) =>
  ev('job', jobEventPayload(seq, 'status', { status: 'succeeded', data: { status: 'succeeded' } }));

function setup() {
  const queryClient = new QueryClient();
  const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
  const jobs = createJobStore();
  return { route: createEventRouter(jobs, queryClient), jobs, invalidate };
}

/** Lets the router's microtask flush run. */
const flush = () => Promise.resolve();

describe('invalidationsFor', () => {
  it('maps each event to the queries it makes stale', () => {
    expect(invalidationsFor(ev('snapshot', snapshotPayload()))).toEqual([queryKeys.jobs.all]);
    expect(invalidationsFor(succeeded(4))).toEqual([queryKeys.jobs.all]);
    expect(invalidationsFor(step(4))).toEqual([['jobs', 'detail', 'job-1']]);
    expect(invalidationsFor(ev('delta', deltaPayload('x')))).toEqual([]);
    expect(invalidationsFor(ev('close', { reason: 'max_duration' }))).toEqual([]);
  });
});

describe('createEventRouter', () => {
  it('puts events in the store before invalidating, so refetches see the new state', async () => {
    const { route, jobs, invalidate } = setup();
    route(step(1));
    expect(jobs.get('job-1')?.steps.map((s) => s.name)).toEqual(['s1']);
    expect(invalidate).not.toHaveBeenCalled();
    await flush();
    expect(invalidate).toHaveBeenCalledExactlyOnceWith({ queryKey: ['jobs', 'detail', 'job-1'] });
  });

  it('never invalidates for token deltas', async () => {
    const { route, jobs, invalidate } = setup();
    route(ev('delta', deltaPayload('Hello')));
    await flush();
    expect(jobs.get('job-1')?.text.search?.text).toBe('Hello');
    expect(invalidate).not.toHaveBeenCalled();
  });

  it('invalidates each key once per burst, skipping keys a prefix already covers', async () => {
    const { route, invalidate } = setup();
    route(step(1));
    route(step(2));
    route(step(1, 'job-2'));
    await flush();
    expect(invalidate.mock.calls).toEqual([
      [{ queryKey: ['jobs', 'detail', 'job-1'] }],
      [{ queryKey: ['jobs', 'detail', 'job-2'] }],
    ]);

    invalidate.mockClear();
    route(step(3));
    route(succeeded(4));
    await flush();
    expect(invalidate.mock.calls).toEqual([[{ queryKey: ['jobs'] }]]);
  });
});
