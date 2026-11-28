/**
 * Web Worker entry: parses and normalizes articles off the main thread, so a
 * hostile or pathological source can only stall this worker, which the
 * client stops after its timeout (`normalize-client.ts`).
 *
 * Start it only through the client. It imports the codec directly, never the
 * feature index, so the worker bundle does not reference itself.
 */
import { ArticleCodec } from './dialect/codec';
import {
  type DialectWorkerResponse,
  handleDialectWorkerRequest,
  isDialectWorkerRequest,
} from './normalize-protocol';

/** The part of DedicatedWorkerGlobalScope this worker uses (the app's TypeScript lib is DOM, not WebWorker). */
interface WorkerScope {
  addEventListener(type: 'message', listener: (event: MessageEvent<unknown>) => void): void;
  postMessage(message: DialectWorkerResponse): void;
}

const scope = self as unknown as WorkerScope;
let codec: ArticleCodec | null = null;

function currentCodec(): ArticleCodec {
  codec ??= new ArticleCodec();
  return codec;
}

scope.addEventListener('message', (event) => {
  const request = event.data;
  if (!isDialectWorkerRequest(request)) {
    // Not a request from the client: nothing to answer.
    return;
  }

  const { response, resetCodec } = handleDialectWorkerRequest(currentCodec, request);
  scope.postMessage(response);

  if (resetCodec) {
    // Crash-only: after a stack overflow or an unexpected error, the next request gets a fresh codec.
    const broken = codec;
    codec = null;
    try {
      broken?.destroy();
    } catch {
      // The broken codec is dropped either way.
    }
  }
});
