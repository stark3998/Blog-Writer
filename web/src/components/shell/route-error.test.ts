import { describe, expect, it } from 'vitest';

import { isChunkLoadError } from './route-error';

describe('isChunkLoadError', () => {
  it('recognises failed lazy chunk downloads in each browser', () => {
    expect(isChunkLoadError(new TypeError('Failed to fetch dynamically imported module: /assets/today-x.js'))).toBe(
      true,
    );
    expect(isChunkLoadError(new TypeError('Importing a module script failed.'))).toBe(true);
    expect(isChunkLoadError(new TypeError('error loading dynamically imported module'))).toBe(true);
    const named = new Error('Loading chunk 3 failed');
    named.name = 'ChunkLoadError';
    expect(isChunkLoadError(named)).toBe(true);
  });

  it('leaves other errors alone', () => {
    expect(isChunkLoadError(new Error('Cannot read properties of undefined'))).toBe(false);
    expect(isChunkLoadError('Failed to fetch dynamically imported module')).toBe(false);
    expect(isChunkLoadError(null)).toBe(false);
  });
});
