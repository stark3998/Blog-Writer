import { describe, expect, it } from 'vitest';

import { isApplePlatform, shortcutLabel } from './platform';

const nav = (userAgent: string, uaPlatform?: string) =>
  ({ userAgent, ...(uaPlatform ? { userAgentData: { platform: uaPlatform } } : {}) }) as unknown as Navigator;

describe('shortcut labels', () => {
  it('uses the command key on Apple devices', () => {
    expect(isApplePlatform(nav('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'))).toBe(true);
    expect(isApplePlatform(nav('Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X)'))).toBe(true);
    expect(shortcutLabel('K', nav('x', 'macOS'))).toBe('⌘K');
  });

  it('uses Ctrl elsewhere', () => {
    expect(shortcutLabel('K', nav('Mozilla/5.0 (Windows NT 10.0; Win64; x64)'))).toBe('Ctrl K');
    expect(shortcutLabel('K', nav('Mozilla/5.0 (X11; Linux x86_64)', 'Linux'))).toBe('Ctrl K');
  });
});
