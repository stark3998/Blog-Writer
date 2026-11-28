/**
 * Seeded random generation for the property tests (test-only; fast-check is
 * not a dependency of web/). A failure reports the seed, the run and the
 * generated input, so it can be replayed exactly.
 */

export interface Random {
  /** A float in [0, 1). */
  next: () => number;
  /** An integer in [min, max]. */
  int: (min: number, max: number) => number;
  /** One item of a non-empty list. */
  pick: <T>(items: readonly T[]) => T;
}

/** mulberry32: small, fast and good enough for test inputs. */
export function createRandom(seed: number): Random {
  let state = seed >>> 0;
  const next = (): number => {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  const int = (min: number, max: number): number => min + Math.floor(next() * (max - min + 1));
  const pick = <T>(items: readonly T[]): T => {
    const item = items[int(0, items.length - 1)];
    if (item === undefined) {
      throw new Error('pick() needs a non-empty list');
    }
    return item;
  };
  return { next, int, pick };
}

/** Returned by a property check to discard an input (like fast-check's `pre`). */
export const SKIP = Symbol('skip');

export interface PropertyOptions {
  runs: number;
  seed: number;
}

function describeInput(value: unknown): string {
  return JSON.stringify(value, (_key, item: unknown) => (typeof item === 'symbol' ? String(item) : item));
}

/**
 * Check `property` on `runs` generated inputs. Throws on the first failure,
 * naming the seed, the run and the input. At least a tenth of the runs must
 * not be skipped, so a too-strict precondition cannot make the test vacuous.
 */
export function checkProperty<T>(
  options: PropertyOptions,
  generate: (random: Random) => T,
  property: (value: T) => typeof SKIP | undefined,
): void {
  const random = createRandom(options.seed);
  let checked = 0;
  for (let run = 0; run < options.runs; run += 1) {
    const value = generate(random);
    let outcome: typeof SKIP | undefined;
    try {
      outcome = property(value);
    } catch (error) {
      const reason = error instanceof Error ? error.message : String(error);
      throw new Error(`Property failed (seed ${options.seed}, run ${run}) for ${describeInput(value)}: ${reason}`, {
        cause: error,
      });
    }
    if (outcome !== SKIP) {
      checked += 1;
    }
  }
  if (checked < Math.ceil(options.runs / 10)) {
    throw new Error(`Only ${checked} of ${options.runs} runs were checked; the precondition skips too much`);
  }
}

/** A string of 1..maxParts pieces drawn from `alphabet`. */
export function textFrom(random: Random, alphabet: readonly string[], maxParts: number): string {
  const parts = random.int(1, maxParts);
  let text = '';
  for (let index = 0; index < parts; index += 1) {
    text += random.pick(alphabet);
  }
  return text;
}

const URL_SEGMENT_CHARS = [
  ...Array.from('abcdefghijklmnopqrstuvwxyz0123456789'),
  ...Array.from('ABCXYZ'),
  ...Array.from('-._~!$&\'()*+,;=:@'),
  '%20',
  '%2F',
  '%C3%A9',
];

/** An absolute https URL: host, optional port, path, query and fragment, with every character a URL path may hold. */
export function webUrl(random: Random): string {
  const label = () => textFrom(random, Array.from('abcdefghijklmnopqrstuvwxyz0123456789'), 10);
  const host = Array.from({ length: random.int(1, 3) }, label).join('.');
  const port = random.next() < 0.2 ? `:${random.int(1, 65535)}` : '';
  const path = Array.from({ length: random.int(0, 4) }, () => `/${textFrom(random, URL_SEGMENT_CHARS, 8)}`).join('');
  const query = random.next() < 0.3 ? `?${textFrom(random, [...URL_SEGMENT_CHARS, '/', '?'], 10)}` : '';
  const fragment = random.next() < 0.3 ? `#${textFrom(random, [...URL_SEGMENT_CHARS, '/', '?'], 10)}` : '';
  return `https://${host}${port}${path}${query}${fragment}`;
}

/**
 * Arbitrary UTF-16 strings up to `maxLength` code units: ASCII, C0 and C1
 * controls, BMP characters, astral characters and lone surrogates.
 */
export function binaryString(random: Random, maxLength: number): string {
  const length = random.int(0, maxLength);
  let text = '';
  while (text.length < length) {
    const kind = random.int(0, 5);
    if (kind === 0) {
      text += String.fromCharCode(random.int(0, 0x1f));
    } else if (kind === 1) {
      text += String.fromCharCode(random.int(0x7f, 0x9f));
    } else if (kind === 2) {
      text += String.fromCharCode(random.int(0x20, 0x7e));
    } else if (kind === 3) {
      text += String.fromCharCode(random.int(0xa0, 0xffff));
    } else if (kind === 4) {
      text += String.fromCodePoint(random.int(0x10000, 0x10ffff));
    } else {
      text += String.fromCharCode(random.int(0xd800, 0xdfff));
    }
  }
  return text;
}
