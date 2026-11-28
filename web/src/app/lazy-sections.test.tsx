import { act, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { sectionModules, SectionSuspense } from './lazy-sections';

function Greeting({ name }: { name: string }) {
  return <p>Hello, {name}</p>;
}

describe('sectionModules', () => {
  it('loads each section once, however many callers ask', async () => {
    const load = vi.fn(() => Promise.resolve(Greeting));
    const sections = sectionModules<'hello', { name: string }>({ hello: load });
    await Promise.all([sections.preload('hello'), sections.preload('hello')]);
    await sections.preload('hello');
    expect(load).toHaveBeenCalledTimes(1);
  });

  it('renders a preloaded section at once, with no fallback', async () => {
    const sections = sectionModules<'hello', { name: string }>({ hello: () => Promise.resolve(Greeting) });
    await sections.preload('hello');
    render(<SectionSuspense>{sections.render('hello', { name: 'owner' })}</SectionSuspense>);
    expect(screen.getByText('Hello, owner')).toBeInTheDocument();
    expect(screen.queryByText('Loading section')).not.toBeInTheDocument();
  });

  it('suspends with the section skeleton when the code is not there yet', async () => {
    let resolve: (component: typeof Greeting) => void = () => undefined;
    const sections = sectionModules<'hello', { name: string }>({
      hello: () =>
        new Promise((done) => {
          resolve = done;
        }),
    });
    render(<SectionSuspense>{sections.render('hello', { name: 'owner' })}</SectionSuspense>);
    expect(screen.getByRole('status')).toHaveTextContent('Loading section');
    await act(async () => {
      resolve(Greeting);
      await Promise.resolve();
    });
    expect(await screen.findByText('Hello, owner')).toBeInTheDocument();
  });

  it('tries again after a failed download', async () => {
    const load = vi
      .fn<() => Promise<typeof Greeting>>()
      .mockRejectedValueOnce(new TypeError('Failed to fetch dynamically imported module'))
      .mockResolvedValueOnce(Greeting);
    const sections = sectionModules<'hello', { name: string }>({ hello: load });
    await expect(sections.preload('hello')).rejects.toThrow('Failed to fetch dynamically imported module');
    await expect(sections.preload('hello')).resolves.toBeUndefined();
    expect(load).toHaveBeenCalledTimes(2);
  });
});
