import { useQueryClient } from '@tanstack/react-query';
import { render, renderHook, screen } from '@testing-library/react';
import type { ReactNode } from 'react';
import { describe, expect, it } from 'vitest';

import { NotAuthorised } from '@/features/auth/auth-screens';
import { useAppData } from '@/lib/app-data';
import { createTestAppData } from '@/test/app-data';
import { MockEventSource } from '@/test/event-source';

import { DataProvider } from './data-provider';

describe('DataProvider', () => {
  it('provides the data layer and its query client', () => {
    const appData = createTestAppData();
    const { result } = renderHook(() => ({ data: useAppData(), client: useQueryClient() }), {
      wrapper: ({ children }: { children: ReactNode }) => <DataProvider appData={appData}>{children}</DataProvider>,
    });
    expect(result.current.data).toBe(appData);
    expect(result.current.client).toBe(appData.queryClient);
  });

  it('passes through when nested, so a route never gets a second cache, stream or gate', () => {
    const outer = createTestAppData();
    const inner = createTestAppData();
    const { result } = renderHook(() => useAppData(), {
      wrapper: ({ children }: { children: ReactNode }) => (
        <DataProvider appData={outer}>
          <DataProvider appData={inner}>{children}</DataProvider>
        </DataProvider>
      ),
    });
    expect(result.current).toBe(outer);
  });

  it('closes the event stream when it leaves the page', () => {
    const appData = createTestAppData();
    const { unmount } = render(
      <DataProvider appData={appData}>
        <p>page</p>
      </DataProvider>,
    );
    appData.events.retain();
    const source = MockEventSource.latest();
    unmount();
    expect(source.closed).toBe(true);
    expect(appData.events.getStatus()).toBe('idle');
  });

  it('fails loudly when a component needs data without a provider', () => {
    expect(() => renderHook(() => useAppData())).toThrow('useAppData() needs a <DataProvider> above it');
  });
});

describe('auth screens', () => {
  it('fill the viewport when the provider wraps the whole app', () => {
    const { container, rerender } = render(<NotAuthorised layout="fullscreen" />);
    expect(container.firstElementChild).toHaveClass('min-h-dvh', 'bg-sheet');
    rerender(<NotAuthorised />);
    expect(container.firstElementChild).not.toHaveClass('min-h-dvh');
    expect(screen.queryByText(/Request ID/)).not.toBeInTheDocument();
  });
});
