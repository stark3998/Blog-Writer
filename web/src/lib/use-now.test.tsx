import { act, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { useNow } from './use-now';

function Clock() {
  return <p>{new Date(useNow()).toISOString()}</p>;
}

afterEach(() => {
  vi.useRealTimers();
});

describe('useNow', () => {
  it('ticks every 15 seconds while mounted, and on return to the page', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-11-27T10:00:00.000Z'));
    render(<Clock />);
    expect(screen.getByText('2026-11-27T10:00:00.000Z')).toBeInTheDocument();

    act(() => {
      vi.advanceTimersByTime(15_000);
    });
    expect(screen.getByText('2026-11-27T10:00:15.000Z')).toBeInTheDocument();

    // A phone that slept: no tick ran, the clock moved on.
    vi.setSystemTime(new Date('2026-11-27T11:00:00.000Z'));
    act(() => {
      document.dispatchEvent(new Event('visibilitychange'));
    });
    expect(screen.getByText('2026-11-27T11:00:00.000Z')).toBeInTheDocument();
  });

  it('gives a component that mounts later a fresh time', () => {
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date('2026-11-27T10:00:00.000Z'));
    const first = render(<Clock />);
    first.unmount();
    vi.setSystemTime(new Date('2026-11-27T10:00:05.000Z'));
    render(<Clock />);
    expect(screen.getByText('2026-11-27T10:00:05.000Z')).toBeInTheDocument();
  });
});
