import { CLOSED, CONNECTING, OPEN, type EventSourceLike } from '@/lib/events/event-stream';

/**
 * A controllable EventSource (jsdom has none). Tests drive it: `open()`, `emit()` events,
 * `fail()` the connection the way a browser reports it.
 */
export class MockEventSource extends EventTarget implements EventSourceLike {
  /** Every instance created since the last `reset()`, oldest first. */
  static readonly instances: MockEventSource[] = [];

  static readonly create = (url: string): MockEventSource => new MockEventSource(url);

  static latest(): MockEventSource {
    const latest = MockEventSource.instances.at(-1);
    if (!latest) throw new Error('no EventSource was created');
    return latest;
  }

  static reset(): void {
    MockEventSource.instances.length = 0;
  }

  readonly url: string;
  readyState: number = CONNECTING;
  closed = false;

  constructor(url: string) {
    super();
    this.url = url;
    MockEventSource.instances.push(this);
  }

  /** The server accepted the stream. */
  open(): void {
    this.readyState = OPEN;
    this.dispatchEvent(new Event('open'));
  }

  /** One server-sent event; objects are sent as JSON. */
  emit(type: string, data: unknown, id = ''): void {
    this.dispatchEvent(
      new MessageEvent(type, { data: typeof data === 'string' ? data : JSON.stringify(data), lastEventId: id }),
    );
  }

  /**
   * The connection failed. `CONNECTING`: the browser will retry by itself (the server closed a
   * healthy stream, or the network blipped). `CLOSED`: it gave up (an HTTP error, a wrong type).
   */
  fail(readyState: typeof CONNECTING | typeof CLOSED = CLOSED): void {
    this.readyState = readyState;
    this.dispatchEvent(new Event('error'));
  }

  close(): void {
    this.readyState = CLOSED;
    this.closed = true;
  }
}
