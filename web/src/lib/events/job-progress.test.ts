// @vitest-environment node
import { describe, expect, it } from 'vitest';

import { jobEventPayload } from '@/test/job-events';

import { parseStreamEvent, type StreamEvent } from './events';
import { latestProgressEvent, parseProgress } from './job-progress';
import { createJobStore } from './job-store';

function jobEvent(seq: number, type: string, step: string | null, data: Record<string, unknown> = {}): StreamEvent {
  const event = parseStreamEvent('job', JSON.stringify(jobEventPayload(seq, type, { step, data })));
  if (!event) throw new Error('fixture does not parse');
  return event;
}

describe('job progress', () => {
  it('reads the newest progress event, of one step or of any', () => {
    const store = createJobStore();
    store.apply(jobEvent(1, 'step.started', 'search'));
    store.apply(jobEvent(2, 'progress', 'search', { done: 1, total: 12, label: 'sources read' }));
    store.apply(jobEvent(3, 'progress', 'search', { done: 5, total: 12, label: 'sources read' }));
    store.apply(jobEvent(4, 'progress', 'extract', { done: 2, total: 0 }));
    const job = store.get('job-1');

    const search = latestProgressEvent(job, 'search');
    expect(search?.seq).toBe(3);
    // The store's own record, so React can compare it by reference.
    expect(search).toBe(job?.events.find((event) => event.seq === 3));
    expect(search && parseProgress(search)).toEqual({
      step: 'search',
      done: 5,
      total: 12,
      label: 'sources read',
      fraction: 5 / 12,
      at: search?.at,
    });

    const latest = latestProgressEvent(job);
    expect(latest && parseProgress(latest)).toMatchObject({ step: 'extract', done: 2, total: 0, fraction: undefined, label: undefined });
    expect(latestProgressEvent(job, 'brief')).toBeUndefined();
    expect(latestProgressEvent(undefined)).toBeUndefined();
  });

  it('ignores progress without usable numbers, and caps the fraction at 1', () => {
    const store = createJobStore();
    store.apply(jobEvent(1, 'progress', 'search', { done: 'x', total: 3 }));
    store.apply(jobEvent(2, 'progress', 'search', { done: 9, total: 3 }));
    const [bad, over] = store.get('job-1')?.events ?? [];
    expect(bad && parseProgress(bad)).toBeUndefined();
    expect(over && parseProgress(over)?.fraction).toBe(1);
    const started = jobEvent(3, 'step.started', 'search');
    if (started.type !== 'job') throw new Error('expected a job event');
    expect(parseProgress(started.event)).toBeUndefined();
  });
});
