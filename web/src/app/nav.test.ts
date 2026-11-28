// @vitest-environment node
import { describe, expect, it } from 'vitest';

import { AREAS, CALENDAR_VIEWS, findSection, LIBRARY_SECTIONS, pieceKind, SETTINGS_SECTIONS, studioTabsFor } from './nav';

describe('navigation config', () => {
  it('has every section of the contract’s web map (section 10)', () => {
    expect(CALENDAR_VIEWS.map((view) => view.id)).toEqual(['plan', 'ideas', 'availability']);
    expect(LIBRARY_SECTIONS.map((section) => section.id)).toEqual(['pillars', 'voice', 'stories', 'brand', 'formats']);
    expect(SETTINGS_SECTIONS.map((section) => section.id)).toEqual([
      'models',
      'slots',
      'sources',
      'integrations',
      'policy',
      'insights',
      'system',
    ]);
    expect(AREAS.map((area) => area.path)).toEqual(['/today', '/calendar', '/studio', '/library', '/settings']);
  });

  it('gives an article five tabs and a standalone post all but Draft', () => {
    expect(pieceKind('art-0192a1b2c3d4e5f60718293a4b5c6d7e')).toBe('article');
    expect(pieceKind('post-0192a1b2c3d4e5f60718293a4b5c6d7e')).toBe('post');
    expect(studioTabsFor('art-1').map((tab) => tab.id)).toEqual(['brief', 'interview', 'draft', 'posts', 'go-live']);
    expect(studioTabsFor('post-1').map((tab) => tab.id)).toEqual(['brief', 'interview', 'posts', 'go-live']);
    expect(findSection(studioTabsFor('post-1'), 'draft')).toBeUndefined();
    expect(findSection(studioTabsFor('post-1'), undefined)?.id).toBe('brief');
  });
});
