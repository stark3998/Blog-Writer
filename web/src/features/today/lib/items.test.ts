// @vitest-environment node
import { describe, expect, expectTypeOf, it } from 'vitest';

import type { components } from '@/lib/api/schema';

import { busyItems, todayItem } from '../testing/fixtures';
import {
  groupSummary,
  headline,
  itemTarget,
  joinWords,
  KIND_META,
  kindMeta,
  safeHref,
  splitInbox,
  type TodayKind,
} from './items';

const EVERY_KIND: readonly TodayKind[] = [
  'approve_plan',
  'answer_interview',
  'review_article',
  'veto_window',
  'approve_post',
  'take_needed',
  'publish_post',
  'kit_confirm',
  'schedule_kit',
  'missed_slot',
  'reply',
  'snapshot',
  'needs_attention',
  'schedule_confirm',
  'reconnect_linkedin',
  'owner_input',
  'restore_drill',
];

describe('kind metadata', () => {
  it('names every kind the server sends, with one action each', () => {
    expectTypeOf<keyof typeof KIND_META>().toEqualTypeOf<components['schemas']['TodayKind']>();
    for (const kind of EVERY_KIND) {
      expect(KIND_META[kind].label, kind).not.toBe('');
      expect(KIND_META[kind].action, kind).not.toBe('');
    }
    expect(Object.keys(KIND_META).sort()).toEqual([...EVERY_KIND].sort());
  });

  it('has a safe default for a kind a newer server adds', () => {
    expect(kindMeta('something_new')).toMatchObject({ label: 'To do', action: 'Open' });
  });

  it('never says publish for a button that only opens the go-live page', () => {
    expect(KIND_META.publish_post.action).toBe('Go live');
  });
});

describe('the lead sentence', () => {
  it('says how many things need the owner', () => {
    expect(headline(0)).toBe('Nothing needs you right now.');
    expect(headline(1)).toBe('One thing needs you.');
    expect(headline(4)).toBe('Four things need you.');
    expect(headline(14)).toBe('14 things need you.');
  });

  it('groups the cards in a fixed order', () => {
    const { cards } = splitInbox(busyItems());
    expect(groupSummary(cards)).toBe(
      '1 post to publish, 1 to review and approve, 1 interview to answer, 1 reply to write and 1 set of numbers to log.',
    );
    expect(groupSummary([])).toBe('');
    expect(joinWords(['a'])).toBe('a');
    expect(joinWords(['a', 'b'])).toBe('a and b');
  });
});

describe('splitInbox', () => {
  it('keeps the server’s order and moves setup items and reconnect banners out of the cards', () => {
    const reconnect = todayItem({ kind: 'reconnect_linkedin', title: 'Reconnect LinkedIn', href: '/settings/integrations' });
    const { cards, setup, notices } = splitInbox([reconnect, ...busyItems()]);
    expect(cards.map((item) => item.kind)).toEqual(['publish_post', 'approve_plan', 'answer_interview', 'reply', 'snapshot']);
    expect(setup.map((item) => item.kind)).toEqual(['owner_input']);
    expect(notices).toEqual([reconnect]);
  });
});

describe('itemTarget', () => {
  it('opens reminders that link back to Today in place', () => {
    expect(itemTarget({ href: '/today?engage=post-9' })).toEqual({ kind: 'engage', postId: 'post-9' });
    expect(itemTarget({ href: '/today?engage=' })).toEqual({ kind: 'engage', postId: null });
    expect(itemTarget({ href: '/today?snapshot=post-8' })).toEqual({ kind: 'snapshot', postId: 'post-8' });
  });

  it('opens every other link as a page, same origin only', () => {
    expect(itemTarget({ href: '/studio/post-1/go-live?post=post-1' })).toEqual({
      kind: 'page',
      href: '/studio/post-1/go-live?post=post-1',
    });
    expect(safeHref('https://evil.example/x')).toBe('/today');
    expect(safeHref('//evil.example/x')).toBe('/today');
    expect(safeHref('javascript:alert(1)')).toBe('/today');
    expect(itemTarget({ href: 'https://evil.example/today?engage=x' })).toEqual({ kind: 'page', href: '/today' });
  });
});
