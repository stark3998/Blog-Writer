/**
 * A stand-in for the normalize worker in tests (jsdom has no Worker). It
 * delivers messages asynchronously like a real worker and can answer with
 * the real worker-side handler, answer with anything, or never answer.
 */
import type { DialectWorkerLike } from '../normalize-client';
import type { DialectWorkerRequest } from '../normalize-protocol';

type Listener = (event: Event) => void;

export class FakeWorker implements DialectWorkerLike {
  readonly received: DialectWorkerRequest[] = [];
  terminated = false;
  private readonly listeners = new Map<string, Listener[]>();
  private readonly respond: (request: DialectWorkerRequest) => unknown;

  /** `respond` returns the reply for a request, or undefined to stay silent (a hung worker). */
  constructor(respond: (request: DialectWorkerRequest) => unknown) {
    this.respond = respond;
  }

  postMessage(message: DialectWorkerRequest): void {
    if (this.terminated) {
      return;
    }
    this.received.push(message);
    setTimeout(() => {
      if (this.terminated) {
        return;
      }
      const reply = this.respond(message);
      if (reply !== undefined) {
        this.emit('message', new MessageEvent('message', { data: reply }));
      }
    }, 0);
  }

  terminate(): void {
    this.terminated = true;
  }

  addEventListener(type: 'message' | 'messageerror' | 'error', listener: Listener): void {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
  }

  /** Fire an event at the client's listeners, as the browser would. */
  emit(type: 'message' | 'messageerror' | 'error', event: Event): void {
    for (const listener of this.listeners.get(type) ?? []) {
      listener(event);
    }
  }
}
