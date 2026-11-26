import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from 'vitest';

import { MockEventSource } from '@/test/event-source';
import { at, deltaPayload, jobEventPayload } from '@/test/job-events';

import {
  CLOSED,
  CONNECTING,
  createEventStream,
  DEFAULT_LINGER_MS,
  EVENTS_PATH,
  type EventStreamOptions,
  type ProbeOutcome,
} from './event-stream';
import type { StreamEvent } from './events';

const STEP = jobEventPayload(1, 'step.started', { step: 'plan' });

type ProbeMock = Mock<() => Promise<ProbeOutcome>>;

function answers(outcome: ProbeOutcome): ProbeMock {
  return vi.fn(() => Promise.resolve(outcome));
}

/** A probe that answers when the test says so. */
function pendingProbe() {
  let answer: (outcome: ProbeOutcome) => void = () => undefined;
  const probe: ProbeMock = vi.fn(
    () =>
      new Promise<ProbeOutcome>((resolve) => {
        answer = resolve;
      }),
  );
  return {
    probe,
    answer: (outcome: ProbeOutcome) => {
      answer(outcome);
    },
  };
}

function setup({
  probe = answers('authenticated'),
  ...options
}: Partial<Omit<EventStreamOptions, 'probe'>> & { probe?: ProbeMock } = {}) {
  const onEvent = vi.fn<(event: StreamEvent) => void>();
  const onLoggedOut = vi.fn();
  const onForbidden = vi.fn();
  const stream = createEventStream({
    createEventSource: MockEventSource.create,
    probe,
    onEvent,
    onLoggedOut,
    onForbidden,
    // No jitter, so the delays are exact: 1 s, 2 s, 4 s ... 30 s.
    backoff: { random: () => 0.5 },
    ...options,
  });
  return { stream, probe, onEvent, onLoggedOut, onForbidden };
}

/** Lets pending promises (the probe) settle without moving the clock. */
const settle = () => vi.advanceTimersByTimeAsync(0);
const advance = (ms: number) => vi.advanceTimersByTimeAsync(ms);
const count = () => MockEventSource.instances.length;

/** Fails the newest connection the way a browser that gave up reports it, then checks the wait before the next one. */
async function expectReconnectAfter(ms: number) {
  const before = count();
  MockEventSource.latest().fail(CLOSED);
  await settle();
  await advance(ms - 1);
  expect(count(), `no reconnect before ${ms} ms`).toBe(before);
  await advance(1);
  expect(count(), `a reconnect at ${ms} ms`).toBe(before + 1);
}

beforeEach(() => {
  vi.useFakeTimers();
});
afterEach(() => {
  vi.useRealTimers();
});

describe('one app-wide stream', () => {
  it('opens one EventSource for /api/events, however many parts of the app need it', () => {
    const { stream } = setup();
    const statuses: string[] = [];
    stream.subscribeStatus(() => statuses.push(stream.getStatus()));
    stream.retain();
    stream.retain();
    expect(count()).toBe(1);
    expect(MockEventSource.latest().url).toBe(EVENTS_PATH);
    expect(EVENTS_PATH).toBe('/api/events');
    MockEventSource.latest().open();
    expect(stream.getStatus()).toBe('open');
    expect(statuses).toEqual(['connecting', 'open']);
  });

  it('routes parsed events and drops malformed ones, logging only their type', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    const { stream, onEvent } = setup();
    stream.retain();
    const source = MockEventSource.latest();
    source.open();
    source.emit('job', STEP, 'job-1:1');
    source.emit('delta', deltaPayload('Hello'));
    source.emit('job', '{"secret draft text', 'job-1:2');
    source.emit('close', { reason: 'max_duration' });
    source.emit('unknown', { anything: true });
    expect(onEvent.mock.calls).toEqual([
      [
        {
          type: 'job',
          jobId: 'job-1',
          status: 'running',
          event: { seq: 1, at: at(1), type: 'step.started', step: 'plan', data: {} },
        },
      ],
      [{ type: 'delta', jobId: 'job-1', step: 'search', text: 'Hello', gap: false }],
      [{ type: 'close', reason: 'max_duration' }],
    ]);
    expect(warn).toHaveBeenCalledExactlyOnceWith('event_stream_dropped', { type: 'job' });
  });

  it('stays open for a moment after the last user leaves, then closes', async () => {
    const { stream } = setup();
    const release = stream.retain();
    MockEventSource.latest().open();
    release();
    await advance(DEFAULT_LINGER_MS - 1);
    expect(MockEventSource.latest().closed).toBe(false);

    // Another page needs it before the linger ends: same connection.
    const releaseAgain = stream.retain();
    await advance(60_000);
    expect(count()).toBe(1);
    releaseAgain();
    releaseAgain();
    await advance(DEFAULT_LINGER_MS);
    expect(MockEventSource.latest().closed).toBe(true);
    expect(stream.getStatus()).toBe('idle');

    stream.retain();
    expect(count()).toBe(2);
  });
});

describe('after an error', () => {
  it('probes the session, then reconnects with capped exponential backoff', async () => {
    const { stream, probe } = setup();
    stream.retain();
    MockEventSource.latest().open();

    await expectReconnectAfter(1000);
    expect(probe).toHaveBeenCalledTimes(1);
    expect(stream.getStatus()).toBe('connecting');
    for (const delay of [2000, 4000, 8000, 16_000, 30_000, 30_000]) await expectReconnectAfter(delay);
    expect(probe).toHaveBeenCalledTimes(7);
  });

  it('opens a fresh /api/events when it has to replace the EventSource (the server re-syncs with snapshots)', async () => {
    const { stream } = setup();
    stream.retain();
    const source = MockEventSource.latest();
    source.open();
    source.emit('job', STEP, 'job-1:1');
    await expectReconnectAfter(1000);
    expect(MockEventSource.latest().url).toBe('/api/events');
  });

  it('lets the browser retry by itself once after a healthy stream ends (it sends Last-Event-ID)', async () => {
    const { stream, probe } = setup();
    stream.retain();
    const source = MockEventSource.latest();
    source.open();
    await advance(240_000); // the server closes streams before 240 s
    source.fail(CONNECTING);
    expect(source.closed).toBe(false);
    await settle();
    expect(probe).toHaveBeenCalledTimes(1);
    expect(stream.getStatus()).toBe('reconnecting');

    source.open(); // the browser's own retry worked
    expect(stream.getStatus()).toBe('open');
    await advance(60_000);
    expect(count()).toBe(1);
  });

  it('takes over with backoff when the browser’s own retry fails too', async () => {
    const { stream, probe } = setup();
    stream.retain();
    const source = MockEventSource.latest();
    source.open();
    await advance(20_000);
    source.fail(CONNECTING);
    await settle();
    source.fail(CONNECTING);
    expect(source.closed).toBe(true);
    await settle();
    expect(probe).toHaveBeenCalledTimes(2);
    await advance(999);
    expect(count()).toBe(1);
    await advance(1);
    expect(count()).toBe(2);
  });

  it('keeps backing off when connections open and drop at once', async () => {
    const { stream } = setup();
    stream.retain();
    MockEventSource.latest().open();
    MockEventSource.latest().fail(CONNECTING);
    await settle();
    MockEventSource.latest().open();
    MockEventSource.latest().fail(CONNECTING); // second short-lived connection: take over
    await settle();
    await advance(1000);
    expect(count()).toBe(2);
    MockEventSource.latest().open();
    MockEventSource.latest().fail(CONNECTING);
    await settle();
    await advance(1999);
    expect(count()).toBe(2);
    await advance(1);
    expect(count()).toBe(3);
  });

  it('starts the backoff again after a connection that stayed up', async () => {
    const { stream } = setup();
    stream.retain();
    await expectReconnectAfter(1000);
    await expectReconnectAfter(2000);
    MockEventSource.latest().open();
    await advance(10_000);
    await expectReconnectAfter(1000);
  });

  it('probes once for a burst of errors', async () => {
    const { probe, answer } = pendingProbe();
    const { stream } = setup({ probe });
    stream.retain();
    const source = MockEventSource.latest();
    source.fail(CONNECTING);
    source.fail(CONNECTING);
    await settle();
    expect(probe).toHaveBeenCalledTimes(1);
    answer('authenticated');
    await settle();
    await advance(1000);
    expect(count()).toBe(2);
  });

  it('reconnects when the probe cannot reach the server', async () => {
    const { stream } = setup({ probe: vi.fn(() => Promise.reject(new Error('offline'))) });
    stream.retain();
    await expectReconnectAfter(1000);
    stream.stop();
    const unavailable = setup({ probe: answers('unavailable') });
    unavailable.stream.retain();
    await expectReconnectAfter(1000);
  });

  it('reconnects at once when the device comes back online', async () => {
    const { stream } = setup();
    stream.retain();
    MockEventSource.latest().fail(CLOSED);
    await settle();
    expect(count()).toBe(1);
    window.dispatchEvent(new Event('online'));
    expect(count()).toBe(2);
    await advance(60_000);
    expect(count()).toBe(2);
  });
});

describe('when the session is gone', () => {
  it('hands over to sign-in when the probe finds no session, and never reconnects', async () => {
    const { stream, onLoggedOut } = setup({ probe: answers('logged-out') });
    stream.retain();
    const source = MockEventSource.latest();
    source.open();
    source.fail(CONNECTING); // the browser would keep retrying against the login page
    await settle();
    expect(onLoggedOut).toHaveBeenCalledTimes(1);
    expect(source.closed).toBe(true);
    expect(stream.getStatus()).toBe('signed-out');
    await advance(120_000);
    stream.retain();
    stream.stop();
    expect(count()).toBe(1);
    expect(stream.getStatus()).toBe('signed-out');
  });

  it('stops for good when the account is refused', async () => {
    const { stream, onForbidden, onLoggedOut } = setup({ probe: answers('forbidden') });
    stream.retain();
    MockEventSource.latest().fail(CLOSED);
    await settle();
    expect(onForbidden).toHaveBeenCalledTimes(1);
    expect(onLoggedOut).not.toHaveBeenCalled();
    expect(stream.getStatus()).toBe('forbidden');
    await advance(120_000);
    expect(count()).toBe(1);
  });

  it('ignores a probe that answers after the stream was stopped', async () => {
    const { probe, answer } = pendingProbe();
    const { stream, onLoggedOut } = setup({ probe });
    stream.retain();
    MockEventSource.latest().fail(CLOSED);
    await settle();
    stream.stop();
    answer('logged-out');
    await settle();
    await advance(60_000);
    expect(onLoggedOut).not.toHaveBeenCalled();
    expect(stream.getStatus()).toBe('idle');
    expect(count()).toBe(1);
  });
});

it('reports a browser without EventSource as unsupported instead of throwing', () => {
  const { stream } = setup({
    createEventSource: () => {
      throw new ReferenceError('EventSource is not defined');
    },
  });
  expect(() => stream.retain()).not.toThrow();
  expect(stream.getStatus()).toBe('unsupported');
  stream.retain();
  expect(stream.getStatus()).toBe('unsupported');
});
