import '@testing-library/jest-dom/vitest';

import { cleanup } from '@testing-library/react';
import { afterAll, afterEach, beforeAll } from 'vitest';

import { MockEventSource } from './event-source';
import { installMatchMedia, resetMedia } from './media';
import { server } from './msw';

/** Pure-logic test files run in Node (`// @vitest-environment node`), which has no DOM. */
const hasDom = typeof window !== 'undefined';

// jsdom gaps that Radix, cmdk and the router rely on.
class ResizeObserverStub {
  observe(): void {
    /* no layout in jsdom */
  }
  unobserve(): void {
    /* no layout in jsdom */
  }
  disconnect(): void {
    /* no layout in jsdom */
  }
}

if (hasDom) {
  if (!('ResizeObserver' in globalThis)) {
    Object.defineProperty(globalThis, 'ResizeObserver', { value: ResizeObserverStub, writable: true });
  }
  for (const method of ['scrollIntoView', 'hasPointerCapture', 'setPointerCapture', 'releasePointerCapture']) {
    if (!(method in Element.prototype)) {
      Object.defineProperty(Element.prototype, method, { value: () => false, writable: true, configurable: true });
    }
  }
  // jsdom implements scrollTo as a "not implemented" error log.
  Object.defineProperty(window, 'scrollTo', { value: () => undefined, writable: true });

  installMatchMedia();
}

// No unit test reaches the network: every request is answered by msw or fails.
beforeAll(() => {
  server.listen({ onUnhandledFrame: 'error' });
});
afterAll(() => {
  server.close();
});

afterEach(() => {
  server.resetHandlers();
  MockEventSource.reset();
  if (!hasDom) return;
  cleanup();
  window.localStorage.clear();
  window.sessionStorage.clear();
  resetMedia();
  document.documentElement.className = '';
  document.documentElement.removeAttribute('style');
  document.body.removeAttribute('data-scroll-locked');
});
