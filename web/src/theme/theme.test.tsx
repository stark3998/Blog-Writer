import { runInNewContext } from 'node:vm';

import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { readProjectFile } from '@/test/files';
import { setMedia } from '@/test/media';
import { findPageHeading, renderApp } from '@/test/render-app';

import { resolveTheme, THEME_COLOR, THEME_STORAGE_KEY } from './theme';
import { ThemeProvider, useTheme } from './theme-provider';

const root = document.documentElement;

function themeColorMeta(): HTMLMetaElement {
  let meta = document.querySelector<HTMLMetaElement>('meta[name="theme-color"]');
  if (!meta) {
    meta = document.createElement('meta');
    meta.name = 'theme-color';
    document.head.append(meta);
  }
  return meta;
}

function Probe() {
  const { preference, resolved, setPreference } = useTheme();
  return (
    <div>
      <p>
        {preference} / {resolved}
      </p>
      <button
        type="button"
        onClick={() => {
          setPreference('dark');
        }}
      >
        Go dark
      </button>
    </div>
  );
}

describe('resolveTheme', () => {
  it('follows the system only when the preference is "system"', () => {
    expect(resolveTheme('system', true)).toBe('dark');
    expect(resolveTheme('system', false)).toBe('light');
    expect(resolveTheme('light', true)).toBe('light');
    expect(resolveTheme('dark', false)).toBe('dark');
  });
});

describe('ThemeProvider', () => {
  it('defaults to the system theme', () => {
    const meta = themeColorMeta();
    render(
      <ThemeProvider>
        <Probe />
      </ThemeProvider>,
    );
    expect(screen.getByText('system / light')).toBeInTheDocument();
    expect(root).not.toHaveClass('dark');
    expect(root.style.colorScheme).toBe('light');
    expect(meta.content).toBe(THEME_COLOR.light);
  });

  it('switches with the operating system while on "system"', () => {
    render(
      <ThemeProvider>
        <Probe />
      </ThemeProvider>,
    );
    act(() => {
      setMedia({ dark: true });
    });
    expect(screen.getByText('system / dark')).toBeInTheDocument();
    expect(root).toHaveClass('dark');
    expect(themeColorMeta().content).toBe(THEME_COLOR.dark);
    act(() => {
      setMedia({ dark: false });
    });
    expect(root).not.toHaveClass('dark');
  });

  it('saves an explicit choice and ignores the system after that', async () => {
    const user = userEvent.setup();
    render(
      <ThemeProvider>
        <Probe />
      </ThemeProvider>,
    );
    await user.click(screen.getByRole('button', { name: 'Go dark' }));
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe('dark');
    expect(root).toHaveClass('dark');
    act(() => {
      setMedia({ dark: false });
    });
    expect(root).toHaveClass('dark');
  });

  it('restores the saved choice and falls back to system for unknown values', () => {
    window.localStorage.setItem(THEME_STORAGE_KEY, 'dark');
    const { unmount } = render(
      <ThemeProvider>
        <Probe />
      </ThemeProvider>,
    );
    expect(screen.getByText('dark / dark')).toBeInTheDocument();
    unmount();

    window.localStorage.setItem(THEME_STORAGE_KEY, 'sepia');
    render(
      <ThemeProvider>
        <Probe />
      </ThemeProvider>,
    );
    expect(screen.getByText('system / light')).toBeInTheDocument();
  });

  it('follows a change made in another tab', () => {
    render(
      <ThemeProvider>
        <Probe />
      </ThemeProvider>,
    );
    act(() => {
      window.localStorage.setItem(THEME_STORAGE_KEY, 'dark');
      window.dispatchEvent(new StorageEvent('storage', { key: THEME_STORAGE_KEY, newValue: 'dark' }));
    });
    expect(screen.getByText('dark / dark')).toBeInTheDocument();
    expect(root).toHaveClass('dark');
  });
});

describe('theme menu', () => {
  it('sets light, dark and system from the sidebar', async () => {
    const { user } = renderApp('/today');
    await findPageHeading('Today');

    await user.click(screen.getByRole('button', { name: 'Theme: System' }));
    const dark = await screen.findByRole('menuitemradio', { name: 'Dark' });
    expect(screen.getByRole('menuitemradio', { name: 'System' })).toHaveAttribute('aria-checked', 'true');
    await user.click(dark);
    expect(root).toHaveClass('dark');
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe('dark');

    await user.click(screen.getByRole('button', { name: 'Theme: Dark' }));
    await user.click(await screen.findByRole('menuitemradio', { name: 'Light' }));
    expect(root).not.toHaveClass('dark');
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe('light');

    await user.click(screen.getByRole('button', { name: 'Theme: Light' }));
    await user.click(await screen.findByRole('menuitemradio', { name: 'System' }));
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe('system');
  });

  it('works from the phone header', async () => {
    const { user } = renderApp('/today', { viewport: 'mobile' });
    await findPageHeading('Today');
    await user.click(screen.getByRole('button', { name: 'Theme: System' }));
    await user.click(await screen.findByRole('menuitemradio', { name: 'Dark' }));
    expect(root).toHaveClass('dark');
  });
});

describe('public/theme-init.js (runs before first paint)', () => {
  const source = readProjectFile('public/theme-init.js');
  const run = () => {
    // The shipped file, run against this page's window and document.
    runInNewContext(source, { window, document });
  };

  it('uses the same storage key and colours as the app', () => {
    expect(source).toContain(`'${THEME_STORAGE_KEY}'`);
    expect(source).toContain(`'${THEME_COLOR.light}'`);
    expect(source).toContain(`'${THEME_COLOR.dark}'`);
  });

  it('applies a saved dark theme', () => {
    const meta = themeColorMeta();
    window.localStorage.setItem(THEME_STORAGE_KEY, 'dark');
    run();
    expect(root).toHaveClass('dark');
    expect(root.style.colorScheme).toBe('dark');
    expect(meta.content).toBe(THEME_COLOR.dark);
  });

  it('follows the system when nothing is saved, and a saved light theme wins over a dark system', () => {
    setMedia({ dark: true });
    run();
    expect(root).toHaveClass('dark');

    window.localStorage.setItem(THEME_STORAGE_KEY, 'light');
    run();
    expect(root).not.toHaveClass('dark');
    expect(root.style.colorScheme).toBe('light');
  });

  it('survives storage that throws', () => {
    const original = Object.getOwnPropertyDescriptor(window, 'localStorage');
    Object.defineProperty(window, 'localStorage', {
      configurable: true,
      get() {
        throw new Error('SecurityError');
      },
    });
    try {
      expect(run).not.toThrow();
      expect(root).not.toHaveClass('dark');
    } finally {
      if (original) Object.defineProperty(window, 'localStorage', original);
    }
  });
});
