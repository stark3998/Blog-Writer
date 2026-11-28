// @vitest-environment node
import { describe, expect, it } from 'vitest';

import { NEXT_WEEK, NY, plannedSlot, postSummary, TEST_NOW, weekPlan } from '../testing/fixtures';

import {
  blockMessage,
  calendarEntries,
  dayOptions,
  expectedPublishMs,
  findingSlot,
  flagshipMilestones,
  isPastWeek,
  meetsOnPillarRule,
  milestoneState,
  newSlotId,
  pillarBalance,
  planJobMs,
  previewPlan,
  problemFindings,
  slotInputs,
  targetTime,
  withAddedSlot,
  withEditedSlot,
  withMovedSlot,
  withoutSlot,
  type MoveContext,
} from './plan';
import { DEFAULT_CADENCE } from './settings';
import { momentLabel } from './time';

const NOW = Date.parse(TEST_NOW);

function context(overrides: Partial<MoveContext> = {}): MoveContext {
  return { plan: weekPlan(), posts: [], cadence: DEFAULT_CADENCE, zone: NY, nowMs: NOW, ...overrides };
}

function slotOf(id: string) {
  const slot = weekPlan().slots?.find((candidate) => candidate.id === id);
  if (!slot) throw new Error(`no slot ${id}`);
  return slot;
}

describe('moving a slot', () => {
  it('takes the target day’s template time, or keeps a time the owner picked', () => {
    const tuesday = slotOf('tue');
    expect(targetTime(DEFAULT_CADENCE, tuesday, '2026-11-30')).toBe('12:00');
    expect(targetTime(DEFAULT_CADENCE, tuesday, '2026-12-05')).toBe('10:00');
    const custom = plannedSlot({ id: 'c', date: '2026-12-01', time: '14:30' });
    expect(targetTime(DEFAULT_CADENCE, custom, '2026-12-04')).toBe('14:30');
  });

  it('blocks days that already hold a post: posts are never stacked', () => {
    const options = dayOptions(slotOf('tue'), context());
    const byDate = new Map(options.map((option) => [option.date, option]));
    expect(byDate.get('2026-12-01')?.current).toBe(true);
    const wednesday = byDate.get('2026-12-02');
    expect(wednesday?.block).toMatchObject({ reason: 'taken' });
    expect(wednesday?.block && blockMessage(wednesday.block)).toBe('Taken: a post goes out Wed 2 Dec, 10:00');
    expect(byDate.get('2026-12-04')?.block).toBeUndefined();
  });

  it('blocks a day a post outside the plan holds, and ignores posts that hold nothing', () => {
    const posts = [
      postSummary({ id: 'post-out', status: 'approved', date: '2026-12-04', time: '15:00', hook: 'Outside post' }),
      postSummary({ id: 'post-draft', status: 'draft', date: '2026-12-05', time: '10:00' }),
    ];
    const options = dayOptions(slotOf('tue'), context({ posts }));
    expect(options.find((option) => option.date === '2026-12-04')?.block).toMatchObject({
      reason: 'taken',
      by: { id: 'post-out', kind: 'post' },
    });
    expect(options.find((option) => option.date === '2026-12-05')?.block).toBeUndefined();
  });

  it('warns under 24 hours, without blocking', () => {
    const options = dayOptions(slotOf('thu'), context());
    const monday = options.find((option) => option.date === '2026-11-30');
    expect(monday?.time).toBe('12:00');
    // Monday 12:00 is 22 hours before Tuesday 10:00.
    expect(monday?.block).toBeUndefined();
    expect(monday?.warnings.map((warning) => warning.message)).toEqual([
      'Under 24 hours from Tue 1 Dec, 10:00 (22 hours)',
    ]);
  });

  it('blocks times that have passed', () => {
    const late = context({ nowMs: Date.parse('2026-12-02T16:00:00Z') });
    const options = dayOptions(slotOf('thu'), late);
    expect(options.find((option) => option.date === '2026-11-30')?.block).toEqual({ reason: 'past' });
    expect(options.find((option) => option.date === '2026-12-04')?.block).toBeUndefined();
  });

  it('lets a week without the same-day rule share days, with a warning', () => {
    const relaxed = context({ cadence: { ...DEFAULT_CADENCE, sameDayBlock: false } });
    const wednesday = dayOptions(slotOf('tue'), relaxed).find((option) => option.date === '2026-12-02');
    expect(wednesday?.block).toBeUndefined();
    expect(wednesday?.warnings[0]?.message).toBe('Same day as Wed 2 Dec, 10:00');
  });
});

describe('plan edits', () => {
  it('sends every slot, with only id, date and time unless something else changes', () => {
    const plan = weekPlan();
    expect(slotInputs(plan)).toEqual([
      { id: 'tue', localDate: '2026-12-01', localTime: '10:00' },
      { id: 'wed', localDate: '2026-12-02', localTime: '10:00' },
      { id: 'thu', localDate: '2026-12-03', localTime: '10:00' },
    ]);
    expect(withMovedSlot(plan, 'tue', '2026-12-04', '15:00')[0]).toEqual({
      id: 'tue',
      localDate: '2026-12-04',
      localTime: '15:00',
    });
    expect(withoutSlot(plan, 'wed').map((slot) => slot.id)).toEqual(['tue', 'thu']);
  });

  it('sends a slot’s contents only when no post fills it', () => {
    const plan = weekPlan();
    const content = {
      localDate: '2026-12-01',
      localTime: '11:00',
      lane: 'native' as const,
      pillar: 'governance',
      format: 'text' as const,
      title: 'New title',
    };
    expect(withEditedSlot(plan, 'tue', content, false)[0]).toEqual({ id: 'tue', ...content });
    expect(withEditedSlot(plan, 'tue', content, true)[0]).toEqual({
      id: 'tue',
      localDate: '2026-12-01',
      localTime: '11:00',
      title: 'New title',
    });
    const added = withAddedSlot(plan, 'own-1', { ...content, localDate: '2026-12-04', localTime: '15:00' });
    expect(added).toHaveLength(4);
    expect(added[3]).toMatchObject({ id: 'own-1', localDate: '2026-12-04', lane: 'native' });
  });

  it('makes slot ids the edit route accepts', () => {
    const id = newSlotId(() => 0.5);
    expect(id).toMatch(/^[a-z0-9-]{1,40}$/);
  });

  it('previews the edited plan in time order, with times converted in the plan’s zone', () => {
    const plan = weekPlan();
    const preview = previewPlan(plan, withMovedSlot(plan, 'tue', '2026-12-04', '15:00'), DEFAULT_CADENCE);
    expect(preview.slots?.map((slot) => slot.id)).toEqual(['wed', 'thu', 'tue']);
    const moved = preview.slots?.[2];
    expect(moved?.slot).toMatchObject({ localDate: '2026-12-04', localTime: '15:00', kind: 'optional' });
    expect(moved?.title).toBe('MCP tool permissions in practice');
    expect(moved && Date.parse(moved.slot.utc)).toBe(Date.parse('2026-12-04T20:00:00Z'));
  });
});

describe('balance and findings', () => {
  it('counts posts per pillar and checks the 80% rule', () => {
    const plan = weekPlan();
    const balance = pillarBalance(plan.slots ?? [], ['agents', 'identity', 'governance']);
    expect(Object.fromEntries(balance.counts)).toEqual({ agents: 1, identity: 1, governance: 1 });
    expect(balance.share).toBe(1);
    expect(meetsOnPillarRule(balance, 0.8)).toBe(true);
    const offPillar = pillarBalance(
      [...(plan.slots ?? []), plannedSlot({ id: 'x', date: '2026-12-04', time: '15:00', pillar: null })],
      ['agents', 'identity', 'governance'],
    );
    expect(offPillar.other).toBe(1);
    expect(offPillar.share).toBe(0.75);
    expect(meetsOnPillarRule(offPillar, 0.8)).toBe(false);
    expect(pillarBalance([], ['agents']).share).toBeNull();
  });

  it('finds the slot a finding is about, by id or by its UTC time', () => {
    const slots = weekPlan().slots ?? [];
    expect(findingSlot({ code: 'slot-past', severity: 'warn', message: 'x', ref: 'thu', overridable: false }, slots)?.id).toBe(
      'thu',
    );
    expect(
      findingSlot(
        { code: 'slot-same-day', severity: 'block', message: 'x', ref: '2026-12-02T15:00:00.000000Z', overridable: false },
        slots,
      )?.id,
    ).toBe('wed');
    expect(findingSlot({ code: 'mode-posts', severity: 'warn', message: 'x', overridable: false }, slots)).toBeUndefined();
  });

  it('reads the findings of a refused approval', () => {
    expect(
      problemFindings({
        findings: [{ code: 'slot-same-day', severity: 'block', message: 'Two posts on the same day.', ref: 'x' }, 'junk'],
      }),
    ).toEqual([{ code: 'slot-same-day', severity: 'block', message: 'Two posts on the same day.', ref: 'x', overridable: false }]);
    expect(problemFindings(undefined)).toEqual([]);
  });

  it('lists what is on the calendar, plan slots and outside posts, in time order', () => {
    const posts = [postSummary({ id: 'post-out', status: 'kit', date: '2026-11-30', time: '12:00', hook: 'Kit post' })];
    const entries = calendarEntries(weekPlan(), posts, NY);
    expect(entries.map((entry) => `${entry.kind}:${entry.id}`)).toEqual(['post:post-out', 'slot:tue', 'slot:wed', 'slot:thu']);
    expect(entries[0]?.label).toBe('Mon 30 Nov, 12:00');
  });
});

describe('the flagship’s week', () => {
  it('runs from Monday research to the next Monday’s publication', () => {
    const milestones = flagshipMilestones(NEXT_WEEK, DEFAULT_CADENCE, NY);
    expect(milestones.map((milestone) => `${milestone.id} ${momentLabel(milestone.utcMs, NY)}`)).toEqual([
      'research Mon 30 Nov, 08:00',
      'interview Wed 2 Dec, 18:00',
      'draft Thu 3 Dec, 09:00',
      'veto Fri 4 Dec, 12:00',
      'publish Mon 7 Dec, 09:00',
    ]);
    expect(expectedPublishMs(NEXT_WEEK, DEFAULT_CADENCE, NY)).toBe(Date.parse('2026-12-07T14:00:00Z'));
  });

  it('marks steps from the article’s status, or from the clock before it exists', () => {
    const [research, interview] = flagshipMilestones(NEXT_WEEK, DEFAULT_CADENCE, NY);
    if (!research || !interview) throw new Error('missing milestones');
    expect(milestoneState(research, 'interviewing', NOW)).toBe('done');
    expect(milestoneState(interview, 'interviewing', NOW)).toBe('current');
    expect(milestoneState(research, undefined, NOW)).toBe('upcoming');
    expect(milestoneState(research, undefined, Date.parse('2026-12-02T00:00:00Z'))).toBe('passed');
  });

  it('knows when the planner proposes a week, and which weeks are over', () => {
    expect(momentLabel(planJobMs(NEXT_WEEK, DEFAULT_CADENCE, NY), NY)).toBe('Sun 29 Nov, 17:00');
    expect(isPastWeek('2026-11-16', NOW, NY)).toBe(true);
    expect(isPastWeek('2026-11-23', NOW, NY)).toBe(false);
  });
});
