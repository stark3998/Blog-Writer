import { describe, expect, it, vi } from 'vitest';

import { createRedirectGuard, REDIRECT_GUARD_KEY, REDIRECT_LIMIT, REDIRECT_WINDOW_MS } from './redirect-guard';

function clock(start = 1_000_000) {
  let now = start;
  return {
    now: () => now,
    advance: (ms: number) => {
      now += ms;
    },
  };
}

describe('createRedirectGuard', () => {
  it('allows three automatic redirects within five minutes, then reports a loop', () => {
    const time = clock();
    const guard = createRedirectGuard({ storage: window.sessionStorage, now: time.now });
    expect(REDIRECT_LIMIT).toBe(3);
    expect(REDIRECT_WINDOW_MS).toBe(300_000);
    for (let i = 0; i < 3; i += 1) {
      expect(guard.attempt()).toBe('allowed');
      time.advance(1000);
    }
    expect(guard.attempt()).toBe('loop');
    // A refused attempt is not recorded, so the count does not grow while blocked.
    expect(JSON.parse(window.sessionStorage.getItem(REDIRECT_GUARD_KEY) ?? '[]')).toHaveLength(3);
  });

  it('counts across page loads, because the count lives in sessionStorage', () => {
    const time = clock();
    for (let i = 0; i < 3; i += 1) {
      // A new guard per "page load", as after each trip through the sign-in page.
      expect(createRedirectGuard({ storage: window.sessionStorage, now: time.now }).attempt()).toBe('allowed');
    }
    expect(createRedirectGuard({ storage: window.sessionStorage, now: time.now }).attempt()).toBe('loop');
  });

  it('forgets redirects older than the window', () => {
    const time = clock();
    const guard = createRedirectGuard({ storage: window.sessionStorage, now: time.now });
    guard.attempt();
    guard.attempt();
    guard.attempt();
    time.advance(REDIRECT_WINDOW_MS);
    expect(guard.attempt()).toBe('allowed');
  });

  it('counts a manual sign-in, so a loop after it is refused at once', () => {
    const time = clock();
    const guard = createRedirectGuard({ storage: window.sessionStorage, now: time.now, limit: 2 });
    guard.attempt();
    guard.attempt();
    expect(guard.attempt()).toBe('loop');
    guard.recordManual();
    expect(guard.attempt()).toBe('loop');
  });

  it('starts again when the stored value is not a list of times', () => {
    const time = clock();
    const guard = createRedirectGuard({ storage: window.sessionStorage, now: time.now });
    window.sessionStorage.setItem(REDIRECT_GUARD_KEY, '{oops');
    expect(guard.attempt()).toBe('allowed');
    window.sessionStorage.setItem(REDIRECT_GUARD_KEY, JSON.stringify({ a: 1 }));
    expect(guard.attempt()).toBe('allowed');
    window.sessionStorage.setItem(REDIRECT_GUARD_KEY, JSON.stringify(['x', null, time.now() + 60_000]));
    expect(guard.attempt()).toBe('allowed');
  });

  it('refuses automatic redirects when storage is missing, unreadable or full', () => {
    expect(createRedirectGuard({ storage: undefined }).attempt()).toBe('unavailable');

    const unreadable = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new DOMException('denied', 'SecurityError');
    });
    expect(createRedirectGuard({ storage: window.sessionStorage }).attempt()).toBe('unavailable');
    unreadable.mockRestore();

    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('quota', 'QuotaExceededError');
    });
    const guard = createRedirectGuard({ storage: window.sessionStorage });
    expect(guard.attempt()).toBe('unavailable');
    expect(() => {
      guard.recordManual();
    }).not.toThrow();
  });
});
