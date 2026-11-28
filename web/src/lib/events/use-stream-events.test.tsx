import { act, renderHook } from '@testing-library/react';
import type { ReactNode } from 'react';
import { describe, expect, it, vi } from 'vitest';

import { DataProvider } from '@/features/data/data-provider';
import { createTestAppData } from '@/test/app-data';
import { MockEventSource } from '@/test/event-source';
import { jobEventPayload } from '@/test/job-events';

import { useEntityChanged, useStreamEvents } from './use-stream-events';

function setup() {
  const appData = createTestAppData();
  const wrapper = ({ children }: { children: ReactNode }) => <DataProvider appData={appData}>{children}</DataProvider>;
  // Something on screen keeps the stream open (a JobTimeline, `useLiveEvents(true)`).
  appData.events.retain();
  const source = MockEventSource.latest();
  act(() => {
    source.open();
  });
  return { appData, wrapper, source };
}

const changed = (seq: number, entity: string, id: string) =>
  jobEventPayload(seq, 'entity.changed', { step: 'save', data: { entity, id } });

describe('useStreamEvents', () => {
  it('hears every event after the store and the cache have it, while mounted', () => {
    const { appData, wrapper, source } = setup();
    const seen: string[] = [];
    const { unmount } = renderHook(
      () => {
        useStreamEvents((event) => {
          // The job store already holds the event when the listener runs.
          seen.push(`${event.type}:${String(appData.jobs.get('job-1')?.seq ?? 0)}`);
        });
      },
      { wrapper },
    );
    act(() => {
      source.emit('job', jobEventPayload(1, 'step.started', { step: 'draft' }), 'job-1:1');
    });
    expect(seen).toEqual(['job:1']);
    unmount();
    act(() => {
      source.emit('job', jobEventPayload(2, 'step.succeeded', { step: 'draft' }), 'job-1:2');
    });
    expect(seen).toEqual(['job:1']);
  });
});

describe('useEntityChanged', () => {
  it('calls back for changes to the chosen kind (and document), with the latest callback', () => {
    const { wrapper, source } = setup();
    const first = vi.fn();
    const latest = vi.fn();
    const anyArticle = vi.fn();
    const { rerender } = renderHook(
      ({ listener }: { listener: () => void }) => {
        useEntityChanged({ entity: 'article', id: 'art-1' }, listener);
        useEntityChanged({ entity: 'article' }, anyArticle);
      },
      { wrapper, initialProps: { listener: first } },
    );
    rerender({ listener: latest });
    act(() => {
      source.emit('job', changed(1, 'article', 'art-1'), 'job-1:1');
      source.emit('job', changed(2, 'article', 'art-2'), 'job-1:2');
      source.emit('job', changed(3, 'post', 'post-1'), 'job-1:3');
    });
    expect(first).not.toHaveBeenCalled();
    expect(latest).toHaveBeenCalledExactlyOnceWith({ entity: 'article', id: 'art-1', jobId: 'job-1' });
    expect(anyArticle.mock.calls.map(([change]) => (change as { id: string }).id)).toEqual(['art-1', 'art-2']);
  });
});
