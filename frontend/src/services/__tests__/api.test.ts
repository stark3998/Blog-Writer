import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

// We need to mock fetch before importing the module
const mockFetch = vi.fn();
global.fetch = mockFetch;

// Import after setting up the mock
import { generateBlog, listDrafts } from '../api';

function createMockResponse(data: unknown, ok = true, status = 200): Response {
  return {
    ok,
    status,
    json: () => Promise.resolve(data),
    headers: new Headers(),
    redirected: false,
    statusText: ok ? 'OK' : 'Error',
    type: 'basic' as ResponseType,
    url: '',
    clone: () => ({} as Response),
    body: null,
    bodyUsed: false,
    arrayBuffer: () => Promise.resolve(new ArrayBuffer(0)),
    blob: () => Promise.resolve(new Blob()),
    formData: () => Promise.resolve(new FormData()),
    text: () => Promise.resolve(''),
    bytes: () => Promise.resolve(new Uint8Array()),
  } as Response;
}

describe('api service', () => {
  beforeEach(() => {
    mockFetch.mockReset();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  describe('json() helper (via generateBlog)', () => {
    it('adds Content-Type application/json header', async () => {
      mockFetch.mockResolvedValueOnce(
        createMockResponse({
          mdx_content: '# Test',
          slug: 'test',
          title: 'Test',
          excerpt: 'A test',
          source_type: 'webpage',
        })
      );

      await generateBlog('https://example.com');

      expect(mockFetch).toHaveBeenCalledTimes(1);
      const [, init] = mockFetch.mock.calls[0];
      expect(init.headers['Content-Type']).toBe('application/json');
    });

    it('throws on non-ok responses', async () => {
      mockFetch.mockResolvedValueOnce(
        createMockResponse({ detail: 'Not found' }, false, 404)
      );

      await expect(generateBlog('https://example.com')).rejects.toThrow('Not found');
    });

    it('throws generic HTTP error when no detail in response', async () => {
      mockFetch.mockResolvedValueOnce(
        createMockResponse({}, false, 500)
      );

      await expect(generateBlog('https://example.com')).rejects.toThrow('HTTP 500');
    });
  });

  describe('generateBlog', () => {
    it('calls the correct endpoint with POST method', async () => {
      mockFetch.mockResolvedValueOnce(
        createMockResponse({
          mdx_content: '# Blog',
          slug: 'blog',
          title: 'Blog',
          excerpt: 'Excerpt',
          source_type: 'webpage',
        })
      );

      await generateBlog('https://example.com/article');

      expect(mockFetch).toHaveBeenCalledTimes(1);
      const [url, init] = mockFetch.mock.calls[0];
      expect(url).toBe('/api/generate');
      expect(init.method).toBe('POST');
      expect(JSON.parse(init.body)).toEqual({ url: 'https://example.com/article' });
    });
  });

  describe('listDrafts', () => {
    it('calls GET /api/blogs with limit', async () => {
      mockFetch.mockResolvedValueOnce(
        createMockResponse([
          { id: '1', title: 'Draft 1', slug: 'draft-1' },
          { id: '2', title: 'Draft 2', slug: 'draft-2' },
        ])
      );

      const result = await listDrafts(10);

      expect(mockFetch).toHaveBeenCalledTimes(1);
      const [url, init] = mockFetch.mock.calls[0];
      expect(url).toBe('/api/blogs?limit=10');
      // listDrafts uses GET (no explicit method set, so undefined or GET)
      expect(init.method).toBeUndefined();
      expect(result).toHaveLength(2);
      expect(result[0].title).toBe('Draft 1');
    });

    it('defaults to limit=50', async () => {
      mockFetch.mockResolvedValueOnce(createMockResponse([]));

      await listDrafts();

      const [url] = mockFetch.mock.calls[0];
      expect(url).toBe('/api/blogs?limit=50');
    });
  });
});
