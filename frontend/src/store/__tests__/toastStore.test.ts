import { describe, it, expect, beforeEach, vi } from 'vitest';
import { useToastStore, toast } from '../toastStore';

describe('toastStore', () => {
  beforeEach(() => {
    // Clear all toasts before each test
    const state = useToastStore.getState();
    state.toasts.forEach((t) => state.removeToast(t.id));
    // Prevent auto-dismiss timers from interfering
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('toast.success creates a toast with type "success"', () => {
    toast.success('It worked!', 'Details here');

    const { toasts } = useToastStore.getState();
    expect(toasts).toHaveLength(1);
    expect(toasts[0].type).toBe('success');
    expect(toasts[0].title).toBe('It worked!');
    expect(toasts[0].message).toBe('Details here');
  });

  it('toast.error creates a toast with type "error"', () => {
    toast.error('Something broke', 'Error details');

    const { toasts } = useToastStore.getState();
    expect(toasts).toHaveLength(1);
    expect(toasts[0].type).toBe('error');
    expect(toasts[0].title).toBe('Something broke');
    expect(toasts[0].message).toBe('Error details');
  });

  it('toast.info creates a toast with type "info"', () => {
    toast.info('FYI');

    const { toasts } = useToastStore.getState();
    expect(toasts).toHaveLength(1);
    expect(toasts[0].type).toBe('info');
  });

  it('toast.warning creates a toast with type "warning"', () => {
    toast.warning('Watch out');

    const { toasts } = useToastStore.getState();
    expect(toasts).toHaveLength(1);
    expect(toasts[0].type).toBe('warning');
  });

  it('removeToast dismisses a toast by id', () => {
    toast.success('First');
    toast.success('Second');

    const { toasts } = useToastStore.getState();
    expect(toasts).toHaveLength(2);

    useToastStore.getState().removeToast(toasts[0].id);
    expect(useToastStore.getState().toasts).toHaveLength(1);
    expect(useToastStore.getState().toasts[0].title).toBe('Second');
  });

  it('toast auto-dismisses after its duration', () => {
    toast.success('Auto dismiss');

    expect(useToastStore.getState().toasts).toHaveLength(1);

    // Default success duration is 4000ms
    vi.advanceTimersByTime(4000);

    expect(useToastStore.getState().toasts).toHaveLength(0);
  });

  it('multiple toasts accumulate', () => {
    toast.success('One');
    toast.error('Two');
    toast.info('Three');

    expect(useToastStore.getState().toasts).toHaveLength(3);
  });
});
