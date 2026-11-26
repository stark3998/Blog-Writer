// @vitest-environment node
import { describe, expect, it } from 'vitest';

import { at, deltaPayload, eventPayload, jobEventPayload, snapshotPayload, stepPayload } from '@/test/job-events';

import { isLiveJobStatus, parseStreamEvent } from './events';

const json = (value: unknown) => JSON.stringify(value);

describe('parseStreamEvent', () => {
  it('reads a snapshot as the server sends it', () => {
    const parsed = parseStreamEvent('snapshot', json(snapshotPayload()));
    expect(parsed).toEqual({
      type: 'snapshot',
      job: {
        jobId: 'job-1',
        jobType: 'research.run',
        status: 'running',
        attempt: 1,
        maxAttempts: 3,
        seq: 3,
        steps: [
          { name: 'plan', status: 'succeeded', runs: 1, startedAt: at(1), finishedAt: at(2) },
          { name: 'search', status: 'running', runs: 1, startedAt: at(3), finishedAt: null },
          { name: 'brief', status: 'pending', runs: 0, startedAt: null, finishedAt: null },
        ],
        events: [
          { seq: 1, at: at(1), type: 'status', step: null, data: { status: 'running', attempt: 1, resumed: false } },
          { seq: 2, at: at(2), type: 'step.succeeded', step: 'plan', data: {} },
          { seq: 3, at: at(3), type: 'step.started', step: 'search', data: {} },
        ],
        awaiting: null,
        error: null,
      },
    });
  });

  it('reads the pending input request and the error of a snapshot', () => {
    const awaiting = { step: 'interview', key: 'answer', prompt: 'What changed?', requestedAt: at(5) };
    const error = { code: 'step_error', step: 'search', errorType: 'TimeoutError', detail: null, at: at(6) };
    expect(parseStreamEvent('snapshot', json(snapshotPayload({ status: 'waiting_input', awaiting, error })))).toMatchObject({
      job: { status: 'waiting_input', awaiting, error: { code: 'step_error', step: 'search', detail: null, at: at(6) } },
    });
  });

  it('drops step and event entries it cannot read, but keeps the snapshot', () => {
    const parsed = parseStreamEvent(
      'snapshot',
      json(
        snapshotPayload({
          steps: [stepPayload('plan'), { name: 'x', status: 'exploded' }, 'nonsense'],
          events: [eventPayload(1, 'status', null, { status: 'running' }), { seq: 0, at: at(0), type: 'status' }],
        }),
      ),
    );
    expect(parsed?.type === 'snapshot' ? [parsed.job.steps.length, parsed.job.events.length] : undefined).toEqual([1, 1]);
  });

  it('reads a live job event with the job status after it', () => {
    const payload = jobEventPayload(4, 'step.succeeded', { step: 'search', data: { sources: 12 } });
    expect(parseStreamEvent('job', json(payload))).toEqual({
      type: 'job',
      jobId: 'job-1',
      status: 'running',
      event: { seq: 4, at: at(4), type: 'step.succeeded', step: 'search', data: { sources: 12 } },
    });
  });

  it('reads token deltas, with or without a gap flag', () => {
    expect(parseStreamEvent('delta', json(deltaPayload('Hello', { gap: true })))).toEqual({
      type: 'delta',
      jobId: 'job-1',
      step: 'search',
      text: 'Hello',
      gap: true,
    });
    expect(parseStreamEvent('delta', '{"jobId":"job-1","step":"draft","text":"x"}')).toMatchObject({ gap: false });
  });

  it('reads the server closing the stream', () => {
    expect(parseStreamEvent('close', '{"reason":"max_duration"}')).toEqual({ type: 'close', reason: 'max_duration' });
  });

  it.each([
    ['not JSON', 'job', '{nope'],
    ['not an object', 'job', '[1,2]'],
    ['a job event without a job id', 'job', json(jobEventPayload(1, 'status', { jobId: '' }))],
    ['an overlong job id', 'job', json(jobEventPayload(1, 'status', { jobId: 'x'.repeat(257) }))],
    ['an unknown job status', 'job', json(jobEventPayload(1, 'status', { status: 'paused' }))],
    ['a seq of 0', 'job', json(jobEventPayload(0, 'status'))],
    ['a fractional seq', 'job', json(jobEventPayload(1.5, 'status'))],
    ['event data that is not an object', 'job', json({ ...jobEventPayload(1, 'status'), data: [1] })],
    ['a snapshot with an unknown status', 'snapshot', json(snapshotPayload({ status: 'paused' }))],
    ['a snapshot without a seq', 'snapshot', json(snapshotPayload({ seq: undefined }))],
    ['a snapshot with a malformed input request', 'snapshot', json(snapshotPayload({ awaiting: { step: 'x' } }))],
    ['a delta without text', 'delta', '{"jobId":"job-1","step":"draft"}'],
    ['a delta without a step', 'delta', '{"jobId":"job-1","text":"x"}'],
    ['a delta with a non-boolean gap', 'delta', '{"jobId":"job-1","step":"draft","text":"x","gap":"yes"}'],
    ['a close without a reason', 'close', '{}'],
  ] as const)('drops %s', (_name, type, data) => {
    expect(parseStreamEvent(type, data)).toBeUndefined();
  });
});

describe('isLiveJobStatus', () => {
  it('is live while a job is unknown or active (queued, running, waiting for the owner)', () => {
    expect(isLiveJobStatus(undefined)).toBe(true);
    expect(isLiveJobStatus('queued')).toBe(true);
    expect(isLiveJobStatus('running')).toBe(true);
    // Answering re-queues the job; the stream is how the store hears it.
    expect(isLiveJobStatus('waiting_input')).toBe(true);
  });

  it.each(['succeeded', 'failed', 'cancelled', 'quarantined'] as const)('is not live once %s', (status) => {
    expect(isLiveJobStatus(status)).toBe(false);
  });
});
