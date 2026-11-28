import { screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';

import { THEME_STORAGE_KEY } from '@/theme/theme';
import { allFirstInstallHandlers } from '@/test/first-install';
import { server } from '@/test/msw';
import { findPageHeading, renderApp } from '@/test/render-app';

import { isCommandPaletteShortcut } from './use-command-palette-shortcut';

// The palette opens pages: every area answers like a first install.
beforeEach(() => {
  server.use(...allFirstInstallHandlers());
});

async function openWith(user: ReturnType<typeof renderApp>['user'], keys: string) {
  await user.keyboard(keys);
  return screen.findByRole('dialog', { name: 'Command palette' });
}

describe('isCommandPaletteShortcut', () => {
  const key = (init: KeyboardEventInit) => new KeyboardEvent('keydown', init);

  it('accepts Cmd+K and Ctrl+K only', () => {
    expect(isCommandPaletteShortcut(key({ key: 'k', metaKey: true }))).toBe(true);
    expect(isCommandPaletteShortcut(key({ key: 'K', ctrlKey: true }))).toBe(true);
    expect(isCommandPaletteShortcut(key({ key: 'k' }))).toBe(false);
    expect(isCommandPaletteShortcut(key({ key: 'k', ctrlKey: true, shiftKey: true }))).toBe(false);
    expect(isCommandPaletteShortcut(key({ key: 'k', metaKey: true, altKey: true }))).toBe(false);
    expect(isCommandPaletteShortcut(key({ key: 'j', metaKey: true }))).toBe(false);
  });
});

describe('command palette', () => {
  it('opens with Ctrl+K or Cmd+K, focuses the search field and closes with Escape', async () => {
    const { user } = renderApp('/today');
    await findPageHeading('Today');

    const dialog = await openWith(user, '{Control>}k{/Control}');
    expect(within(dialog).getByRole('combobox')).toHaveFocus();
    await user.keyboard('{Escape}');
    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });

    await openWith(user, '{Meta>}k{/Meta}');
    // The same shortcut closes it again.
    await user.keyboard('{Meta>}k{/Meta}');
    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });
  });

  it('opens from the sidebar search button', async () => {
    const { user } = renderApp('/today');
    await findPageHeading('Today');
    await user.click(screen.getByRole('button', { name: 'Search' }));
    expect(await screen.findByRole('dialog', { name: 'Command palette' })).toBeInTheDocument();
  });

  it('gives focus back to the button that opened it when it closes without going anywhere', async () => {
    const { user } = renderApp('/today');
    await findPageHeading('Today');
    const search = screen.getByRole('button', { name: 'Search' });
    await user.click(search);
    await screen.findByRole('dialog', { name: 'Command palette' });
    await user.keyboard('{Escape}');
    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });
    await waitFor(() => {
      expect(search).toHaveFocus();
    });
  });

  it('gives focus back to where the shortcut was pressed', async () => {
    const { user } = renderApp('/library');
    await findPageHeading('Library');
    const pillars = screen.getByRole('tab', { name: 'Pillars' });
    pillars.focus();
    const dialog = await openWith(user, '{Control>}k{/Control}');
    await user.type(within(dialog).getByRole('combobox'), 'light theme');
    await user.keyboard('{Enter}');
    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });
    await waitFor(() => {
      expect(pillars).toHaveFocus();
    });
  });

  it('lists every area and section', async () => {
    const { user } = renderApp('/today');
    await findPageHeading('Today');
    const dialog = await openWith(user, '{Control>}k{/Control}');
    const options = within(dialog)
      .getAllByRole('option')
      .map((option) => option.textContent);
    for (const label of ['Today', 'Calendar', 'Studio', 'Library', 'Settings', 'SlotsSettings', 'Voice guideLibrary']) {
      expect(options).toContain(label);
    }
  });

  it('jumps to a section by name and lands focus on the new page', async () => {
    const { user, router } = renderApp('/today');
    await findPageHeading('Today');
    const dialog = await openWith(user, '{Control>}k{/Control}');

    await user.type(within(dialog).getByRole('combobox'), 'slots');
    const options = within(dialog).getAllByRole('option');
    expect(options[0]).toHaveTextContent('Slots');
    await user.keyboard('{Enter}');

    const heading = await findPageHeading('Settings');
    expect(router.state.location.pathname).toBe('/settings/slots');
    expect(screen.getByRole('tab', { name: 'Slots' })).toHaveAttribute('aria-selected', 'true');
    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });
    await waitFor(() => {
      expect(heading).toHaveFocus();
    });
  });

  it('matches on keywords, not just labels', async () => {
    const { user, router } = renderApp('/today');
    await findPageHeading('Today');
    const dialog = await openWith(user, '{Control>}k{/Control}');
    await user.type(within(dialog).getByRole('combobox'), 'linkedin');
    expect(within(dialog).getAllByRole('option')[0]).toHaveTextContent('Integrations');
    await user.keyboard('{Enter}');
    await waitFor(() => {
      expect(router.state.location.pathname).toBe('/settings/integrations');
    });
  });

  it('says so when nothing matches', async () => {
    const { user } = renderApp('/today');
    await findPageHeading('Today');
    const dialog = await openWith(user, '{Control>}k{/Control}');
    await user.type(within(dialog).getByRole('combobox'), 'zzzz');
    expect(within(dialog).getByText('No matches. Try a page name, such as Slots or Voice guide.')).toBeInTheDocument();
  });

  it('switches the theme', async () => {
    const { user } = renderApp('/today');
    await findPageHeading('Today');
    const dialog = await openWith(user, '{Control>}k{/Control}');
    await user.type(within(dialog).getByRole('combobox'), 'dark theme');
    await user.keyboard('{Enter}');
    expect(document.documentElement).toHaveClass('dark');
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe('dark');
  });

  it('collapses and expands the sidebar on desktop', async () => {
    const { user } = renderApp('/today');
    await findPageHeading('Today');

    let dialog = await openWith(user, '{Control>}k{/Control}');
    await user.type(within(dialog).getByRole('combobox'), 'collapse');
    await user.keyboard('{Enter}');
    expect(await screen.findByRole('button', { name: 'Expand sidebar' })).toBeInTheDocument();

    dialog = await openWith(user, '{Control>}k{/Control}');
    await user.type(within(dialog).getByRole('combobox'), 'expand');
    await user.keyboard('{Enter}');
    expect(await screen.findByRole('button', { name: 'Collapse sidebar' })).toBeInTheDocument();
  });

  it('opens from the phone header, without desktop-only commands', async () => {
    const { user, router } = renderApp('/today', { viewport: 'mobile' });
    await findPageHeading('Today');
    await user.click(within(screen.getByRole('banner')).getByRole('button', { name: 'Search' }));
    const dialog = await screen.findByRole('dialog', { name: 'Command palette' });
    expect(within(dialog).queryByRole('option', { name: /sidebar/i })).not.toBeInTheDocument();

    await user.click(within(dialog).getByRole('option', { name: 'Library' }));
    expect(await findPageHeading('Library')).toBeInTheDocument();
    expect(router.state.location.pathname).toBe('/library');
  });

  it('has a Close button on phones (no Escape key there), which returns focus to search', async () => {
    const { user, router } = renderApp('/today', { viewport: 'mobile' });
    await findPageHeading('Today');
    const search = within(screen.getByRole('banner')).getByRole('button', { name: 'Search' });
    await user.click(search);
    const dialog = await screen.findByRole('dialog', { name: 'Command palette' });

    const close = within(dialog).getByRole('button', { name: 'Close' });
    // Shown below the desktop breakpoint and on touch screens; Escape does the job elsewhere.
    expect(close).toHaveClass('hidden', 'max-md:inline-flex', 'pointer-coarse:inline-flex');
    await user.click(close);
    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });
    await waitFor(() => {
      expect(search).toHaveFocus();
    });
    expect(router.state.location.pathname).toBe('/today');
  });
});
