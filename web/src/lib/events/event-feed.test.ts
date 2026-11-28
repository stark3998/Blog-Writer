// @vitest-environment node
import { describe, expect, it, vi } from 'vitest';

import { createEventFeed } from './event-feed';
import type { StreamEvent } from './events';

const close: StreamEvent = { type: 'close', reason: 'max_duration' };

describe('createEventFeed', () => {
  it('hands each event to every listener until it unsubscribes', () => {
    const feed = createEventFeed();
    const one = vi.fn();
    const two = vi.fn();
    const unsubscribe = feed.subscribe(one);
    feed.subscribe(two);
    feed.publish(close);
    unsubscribe();
    feed.publish(close);
    expect(one).toHaveBeenCalledTimes(1);
    expect(two).toHaveBeenCalledTimes(2);
  });

  it('keeps going when one listener throws, and logs only the types', () => {
    const feed = createEventFeed();
    const error = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const after = vi.fn();
    feed.subscribe(() => {
      throw new SyntaxError('quotes the "content"');
    });
    feed.subscribe(after);
    feed.publish(close);
    expect(after).toHaveBeenCalledTimes(1);
    expect(error).toHaveBeenCalledExactlyOnceWith('event_listener_failed', { type: 'close', error: 'SyntaxError' });
  });
});
