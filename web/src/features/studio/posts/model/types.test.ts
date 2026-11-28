// @vitest-environment node
import { describe, expect, it } from 'vitest';

import { mediaUrl } from './media';
import { findingField, findingInText, findingSlide, truncatedCount, type CheckState, type Finding } from './types';

const finding = (field?: string | null): Finding => ({
  code: 'carousel-slide-words',
  severity: 'block',
  message: 'Too many words.',
  overridable: false,
  ...(field === undefined ? {} : { field }),
});

const check = (truncated: number): CheckState => ({
  ranAt: '2026-11-27T12:00:00.000000Z',
  contentSha256: 'a'.repeat(64),
  passed: false,
  findings: [finding('displayText')],
  truncated,
});

describe('stored check results', () => {
  it('read the post field a finding points into', () => {
    expect(findingField(finding('slides[3]'))).toBe('slides[3]');
    expect(findingSlide(finding('slides[3]'))).toBe(3);
    expect(findingInText(finding('displayText'))).toBe(true);
    expect(findingInText(finding())).toBe(true); // no field: the text
    expect(findingInText(finding(null))).toBe(true);
    expect(findingInText(finding('documentTitle'))).toBe(false);
  });

  it('count the findings a stored check left out', () => {
    expect(truncatedCount(check(12))).toBe(12);
    expect(truncatedCount(check(0))).toBe(0);
    expect(truncatedCount(null)).toBe(0);
    expect(truncatedCount(undefined)).toBe(0);
  });
});

describe('media', () => {
  it('keeps the folders of a media name in its address', () => {
    const sha = 'c'.repeat(64);
    expect(mediaUrl({ name: `posts/post-1/${sha}.pdf` })).toBe(`/api/media/posts/post-1/${sha}.pdf`);
  });
});
