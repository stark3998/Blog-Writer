import type { StreamEvent } from './events';

export type StreamEventListener = (event: StreamEvent) => void;

/**
 * Every parsed stream event, after the job store and the query cache have it, for screens that
 * react to an event beyond what invalidation does (a toast when the pipeline wrote the draft).
 */
export interface EventFeed {
  readonly publish: (event: StreamEvent) => void;
  /** Returns the unsubscribe function. */
  readonly subscribe: (listener: StreamEventListener) => () => void;
}

export function createEventFeed(): EventFeed {
  const listeners = new Set<StreamEventListener>();
  return {
    publish: (event) => {
      for (const listener of [...listeners]) {
        try {
          listener(event);
        } catch (error) {
          // One broken screen must not stop the others. Logged by event and error type only:
          // an error message could quote content.
          console.error('event_listener_failed', {
            type: event.type,
            error: error instanceof Error ? error.name : typeof error,
          });
        }
      }
    },
    subscribe: (listener) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
  };
}
