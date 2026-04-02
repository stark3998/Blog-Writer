import { describe, it, expect, beforeEach } from 'vitest';
import { useThemeStore } from '../themeStore';

describe('themeStore', () => {
  beforeEach(() => {
    // Reset store to light theme before each test
    useThemeStore.getState().setTheme('light');
  });

  it('should default to light theme', () => {
    const { theme } = useThemeStore.getState();
    expect(theme).toBe('light');
  });

  it('toggleTheme switches from light to dark', () => {
    useThemeStore.getState().toggleTheme();
    expect(useThemeStore.getState().theme).toBe('dark');
  });

  it('toggleTheme switches from dark back to light', () => {
    useThemeStore.getState().setTheme('dark');
    useThemeStore.getState().toggleTheme();
    expect(useThemeStore.getState().theme).toBe('light');
  });

  it('setTheme sets the theme to dark', () => {
    useThemeStore.getState().setTheme('dark');
    expect(useThemeStore.getState().theme).toBe('dark');
  });

  it('setTheme sets the theme to light', () => {
    useThemeStore.getState().setTheme('dark');
    useThemeStore.getState().setTheme('light');
    expect(useThemeStore.getState().theme).toBe('light');
  });

  it('adds dark class to documentElement when theme is dark', () => {
    useThemeStore.getState().setTheme('dark');
    expect(document.documentElement.classList.contains('dark')).toBe(true);
  });

  it('removes dark class from documentElement when theme is light', () => {
    useThemeStore.getState().setTheme('dark');
    expect(document.documentElement.classList.contains('dark')).toBe(true);

    useThemeStore.getState().setTheme('light');
    expect(document.documentElement.classList.contains('dark')).toBe(false);
  });

  it('toggleTheme adds and removes dark class', () => {
    useThemeStore.getState().toggleTheme(); // light -> dark
    expect(document.documentElement.classList.contains('dark')).toBe(true);

    useThemeStore.getState().toggleTheme(); // dark -> light
    expect(document.documentElement.classList.contains('dark')).toBe(false);
  });
});
