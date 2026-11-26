import { act, render, screen, within } from '@testing-library/react';
import { Profiler } from 'react';
import { describe, expect, it, vi } from 'vitest';

import { DataProvider } from '@/features/data/data-provider';
import { parseStreamEvent, type StreamEvent, type StreamEventType } from '@/lib/events/events';
import { createTestAppData, type TestAppData } from '@/test/app-data';
import { MockEventSource } from '@/test/event-source';
import { at, deltaPayload, jobEventPayload, snapshotPayload, stepPayload } from '@/test/job-events';

import { jobSummary, JobTimeline, stepLabel } from './job-timeline';

function ev(type: StreamEventType, payload: unknown): StreamEvent {
  const event = parseStreamEvent(type, JSON.stringify(payload));
  if (!event) throw new Error(`fixture does not parse as ${type}`);
  return event;
}

function renderTimeline(appData: TestAppData = createTestAppData(), labels?: Record<string, string>) {
  const view = render(
    <DataProvider appData={appData}>
      <JobTimeline jobId="job-1" title="Research" labels={labels} />
    </DataProvider>,
  );
  return { ...view, appData };
}

function apply(appData: TestAppData, event: StreamEvent) {
  act(() => {
    appData.jobs.apply(event);
  });
}

/** Each row's text, as a screen reader reads it. */
function rows() {
  return screen.getAllByRole('listitem').map((item) => item.querySelector('p')?.textContent);
}

describe('JobTimeline', () => {
  it('renders the steps as an ordered list inside a polite live region', () => {
    const appData = createTestAppData();
    appData.jobs.apply(ev('snapshot', snapshotPayload()));
    renderTimeline(appData);

    const list = screen.getByRole('list', { name: 'Research' });
    expect(within(list).getAllByRole('listitem')).toHaveLength(3);
    expect(rows()).toEqual(['Plan: done', 'Search: in progress', 'Brief: not started']);
    const [plan, search, brief] = within(list).getAllByRole('listitem');
    expect(plan?.querySelector('time')).toHaveAttribute('datetime', at(2));
    expect(search?.querySelector('time')).toHaveAttribute('datetime', at(3));
    expect(brief?.querySelector('time')).toBeNull();
    for (const item of [plan, search, brief]) expect(item).toHaveAttribute('aria-atomic', 'true');

    expect(list.closest('[aria-live]')).toHaveAttribute('aria-live', 'polite');
    expect(screen.getByRole('heading', { name: 'Research' })).toBeInTheDocument();
  });

  it('uses the labels a job type supplies', () => {
    const appData = createTestAppData();
    appData.jobs.apply(ev('snapshot', snapshotPayload()));
    renderTimeline(appData, { search: 'Searching sources' });
    expect(rows()).toEqual(['Plan: done', 'Searching sources: in progress', 'Brief: not started']);
  });

  it('updates as step events arrive, in the same live region', () => {
    const { appData } = renderTimeline();
    const waiting = screen.getByText('Waiting for the first step…');
    const region = waiting.closest('[aria-live="polite"]');
    expect(region).not.toBeNull();

    apply(appData, ev('job', jobEventPayload(1, 'step.started', { step: 'draft' })));
    expect(rows()).toEqual(['Draft: in progress']);
    const request = jobEventPayload(2, 'input.requested', { step: 'draft', status: 'running', data: { key: 'answer' } });
    apply(appData, ev('job', request));
    expect(rows()).toEqual(['Draft: waiting for you']);
    apply(appData, ev('job', jobEventPayload(3, 'step.failed', { step: 'draft', data: { code: 'step_error' } })));
    expect(rows()).toEqual(['Draft: failed']);
    expect(screen.getByRole('listitem')).toHaveAttribute('data-status', 'failed');
    // The list appeared inside the region that was already there, so it is announced.
    expect(screen.getByRole('list').closest('[aria-live="polite"]')).toBe(region);
  });

  it('does not re-render for streamed tokens, only for step changes', () => {
    const appData = createTestAppData();
    appData.jobs.apply(ev('snapshot', snapshotPayload()));
    // Count commits: React keeps the same DOM nodes across re-renders, so the DOM cannot tell.
    const onRender = vi.fn();
    render(
      <DataProvider appData={appData}>
        <Profiler id="timeline" onRender={onRender}>
          <JobTimeline jobId="job-1" title="Research" />
        </Profiler>
      </DataProvider>,
    );
    onRender.mockClear();

    apply(appData, ev('delta', deltaPayload('Hello')));
    apply(appData, ev('delta', deltaPayload(', world', { step: 'brief' })));
    expect(appData.jobs.get('job-1')?.text.search?.text).toBe('Hello');
    expect(onRender).not.toHaveBeenCalled();

    // A step change does re-render, so the count above would have seen one.
    apply(appData, ev('job', jobEventPayload(4, 'step.succeeded', { step: 'search' })));
    expect(onRender).toHaveBeenCalled();
    expect(rows()).toEqual(['Plan: done', 'Search: done', 'Brief: not started']);
  });

  it('keeps listening while the job waits for the owner, so the answer shows up', () => {
    const appData = createTestAppData({ streamLingerMs: 0 });
    const awaiting = { step: 'brief', key: 'answer', prompt: 'What’s your take?', requestedAt: at(4) };
    appData.jobs.apply(
      ev(
        'snapshot',
        snapshotPayload({
          status: 'waiting_input',
          seq: 4,
          awaiting,
          steps: [stepPayload('plan', 'succeeded'), stepPayload('brief', 'waiting_input')],
        }),
      ),
    );
    renderTimeline(appData);
    expect(screen.getByText('Waiting for you.')).toBeInTheDocument();
    expect(MockEventSource.instances).toHaveLength(1);
    const source = MockEventSource.latest();
    expect(source.closed).toBe(false);

    // The owner answers elsewhere in the app; the server re-queues the job and runs the step.
    act(() => {
      source.open();
      source.emit(
        'job',
        jobEventPayload(5, 'input.received', { step: 'brief', status: 'waiting_input', data: { key: 'answer' } }),
        'job-1:5',
      );
      source.emit('job', jobEventPayload(6, 'status', { status: 'queued', data: { status: 'queued' } }), 'job-1:6');
      source.emit('job', jobEventPayload(7, 'step.resumed', { step: 'brief' }), 'job-1:7');
    });
    expect(screen.queryByText('Waiting for you.')).not.toBeInTheDocument();
    expect(rows()).toEqual(['Plan: done', 'Brief: in progress']);
    expect(source.closed).toBe(false);
  });

  it.each([
    ['succeeded', {}, 'Finished.'],
    ['waiting_input', {}, 'Waiting for you.'],
    ['cancelled', {}, 'Cancelled.'],
    ['quarantined', {}, 'Stopped after repeated failures. It needs a look before it runs again.'],
    ['failed', {}, 'Stopped: the job failed.'],
    [
      'failed',
      { error: { code: 'step_error', step: 'search', errorType: 'TimeoutError', detail: 'The source timed out.', at: at(4) } },
      'Stopped: The source timed out.',
    ],
    ['running', { attempt: 2 }, 'Attempt 2 of 3.'],
  ] as const)('sums up a %s job', (status, extra, summary) => {
    const appData = createTestAppData();
    appData.jobs.apply(ev('snapshot', snapshotPayload({ status, steps: [stepPayload('plan', 'succeeded')], ...extra })));
    renderTimeline(appData);
    expect(screen.getByText(summary)).toBeInTheDocument();
  });

  it('keeps the event stream open while the job runs, and lets it go when it ends', async () => {
    const appData = createTestAppData({ streamLingerMs: 0 });
    renderTimeline(appData);
    expect(MockEventSource.instances).toHaveLength(1);
    const source = MockEventSource.latest();
    act(() => {
      source.open();
      source.emit('snapshot', snapshotPayload(), 'job-1:3');
    });
    expect(rows()).toEqual(['Plan: done', 'Search: in progress', 'Brief: not started']);

    act(() => {
      source.emit('job', jobEventPayload(4, 'status', { status: 'succeeded', data: { status: 'succeeded' } }), 'job-1:4');
    });
    expect(screen.getByText('Finished.')).toBeInTheDocument();
    await vi.waitFor(() => {
      expect(source.closed).toBe(true);
    });
  });

  it('does not open the stream for a job that has already ended', () => {
    const appData = createTestAppData();
    appData.jobs.apply(ev('snapshot', snapshotPayload({ status: 'succeeded', steps: [] })));
    renderTimeline(appData);
    expect(MockEventSource.instances).toHaveLength(0);
    expect(screen.getByText('No steps were recorded.')).toBeInTheDocument();
  });

  it('says when live updates are paused', async () => {
    const { appData } = renderTimeline();
    act(() => {
      MockEventSource.latest().fail();
    });
    expect(screen.getByText('Live updates paused. Reconnecting…')).toBeInTheDocument();
    await vi.waitFor(() => {
      expect(appData.events.getStatus()).toBe('reconnecting');
    });
  });
});

describe('stepLabel', () => {
  it('makes a step name readable', () => {
    expect(stepLabel('research.search')).toBe('Research search');
    expect(stepLabel('draft_v2-final')).toBe('Draft v2 final');
    expect(stepLabel('...')).toBe('...');
  });
});

describe('jobSummary', () => {
  it('says nothing for a job that is simply running, or unknown', () => {
    expect(jobSummary('running', { error: null, attempt: 1, maxAttempts: 3 })).toBeUndefined();
    expect(jobSummary(undefined, { error: null, attempt: 0, maxAttempts: 0 })).toBeUndefined();
  });
});
