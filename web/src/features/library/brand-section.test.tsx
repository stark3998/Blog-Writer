import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { toast } from 'sonner';
import { afterEach, describe, expect, it } from 'vitest';

import { apiUrl, server } from '@/test/msw';
import { findPageHeading, renderApp } from '@/test/render-app';

import { contrastRatio, formatRatio } from './lib/contrast';
import { brandKit, employerPolicy, hex, mediaRef, page, pillarSettings, postSummary, versioned } from './testing/fixtures';
import { getJson, problemWith, record, singleton, type RecordedRequest } from './testing/handlers';

afterEach(() => {
  act(() => {
    toast.dismiss();
  });
});

interface SetupOptions {
  onPut?: (body: unknown) => Response | undefined;
  posts?: ReturnType<typeof postSummary>[];
}

function setup({ onPut, posts = [] }: SetupOptions = {}) {
  const brand = singleton('/api/kb/brand', versioned(brandKit(), '"brand-1"'), { onPut });
  const uploads: RecordedRequest[] = [];
  server.use(
    ...brand.handlers,
    getJson('/api/kb/policy', versioned(employerPolicy(), '"policy-1"')),
    getJson('/api/kb/pillars', versioned(pillarSettings(), '"pillars-1"')),
    getJson('/api/posts', page(posts.map((post) => versioned(post)))),
    getJson('/api/posts/post-0001', versioned({ ...postSummary(), carouselSpec: { slides: [{ kind: 'cover' }] } })),
    http.post(apiUrl('/api/media'), async ({ request }) => {
      await record(uploads, request);
      return HttpResponse.json(mediaRef(), { status: 201 });
    }),
  );
  return { brand, uploads };
}

async function openBrand(options?: SetupOptions) {
  const state = setup(options);
  const view = renderApp('/library/brand');
  await findPageHeading('Library');
  await screen.findByRole('textbox', { name: /^Your name/ });
  return { ...state, ...view };
}

describe('Library > Brand kit', () => {
  it('shows the kit, its contrast checks, the footer and both previews', async () => {
    await openBrand();
    expect(screen.getByRole('heading', { level: 2, name: 'Brand kit' })).toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: /^Your name/ })).toHaveValue('Jordan Writer');
    expect(screen.getByRole('textbox', { name: /^Ink/ })).toHaveValue(hex('0E2A24'));
    expect(screen.getByText('All 7 text pairs reach 4.5:1.')).toBeInTheDocument();
    expect(screen.getAllByText('Passes')).toHaveLength(7);
    // The footer as quoted in the kit (the sample slides show it too).
    expect(await screen.findByText('Views are my own.', { selector: 'q' })).toBeInTheDocument();
    const previews = screen.getByRole('complementary', { name: 'Previews' });
    expect(within(previews).getByRole('figure', { name: /A sample cover and content slide with your kit/ })).toBeInTheDocument();
    expect(await within(previews).findByText(/No carousel yet/)).toBeInTheDocument();
  });

  it('checks contrast as you type and keeps a failing palette from saving', async () => {
    const { brand, user } = await openBrand();
    const muted = screen.getByRole('textbox', { name: 'Muted' });
    await user.clear(muted);
    await user.type(muted, '9AA8A2');
    expect(screen.getByText('1 pair is below 4.5:1.')).toBeInTheDocument();
    const pair = screen.getByText('Muted on paper').closest('li');
    if (!pair) throw new Error('no contrast row');
    expect(within(pair).getByText('Too low')).toBeInTheDocument();
    expect(within(pair).getByText(formatRatio(contrastRatio(hex('9AA8A2'), hex('F3F5F2'))))).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Save brand kit' }));
    expect(await screen.findByText(/Muted on paper is below 4.5:1/)).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByRole('heading', { level: 4, name: 'Contrast' })).toHaveFocus();
    });
    expect(brand.requests.some((request) => request.method === 'PUT')).toBe(false);
  });

  it('uploads a headshot as raw bytes and saves the kit with it', async () => {
    const { brand, uploads, user } = await openBrand();
    const file = new File([new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])], 'client-offsite.png', { type: 'image/png' });
    await user.upload(screen.getByLabelText('Headshot file'), file);
    expect(await screen.findByRole('img', { name: 'Your headshot' })).toHaveAttribute(
      'src',
      `/api/media/uploads/2026-11/${'a'.repeat(64)}.png`,
    );
    const upload = uploads[0];
    expect(upload?.contentType).toBe('image/png');
    expect(upload?.search.get('role')).toBe('headshot');
    // The local file name never leaves the browser.
    expect(upload?.search.get('filename')).toBe('headshot.png');
    expect((upload?.body as ArrayBuffer).byteLength).toBe(8);

    await user.click(screen.getByRole('button', { name: 'Save brand kit' }));
    expect(await screen.findByText('Brand kit saved.')).toBeInTheDocument();
    const put = brand.requests.find((request) => request.method === 'PUT');
    expect(put?.ifMatch).toBe('"brand-1"');
    expect(put?.body).toMatchObject({ authorName: 'Jordan Writer', font: 'inter', headshot: mediaRef(), nameMark: null });
  });

  it('saves an art brief per pillar for the illustrations, and leaves an empty one to the default', async () => {
    const { brand, user } = await openBrand();
    const briefs = screen.getByRole('region', { name: 'Art briefs' });
    expect(within(briefs).getByText(/Leave one empty for the default look/)).toBeInTheDocument();
    expect(within(briefs).getByRole('link', { name: 'Settings, Models' })).toHaveAttribute('href', '/settings/models');
    await user.type(within(briefs).getByRole('textbox', { name: /AI agents & security/ }), 'Isometric line art, teal on paper');
    await user.type(within(briefs).getByRole('textbox', { name: /Identity modernization/ }), '   ');
    await user.click(screen.getByRole('button', { name: 'Save brand kit' }));
    expect(await screen.findByText('Brand kit saved.')).toBeInTheDocument();
    const put = brand.requests.find((request) => request.method === 'PUT');
    expect((put?.body as { artBriefs?: unknown }).artBriefs).toEqual({ agents: 'Isometric line art, teal on paper' });
  });

  it('refuses a file that is not an image before uploading it', async () => {
    const { uploads } = await openBrand();
    const gif = new File(['GIF89a'], 'mark.gif', { type: 'image/gif' });
    // The picker filters by type; a file dropped in anyway is refused before any upload.
    fireEvent.change(screen.getByLabelText('Name mark file'), { target: { files: [gif] } });
    expect(await screen.findByText('Choose a PNG, JPEG or WebP image.')).toBeInTheDocument();
    expect(uploads).toHaveLength(0);
  });

  it('marks the pairs the server refuses', async () => {
    const { user } = await openBrand({
      onPut: () =>
        problemWith(422, 'brand_contrast', {
          detail: 'Some colour pairs are below the WCAG AA contrast of 4.5:1.',
          findings: [{ code: 'brand-contrast', severity: 'block', message: 'signal on ink has a contrast of 4.40:1.', ref: 'signal:ink', overridable: false }],
        }),
    });
    await user.type(screen.getByRole('textbox', { name: /^Headline/ }), ' at scale');
    await user.click(screen.getByRole('button', { name: 'Save brand kit' }));
    expect(await screen.findByText('1 pair is below 4.5:1.')).toBeInTheDocument();
    const pair = screen.getByText('Signal on ink').closest('li');
    if (!pair) throw new Error('no contrast row');
    expect(within(pair).getByText('Too low')).toBeInTheDocument();
  });

  it('shows the newest carousel rendered by the server in a sandboxed frame', async () => {
    await openBrand({ posts: [postSummary({ id: 'post-0002', format: 'text' }), postSummary()] });
    const frame = await screen.findByTitle(/Your newest carousel/);
    expect(frame.tagName).toBe('IFRAME');
    expect(frame).toHaveAttribute('sandbox', '');
    expect(frame).toHaveAttribute('src', '/api/posts/post-0001/carousel/preview');
  });
});
