export interface BackoffOptions {
  /** The first delay. */
  readonly baseMs: number;
  /** No delay is ever longer than this. */
  readonly maxMs: number;
  /** Spread each delay by up to this fraction either way, so clients do not retry in step. */
  readonly jitter?: number;
  /** A number in [0, 1); `Math.random` by default. */
  readonly random?: () => number;
}

/**
 * Capped exponential backoff: `baseMs * 2^attempt`, at most `maxMs`, with jitter. `attempt` counts
 * from 0 (the delay before the first retry). Shared by query retries and the event stream.
 */
export function backoffDelay(
  attempt: number,
  { baseMs, maxMs, jitter = 0.2, random = Math.random }: BackoffOptions,
): number {
  const exponential = Math.min(maxMs, baseMs * 2 ** Math.max(0, attempt));
  const spread = exponential * Math.min(Math.max(jitter, 0), 1);
  const delay = exponential - spread + 2 * spread * random();
  return Math.round(Math.min(maxMs, Math.max(0, delay)));
}
