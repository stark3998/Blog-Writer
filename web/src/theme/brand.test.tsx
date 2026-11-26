import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { BrandMark } from '@/components/shell/brand-mark';
import { readProjectFile } from '@/test/files';

import { BRAND_INK, BRAND_THREADS } from './brand';

const ATTRIBUTES = ['x', 'y', 'width', 'height', 'rx', 'fill'] as const;

/** Each <rect>'s geometry and fill, in drawing order. */
function rectsOf(svg: Element | null): Record<string, string | null>[] {
  if (!svg) throw new Error('no <svg>');
  return [...svg.querySelectorAll('rect')].map((rect) =>
    Object.fromEntries(ATTRIBUTES.map((name) => [name, rect.getAttribute(name)])),
  );
}

function renderedMark(): Element | null {
  return render(<BrandMark />).container.querySelector('svg');
}

describe('the app mark', () => {
  it('draws the three pillar threads on ink, with the brand colours', () => {
    expect(rectsOf(renderedMark()).map((rect) => rect.fill)).toEqual([BRAND_INK, ...BRAND_THREADS]);
  });

  it.each(['public/favicon.svg', 'public/icons/icon.svg'])('matches %s shape for shape', (path) => {
    const icon = new DOMParser().parseFromString(readProjectFile(path), 'image/svg+xml').documentElement;
    expect(rectsOf(renderedMark())).toEqual(rectsOf(icon));
  });

  it('uses the colours the icon generator draws with', () => {
    const script = readProjectFile('scripts/generate-icons.mjs');
    for (const colour of [BRAND_INK, ...BRAND_THREADS]) expect(script).toContain(`'${colour}'`);
  });
});
