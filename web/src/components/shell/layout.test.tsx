import { act, screen, within } from '@testing-library/react';
import { toast } from 'sonner';
import { describe, expect, it } from 'vitest';

import { setMedia } from '@/test/media';
import { findPageHeading, renderApp } from '@/test/render-app';

const AREA_NAMES = ['Today', 'Calendar', 'Studio', 'Library', 'Settings'];

describe('desktop layout', () => {
  it('has one main navigation with the five areas, in order', async () => {
    renderApp('/today');
    await findPageHeading('Today');
    const nav = screen.getByRole('navigation', { name: 'Main' });
    expect(within(nav).getAllByRole('link').map((link) => link.textContent)).toEqual(AREA_NAMES);
    expect(screen.getAllByRole('navigation', { name: 'Main' })).toHaveLength(1);
  });

  it('marks the current area with aria-current', async () => {
    renderApp('/library/voice');
    await findPageHeading('Library');
    const nav = screen.getByRole('navigation', { name: 'Main' });
    expect(within(nav).getByRole('link', { name: 'Library' })).toHaveAttribute('aria-current', 'page');
    expect(within(nav).getByRole('link', { name: 'Today' })).not.toHaveAttribute('aria-current');
  });

  it('puts the page in a single main landmark that can take focus', async () => {
    renderApp('/today');
    await findPageHeading('Today');
    const main = screen.getByRole('main');
    expect(main).toHaveAttribute('id', 'main');
    expect(main).toHaveAttribute('tabindex', '-1');
    expect(within(main).getByRole('heading', { level: 1, name: 'Today' })).toBeInTheDocument();
  });

  it('offers search, theme and collapse controls without a phone header or tab bar', async () => {
    renderApp('/today');
    await findPageHeading('Today');
    expect(screen.getByRole('button', { name: 'Search' })).toHaveAttribute('aria-keyshortcuts', 'Meta+K Control+K');
    expect(screen.getByRole('button', { name: 'Theme: System' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Collapse sidebar' })).toBeInTheDocument();
    expect(screen.queryByRole('banner')).not.toBeInTheDocument();
  });

  it('shows the owner time budget on Today', async () => {
    renderApp('/today');
    await findPageHeading('Today');
    expect(screen.getByRole('heading', { level: 2, name: 'Your week' })).toBeInTheDocument();
    for (const term of ['Sunday', 'Mid-week', 'Each post slot', 'Daily']) {
      expect(screen.getByText(term)).toBeInTheDocument();
    }
    expect(screen.getByText('About 2½ to 3 hours a week in total.')).toBeInTheDocument();
  });
});

describe('skip link', () => {
  it('is the first stop for the keyboard and moves focus to the page', async () => {
    const { user } = renderApp('/today');
    await findPageHeading('Today');
    await user.tab();
    const skip = screen.getByRole('link', { name: 'Skip to content' });
    expect(skip).toHaveFocus();
    await user.keyboard('{Enter}');
    expect(screen.getByRole('main')).toHaveFocus();
  });
});

describe('phone layout', () => {
  it('swaps the sidebar for a header and a bottom tab bar', async () => {
    renderApp('/today', { viewport: 'mobile' });
    await findPageHeading('Today');
    const header = screen.getByRole('banner');
    expect(within(header).getByRole('link', { name: 'Blog Writer' })).toHaveAttribute('href', '/today');
    expect(within(header).getByRole('button', { name: 'Search' })).toBeInTheDocument();
    expect(within(header).getByRole('button', { name: 'Theme: System' })).toBeInTheDocument();

    const tabBar = screen.getByRole('navigation', { name: 'Main' });
    expect(within(tabBar).getAllByRole('link').map((link) => link.textContent)).toEqual(AREA_NAMES);
    expect(screen.queryByRole('button', { name: 'Collapse sidebar' })).not.toBeInTheDocument();
  });

  it('keeps Today reachable with one thumb: tab bar items are full-height targets', async () => {
    renderApp('/today', { viewport: 'mobile' });
    await findPageHeading('Today');
    const tabBar = screen.getByRole('navigation', { name: 'Main' });
    const today = within(tabBar).getByRole('link', { name: 'Today' });
    expect(today).toHaveAttribute('aria-current', 'page');
    // 4rem tall; the bar clears the home indicator with the safe-area inset.
    expect(today).toHaveClass('h-16');
    expect(tabBar).toHaveClass('fixed', 'bottom-0', 'pb-[env(safe-area-inset-bottom)]');
    // The page scrolls clear of the bar: 4rem plus the same inset.
    expect(screen.getByRole('main')).toHaveClass('pb-[calc(4rem+env(safe-area-inset-bottom))]');
  });

  it('marks the current tab by weight as well as colour', async () => {
    renderApp('/calendar/ideas', { viewport: 'mobile' });
    await findPageHeading('Calendar');
    const tabBar = screen.getByRole('navigation', { name: 'Main' });
    for (const link of within(tabBar).getAllByRole('link')) {
      expect(link).toHaveClass('aria-[current=page]:font-semibold', 'aria-[current=page]:text-ink');
    }
    expect(within(tabBar).getByRole('link', { name: 'Calendar' })).toHaveAttribute('aria-current', 'page');
    expect(within(tabBar).getByRole('link', { name: 'Today' })).not.toHaveAttribute('aria-current');
  });

  it('follows the viewport when it crosses the breakpoint', async () => {
    renderApp('/today');
    await findPageHeading('Today');
    expect(screen.getByRole('button', { name: 'Collapse sidebar' })).toBeInTheDocument();
    act(() => {
      setMedia({ desktop: false });
    });
    expect(screen.queryByRole('button', { name: 'Collapse sidebar' })).not.toBeInTheDocument();
    expect(screen.getByRole('banner')).toBeInTheDocument();
  });
});

describe('connectivity', () => {
  it('tells the owner when the connection drops and comes back', async () => {
    renderApp('/today');
    await findPageHeading('Today');
    act(() => {
      window.dispatchEvent(new Event('offline'));
    });
    expect(await screen.findByText('You’re offline')).toBeInTheDocument();
    act(() => {
      window.dispatchEvent(new Event('online'));
    });
    expect(await screen.findByText('Back online')).toBeInTheDocument();
    act(() => {
      toast.dismiss();
    });
  });
});
