import { act, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { findPageHeading, renderApp } from '@/test/render-app';

import { SIDEBAR_STORAGE_KEY } from './sidebar-state';

describe('desktop navigation', () => {
  it('moves between areas from the sidebar and focuses the new page heading', async () => {
    const { user, router } = renderApp('/today');
    await findPageHeading('Today');
    const nav = screen.getByRole('navigation', { name: 'Main' });

    await user.click(within(nav).getByRole('link', { name: 'Calendar' }));
    const heading = await findPageHeading('Calendar');
    expect(router.state.location.pathname).toBe('/calendar');
    expect(within(nav).getByRole('link', { name: 'Calendar' })).toHaveAttribute('aria-current', 'page');
    await waitFor(() => {
      expect(heading).toHaveFocus();
    });

    await user.click(within(nav).getByRole('link', { name: 'Settings' }));
    expect(await findPageHeading('Settings')).toBeInTheDocument();
    expect(router.state.location.pathname).toBe('/settings');
  });

  it('collapses to an icon rail that keeps every link named, and remembers it', async () => {
    const { user, unmount } = renderApp('/today');
    await findPageHeading('Today');

    const toggle = screen.getByRole('button', { name: 'Collapse sidebar' });
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    expect(toggle).toHaveAttribute('aria-controls', 'sidebar');
    await user.click(toggle);

    const expand = screen.getByRole('button', { name: 'Expand sidebar' });
    expect(expand).toHaveAttribute('aria-expanded', 'false');
    expect(document.getElementById('sidebar')).toHaveAttribute('data-collapsed', 'true');
    // Labels are visually hidden, not removed: links keep their accessible names.
    const nav = screen.getByRole('navigation', { name: 'Main' });
    const studio = within(nav).getByRole('link', { name: 'Studio' });
    // Regression: the tooltip's Slot must not stringify a function className.
    expect(studio).toHaveClass('justify-center', 'aria-[current=page]:bg-sheet');
    expect(studio.className).not.toContain('=>');
    expect(window.localStorage.getItem(SIDEBAR_STORAGE_KEY)).toBe('collapsed');

    unmount();
    renderApp('/today');
    await findPageHeading('Today');
    expect(screen.getByRole('button', { name: 'Expand sidebar' })).toBeInTheDocument();
  });

  it('shows the label as a tooltip when the rail hides it', async () => {
    window.localStorage.setItem(SIDEBAR_STORAGE_KEY, 'collapsed');
    const { user } = renderApp('/today');
    await findPageHeading('Today');
    await user.hover(within(screen.getByRole('navigation', { name: 'Main' })).getByRole('link', { name: 'Library' }));
    expect(await screen.findByRole('tooltip', { name: 'Library' })).toBeInTheDocument();
  });
});

describe('phone navigation', () => {
  it('moves between areas from the bottom tab bar', async () => {
    const { user, router } = renderApp('/today', { viewport: 'mobile' });
    await findPageHeading('Today');
    const tabBar = screen.getByRole('navigation', { name: 'Main' });

    await user.click(within(tabBar).getByRole('link', { name: 'Studio' }));
    expect(await findPageHeading('Studio')).toBeInTheDocument();
    expect(router.state.location.pathname).toBe('/studio');
    expect(within(tabBar).getByRole('link', { name: 'Studio' })).toHaveAttribute('aria-current', 'page');

    await user.click(within(tabBar).getByRole('link', { name: 'Today' }));
    expect(await findPageHeading('Today')).toBeInTheDocument();
  });

  it('keeps the area tab active on sub-pages', async () => {
    renderApp('/settings/system', { viewport: 'mobile' });
    await findPageHeading('Settings');
    const tabBar = screen.getByRole('navigation', { name: 'Main' });
    expect(within(tabBar).getByRole('link', { name: 'Settings' })).toHaveAttribute('aria-current', 'page');
  });
});

describe('section tabs', () => {
  it('puts the chosen tab in the URL without adding history entries', async () => {
    const { user, router } = renderApp('/today');
    await findPageHeading('Today');
    await user.click(within(screen.getByRole('navigation', { name: 'Main' })).getByRole('link', { name: 'Settings' }));
    await findPageHeading('Settings');

    await user.click(screen.getByRole('tab', { name: 'Slots' }));
    await waitFor(() => {
      expect(router.state.location.pathname).toBe('/settings/slots');
    });
    expect(router.state.historyAction).toBe('REPLACE');
    expect(screen.getByRole('tab', { name: 'Slots' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('heading', { level: 2, name: 'Post slots.' })).toBeInTheDocument();

    // Back leaves the page rather than stepping back through its tabs. (The memory router never
    // touches window.history, so its length cannot show this.)
    await act(async () => {
      await router.navigate(-1);
    });
    expect(router.state.location.pathname).toBe('/today');
    expect(await findPageHeading('Today')).toBeInTheDocument();
  });

  it('follows the arrow keys and keeps focus on the tab list', async () => {
    const { user, router } = renderApp('/library');
    await findPageHeading('Library');

    const pillars = screen.getByRole('tab', { name: 'Pillars' });
    pillars.focus();
    await user.keyboard('{ArrowRight}');
    await waitFor(() => {
      expect(router.state.location.pathname).toBe('/library/voice');
    });
    const voice = screen.getByRole('tab', { name: 'Voice guide' });
    // The render for the new URL can commit a moment after the router state changes.
    await waitFor(() => {
      expect(voice).toHaveAttribute('aria-selected', 'true');
    });
    expect(voice).toHaveFocus();
  });

  it('links a Studio piece back to the Studio list', async () => {
    const { user, router } = renderApp('/studio/piece-7/draft');
    await findPageHeading('Untitled piece');
    expect(screen.getByRole('tab', { name: 'Draft' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tablist', { name: 'Piece stages' })).toBeInTheDocument();

    await user.click(screen.getByRole('tab', { name: 'Go-live' }));
    await waitFor(() => {
      expect(router.state.location.pathname).toBe('/studio/piece-7/go-live');
    });

    await user.click(within(screen.getByRole('main')).getByRole('link', { name: 'Back to Studio' }));
    expect(await findPageHeading('Studio')).toBeInTheDocument();
  });
});
