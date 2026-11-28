import { act, renderHook } from '@testing-library/react';
import type { ReactNode } from 'react';
import { describe, expect, it } from 'vitest';

import { DataProvider } from '@/features/data/data-provider';
import { parseStreamEvent, type StreamEvent } from '@/lib/events/events';
import { createTestAppData } from '@/test/app-data';
import { deltaPayload, jobEventPayload } from '@/test/job-events';

import { useJobEvents, useJobProgress, useJobStepText } from './use-job';

function job(payload: unknown, type: 'job' | 'delta' = 'job'): StreamEvent {
  const event = parseStreamEvent(type, JSON.stringify(payload));
  if (!event) throw new Error('fixture does not parse');
  return event;
}

function setup() {
  const appData = createTestAppData();
  const wrapper = ({ children }: { children: ReactNode }) => <DataProvider appData={appData}>{children}</DataProvider>;
  const apply = (event: StreamEvent) => {
    act(() => {
      appData.jobs.apply(event);
    });
  };
  return { appData, wrapper, apply };
}

describe('useJobProgress', () => {
  it('follows the newest progress of a step, and keeps the same object while nothing changes', () => {
    const { wrapper, apply } = setup();
    const { result } = renderHook(() => useJobProgress('job-1', 'search'), { wrapper });
    expect(result.current).toBeUndefined();

    apply(job(jobEventPayload(1, 'progress', { step: 'search', data: { done: 2, total: 8 } })));
    expect(result.current).toMatchObject({ step: 'search', done: 2, total: 8, fraction: 0.25 });
    const first = result.current;

    apply(job(deltaPayload('streamed text', { step: 'search' }), 'delta'));
    expect(result.current).toBe(first);

    apply(job(jobEventPayload(2, 'progress', { step: 'other', data: { done: 1, total: 1 } })));
    expect(result.current).toBe(first);
    apply(job(jobEventPayload(3, 'progress', { step: 'search', data: { done: 8, total: 8 } })));
    expect(result.current).toMatchObject({ done: 8, fraction: 1 });
  });
});

describe('useJobEvents and useJobStepText', () => {
  it('read the retained history and a step’s live text, which draft.reset clears', () => {
    const { wrapper, apply } = setup();
    const { result } = renderHook(() => ({ events: useJobEvents('job-1'), text: useJobStepText('job-1', 'draft') }), {
      wrapper,
    });
    expect(result.current.events).toEqual([]);
    apply(job(jobEventPayload(1, 'step.started', { step: 'draft' })));
    apply(job(deltaPayload('First try', { step: 'draft' }), 'delta'));
    expect(result.current.text?.text).toBe('First try');
    apply(job(jobEventPayload(2, 'draft.reset', { step: 'draft' })));
    expect(result.current.text).toBeUndefined();
    expect(result.current.events.map((event) => event.type)).toEqual(['step.started', 'draft.reset']);
  });
});
