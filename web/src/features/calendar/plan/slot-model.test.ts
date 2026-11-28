// @vitest-environment node
import { describe, expect, it } from 'vitest';

import { cadenceView, DEFAULT_CADENCE, lanesView, pillarViews } from '../lib/settings';
import { cadenceSettings, plannedSlot, postSummary, TEST_NOW, weekPlan } from '../testing/fixtures';

import { describePlan, kindLabel, postHref, slotModels, slotPost, takeHref } from './slot-model';

const NOW = Date.parse(TEST_NOW);

describe('slot models', () => {
  it('links posts to their composer while drafts, to go-live after approval, and derivatives under their article', () => {
    expect(postHref({ id: 'post-1', lane: 'native', articleId: null, status: 'draft' })).toBe('/studio/post-1/posts');
    expect(postHref({ id: 'post-1', lane: 'native', articleId: null, status: 'staged' })).toBe(
      '/studio/post-1/go-live?post=post-1',
    );
    expect(postHref({ id: 'post-2', lane: 'derivative', articleId: 'art-1', status: 'draft' })).toBe(
      '/studio/art-1/posts?post=post-2',
    );
  });

  it('sends a quick-take slot to the ideas inbox with its idea and week', () => {
    const slot = plannedSlot({ id: 'wed', date: '2026-12-02', time: '10:00', lane: 'quick_take', ideaId: 'idea-eu' });
    expect(takeHref(slot, '2026-11-30')).toBe('/calendar/ideas?take=wed&week=2026-11-30&idea=idea-eu');
  });

  it('finds a slot’s post by its link, never by its day', () => {
    const plan = 'plan-2026-11-30';
    const quick = plannedSlot({ id: 'wed', date: '2026-12-02', time: '10:00', lane: 'quick_take' });
    const linked = postSummary({
      id: 'post-q',
      lane: 'quick_take',
      status: 'draft',
      date: '2026-12-02',
      time: '10:00',
      weekPlanId: plan,
      planSlotId: 'wed',
    });
    const sameDay = postSummary({ id: 'post-other', lane: 'quick_take', status: 'draft', date: '2026-12-02', time: '11:30' });
    const native = postSummary({ id: 'post-n', lane: 'native', status: 'draft', date: '2026-12-01', time: '10:00' });
    expect(slotPost(quick, plan, [sameDay, linked, native])?.id).toBe('post-q');
    expect(slotPost(quick, plan, [sameDay, native])).toBeUndefined(); // the same day is not a link
    expect(slotPost(quick, 'plan-2026-12-07', [linked])).toBeUndefined(); // another week's slot
    expect(slotPost(quick, plan, [{ ...linked, status: 'archived' }])).toBeUndefined();
    expect(slotPost(plannedSlot({ id: 'tue', date: '2026-12-01', time: '10:00', postId: 'post-n' }), plan, [native])?.id).toBe(
      'post-n',
    );
    expect(slotPost(plannedSlot({ id: 'thu', date: '2026-12-03', time: '10:00' }), plan, [native])).toBeUndefined();
  });

  it.each(['archived', 'expired', 'deleted'] as const)('opens a slot again when its take is %s: a new take fills it', (gone) => {
    const plan = 'plan-2026-11-30';
    const slot = plannedSlot({ id: 'wed', date: '2026-12-02', time: '10:00', lane: 'quick_take', ideaId: 'idea-eu', postId: 'post-old' });
    const old = postSummary({
      id: 'post-old',
      lane: 'quick_take',
      status: gone,
      hook: 'The take that went',
      date: '2026-12-02',
      time: '10:00',
      weekPlanId: plan,
      planSlotId: 'wed',
    });
    // The plan still names the gone take (nothing clears `postId`), but it fills nothing.
    expect(slotPost(slot, plan, [old])).toBeUndefined();
    const approved = weekPlan({ status: 'approved', slots: [slot] });
    const [open] = slotModels(approved, [old], NOW);
    expect(open?.post).toBeUndefined();
    expect(open?.needsTake).toBe(true);
    expect(open?.state.label).toBe('Needs your take');
    expect(open?.title).toBe('Working title');
    expect(open?.removable).toBe(true);
    expect(open?.movable).toBe(false); // it waits for its new take where it is

    // The new take, before the plan's link lands (it names the slot) and after (the slot names it).
    const fresh = postSummary({
      id: 'post-new',
      lane: 'quick_take',
      status: 'generating',
      date: '2026-12-02',
      time: '10:00',
      weekPlanId: plan,
      planSlotId: 'wed',
    });
    expect(slotPost(slot, plan, [old, fresh])?.id).toBe('post-new');
    expect(slotModels(approved, [old, fresh], NOW)[0]?.needsTake).toBe(false);
    const relinked = weekPlan({ status: 'approved', slots: [{ ...slot, postId: 'post-new' }] });
    const [filled] = slotModels(relinked, [old, fresh], NOW);
    expect(filled?.post?.id).toBe('post-new');
    expect(filled?.needsTake).toBe(false);
    expect(filled?.state.label).toBe('Writing');
    expect(filled?.removable).toBe(false);
  });

  it('keeps a slot whose take is live, or not loaded yet, filled as before', () => {
    const plan = 'plan-2026-11-30';
    const slot = plannedSlot({ id: 'wed', date: '2026-12-02', time: '10:00', lane: 'quick_take', postId: 'post-q' });
    const live = postSummary({ id: 'post-q', lane: 'quick_take', status: 'draft', date: '2026-12-02', time: '10:00' });
    const approved = weekPlan({ status: 'approved', slots: [slot] });
    const [filled] = slotModels(approved, [live], NOW);
    expect(filled?.post?.id).toBe('post-q');
    expect(filled?.needsTake).toBe(false);
    expect(filled?.removable).toBe(false);
    expect(filled?.movable).toBe(true);
    expect(slotPost(slot, plan, [])).toBeUndefined();
  });

  it('says what each slot is and what the owner may do with it', () => {
    const approved = weekPlan({
      status: 'approved',
      slots: [
        plannedSlot({ id: 'tue', date: '2026-12-01', time: '10:00', postId: 'post-live' }),
        plannedSlot({ id: 'wed', date: '2026-12-02', time: '10:00', lane: 'quick_take' }),
        plannedSlot({ id: 'thu', date: '2026-12-03', time: '10:00', lane: 'derivative' }),
      ],
    });
    const posts = [postSummary({ id: 'post-live', status: 'posted', date: '2026-12-01', time: '10:00' })];
    const [tue, wed, thu] = slotModels(approved, posts, NOW);
    expect(tue?.state.label).toBe('Posted');
    expect(tue?.movable).toBe(false);
    expect(tue?.removable).toBe(false);
    expect(wed?.state.label).toBe('Needs your take');
    expect(wed?.needsTake).toBe(true);
    expect(wed?.removable).toBe(true);
    expect(thu?.state.label).toBe('Waits for the flagship');
    expect(thu?.zoneName).toBe('EST');
    expect(slotModels(weekPlan(), [], NOW)[0]?.state.label).toBe('Planned');
    expect(kindLabel({ lane: 'quick_take', format: 'text' })).toBe('Quick take text post');
  });

  it('describes a plan as lines for the conflict dialog', () => {
    expect(describePlan(weekPlan(), pillarViews(undefined)).split('\n\n')).toEqual([
      'Flagship: Okta to Entra: what 2,000 apps taught us (Identity modernization)',
      'Tue 1 Dec, 10:00: Native carousel, AI agents & security. MCP tool permissions in practice',
      'Wed 2 Dec, 10:00: Quick take text post, AI governance. EU AI Act guidance for agent builders',
      'Thu 3 Dec, 10:00: Native image card, Identity modernization. Conditional access checklist',
    ]);
  });
});

describe('settings with defaults', () => {
  it('fills the cadence from the server’s defaults, and never trusts an unknown zone', () => {
    expect(cadenceView(undefined)).toBe(DEFAULT_CADENCE);
    expect(cadenceView(cadenceSettings({ timezone: 'Mars/Olympus_Mons' })).timezone).toBe('America/New_York');
    expect(cadenceView(cadenceSettings({ timezone: 'Europe/London' })).timezone).toBe('Europe/London');
    expect(cadenceView(cadenceSettings({ postSlots: undefined })).postSlots).toEqual(DEFAULT_CADENCE.postSlots);
    expect(lanesView(undefined).onPillarMinShare).toBe(0.8);
    expect(pillarViews(undefined).map((pillar) => pillar.id)).toEqual(['agents', 'identity', 'governance']);
  });
});
