import { act, screen, within } from '@testing-library/react';
import { toast } from 'sonner';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { server } from '@/test/msw';
import { findPageHeading, renderApp } from '@/test/render-app';

import { formatPacks, versioned } from './testing/fixtures';
import { singleton, validationFailed } from './testing/handlers';

afterEach(() => {
  act(() => {
    toast.dismiss();
  });
  vi.restoreAllMocks();
});

async function openFormats(options: Parameters<typeof singleton>[2] = {}, etag: string | null = '"formats-1"') {
  const formats = singleton('/api/kb/formats', versioned(formatPacks(), etag), options);
  server.use(...formats.handlers);
  const view = renderApp('/library/formats');
  await findPageHeading('Library');
  await screen.findByRole('heading', { level: 2, name: 'Article formats' });
  await screen.findByRole('figure', { name: 'Article length by format' });
  return { formats, ...view };
}

describe('Library > Format packs', () => {
  it('lays every format’s length on one ruler', async () => {
    await openFormats();
    const ruler = screen.getByRole('figure', { name: 'Article length by format' });
    expect(within(ruler).getByText('1,500–2,500 words')).toBeInTheDocument();
    expect(within(ruler).getByText('900–1,500 words')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Case study \/ how I did it/ })).toHaveAttribute('aria-expanded', 'false');
    expect(screen.getByText('1,500–2,500 words. Full interview, 5–8 questions.')).toBeInTheDocument();
  });

  it('edits a format and saves it with If-Match', async () => {
    const { formats, user } = await openFormats();
    await user.click(screen.getByRole('button', { name: /News analysis/ }));
    const longest = await screen.findByRole('textbox', { name: 'Longest, in words' });
    await user.clear(longest);
    await user.type(longest, '1800');
    await user.type(screen.getByRole('textbox', { name: 'Add a required part to News analysis' }), 'a counterpoint{Enter}');
    expect(screen.getByRole('figure', { name: 'Article length by format' })).toHaveTextContent('900–1,800 words');
    await user.click(screen.getByRole('button', { name: 'Save formats' }));

    expect(await screen.findByText('Formats saved.')).toBeInTheDocument();
    const put = formats.requests.find((request) => request.method === 'PUT');
    expect(put?.ifMatch).toBe('"formats-1"');
    expect((put?.body as { packs: { id: string; maxWords: number; mustContain: string[] }[] }).packs[1]).toMatchObject({
      id: 'news-analysis',
      maxWords: 1800,
      mustContain: ['what changed (cited)', 'what to do Monday', 'a counterpoint'],
    });
  });

  it('catches a backwards range before saving and opens the format with the problem', async () => {
    const { formats, user } = await openFormats();
    await user.click(screen.getByRole('button', { name: /Case study/ }));
    const shortest = await screen.findByRole('textbox', { name: 'Shortest, in words' });
    await user.clear(shortest);
    await user.type(shortest, '3000');
    await user.click(screen.getByRole('button', { name: 'Save formats' }));
    expect(await screen.findByText('1 field needs a fix before saving.')).toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: 'Longest, in words' })).toHaveAccessibleDescription(/The longest is shorter than the shortest/);
    expect(formats.requests.some((request) => request.method === 'PUT')).toBe(false);
  });

  it('shows the server’s field errors where they belong', async () => {
    const { user } = await openFormats({
      onPut: () => validationFailed([{ loc: ['packs', 1, 'name'], msg: 'String should have at most 80 characters' }]),
    });
    await user.click(screen.getByRole('button', { name: /News analysis/ }));
    const name = await screen.findByRole('textbox', { name: /^Name/ });
    await user.type(name, '!');
    await user.click(screen.getByRole('button', { name: 'Save formats' }));
    expect(await screen.findByText('String should have at most 80 characters')).toBeInTheDocument();
    expect(name).toHaveAttribute('aria-invalid', 'true');
  });

  it('adds a format whose id follows its name', async () => {
    const { formats, user } = await openFormats();
    await user.click(screen.getByRole('button', { name: 'Add a format' }));
    await user.type(await screen.findByRole('textbox', { name: /^Name/ }), 'Field notes');
    expect(screen.getByRole('textbox', { name: /^Id/ })).toHaveValue('field-notes');
    await user.click(screen.getByRole('button', { name: 'Save formats' }));
    await screen.findByText('Formats saved.');
    const body = formats.requests.find((request) => request.method === 'PUT')?.body as { packs: { id: string; name: string }[] };
    expect(body.packs.map((pack) => pack.id)).toEqual(['case-study', 'news-analysis', 'field-notes']);
  });

  it('keeps the changes and waits for the connection while offline', async () => {
    const { formats, user } = await openFormats();
    await user.click(screen.getByRole('button', { name: /News analysis/ }));
    const longest = await screen.findByRole('textbox', { name: 'Longest, in words' });
    await user.clear(longest);
    await user.type(longest, '1600');
    const online = vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false);
    act(() => {
      window.dispatchEvent(new Event('offline'));
    });
    expect(await screen.findByRole('button', { name: 'Save formats' })).toBeDisabled();
    expect(screen.getAllByText(/You’re offline. Your changes stay on this page/).length).toBeGreaterThan(0);
    online.mockReturnValue(true);
    act(() => {
      window.dispatchEvent(new Event('online'));
    });
    await user.click(screen.getByRole('button', { name: 'Save formats' }));
    await screen.findByText('Formats saved.');
    expect(formats.requests.filter((request) => request.method === 'PUT')).toHaveLength(1);
  });
});
