import { describe, expect, it, vi } from 'vitest';

import { registerServiceWorker, SERVICE_WORKER_URL } from './register-sw';

function fakeNavigator(register = vi.fn(() => Promise.resolve({}))) {
  return { nav: { serviceWorker: { register } } as unknown as Navigator, register };
}

function fakeWindow(readyState: DocumentReadyState) {
  const target = new EventTarget();
  return Object.assign(target, { document: { readyState } }) as unknown as Window;
}

describe('registerServiceWorker', () => {
  it('registers /sw.js at the root scope once the page has loaded', () => {
    const { nav, register } = fakeNavigator();
    const win = fakeWindow('loading');
    registerServiceWorker({ enabled: true, nav, win });
    expect(register).not.toHaveBeenCalled();
    win.dispatchEvent(new Event('load'));
    expect(register).toHaveBeenCalledWith(SERVICE_WORKER_URL, { scope: '/' });
    expect(SERVICE_WORKER_URL).toBe('/sw.js');
  });

  it('registers straight away when the page is already loaded', () => {
    const { nav, register } = fakeNavigator();
    registerServiceWorker({ enabled: true, nav, win: fakeWindow('complete') });
    expect(register).toHaveBeenCalledTimes(1);
  });

  it('does nothing in development or where service workers are missing', () => {
    const { nav, register } = fakeNavigator();
    registerServiceWorker({ enabled: false, nav, win: fakeWindow('complete') });
    expect(register).not.toHaveBeenCalled();
    expect(() => {
      registerServiceWorker({ enabled: true, nav: {} as Navigator, win: fakeWindow('complete') });
    }).not.toThrow();
  });

  it('logs a failed registration without page content and carries on', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    const { nav } = fakeNavigator(vi.fn(() => Promise.reject(new DOMException('blocked', 'SecurityError'))));
    registerServiceWorker({ enabled: true, nav, win: fakeWindow('complete') });
    await vi.waitFor(() => {
      expect(warn).toHaveBeenCalledWith('Service worker registration failed', 'SecurityError');
    });
  });
});
