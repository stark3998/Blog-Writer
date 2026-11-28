// @vitest-environment node
import { describe, expect, it } from 'vitest';

import { carouselPreviewUrl, isHttpsUrl, MAX_URL_CHARS, mediaUrl } from './urls';

describe('isHttpsUrl', () => {
  it('accepts what the server stores and nothing else', () => {
    expect(isHttpsUrl('https://www.linkedin.com/in/someone')).toBe(true);
    expect(isHttpsUrl('http://www.linkedin.com/in/someone')).toBe(false);
    expect(isHttpsUrl('https://user:pass@example.com')).toBe(false);
    expect(isHttpsUrl('https://exa mple.com')).toBe(false);
    expect(isHttpsUrl('https://example.com\\x')).toBe(false);
    expect(isHttpsUrl(`https://example.com/${'a'.repeat(MAX_URL_CHARS)}`)).toBe(false);
    expect(isHttpsUrl('linkedin.com/in/x')).toBe(false);
  });
});

describe('mediaUrl', () => {
  it('keeps the folders of a media name and encodes each segment', () => {
    const sha = 'a'.repeat(64);
    expect(mediaUrl(`uploads/2026-11/${sha}.png`)).toBe(`/api/media/uploads/2026-11/${sha}.png`);
    expect(mediaUrl('posts/post-1/deck one.pdf')).toBe('/api/media/posts/post-1/deck%20one.pdf');
    expect(mediaUrl('brand/a?b#c.png')).toBe('/api/media/brand/a%3Fb%23c.png');
  });
});

describe('carouselPreviewUrl', () => {
  it('names the post in the preview route', () => {
    expect(carouselPreviewUrl('post-1')).toBe('/api/posts/post-1/carousel/preview');
    expect(carouselPreviewUrl('post/1')).toBe('/api/posts/post%2F1/carousel/preview');
  });
});
