// @vitest-environment node
import { describe, expect, it } from 'vitest';

import { cadence } from '../testing/fixtures';
import { cadenceProblems, numberList, toCadenceBody, toCadenceDraft, wholeNumber, type CadenceDraft } from './cadence-draft';

function draft(change: Partial<CadenceDraft> = {}): CadenceDraft {
  return { ...toCadenceDraft(cadence().item), ...change };
}

describe('cadence draft', () => {
  it('round-trips the document into the body it saves', () => {
    const item = cadence().item;
    const body = toCadenceBody(toCadenceDraft(item), item);
    expect(body.postSlots).toEqual(item.postSlots);
    expect(body.reminders).toEqual({ replyMinutes: [15, 60, 180], snapshotHours: [48, 168] });
    expect(body.postsPerWeek).toEqual({ normal: 3, light: 2, away: 0, max: 5 });
    expect(body.vetoNotice).toEqual({ day: 'fri', time: '12:00' });
    expect(cadenceProblems(toCadenceDraft(item))).toEqual({});
  });

  it('keeps fields of nested values it does not know (a newer server’s)', () => {
    const base = cadence().item;
    const item = {
      ...base,
      postSlots: [{ day: 'tue' as const, time: '10:00', kind: 'primary' as const, windowMinutes: 120, label: 'kept' }],
      reminders: { replyMinutes: [15], snapshotHours: [48], digestHours: [12] },
    };
    const body = toCadenceBody(toCadenceDraft(item), item);
    expect(body.postSlots[0]).toMatchObject({ label: 'kept', day: 'tue' });
    expect(body.reminders).toMatchObject({ digestHours: [12], replyMinutes: [15] });
  });

  it('reads whole numbers and lists of them', () => {
    expect(wholeNumber(' 42 ')).toBe(42);
    expect(wholeNumber('1.5')).toBeUndefined();
    expect(wholeNumber('')).toBeUndefined();
    expect(numberList('15, 60 ,180')).toEqual([15, 60, 180]);
    expect(numberList('15, x')).toEqual([15, undefined]);
  });

  it('mirrors the server’s calendar rules, by request path', () => {
    expect(cadenceProblems(draft({ businessHoursStart: '18:00', businessHoursEnd: '09:00' }))).toMatchObject({
      businessHoursEnd: 'Business hours must end after they start.',
    });
    expect(cadenceProblems(draft({ vetoNotice: { day: 'fri', time: '20:00' } }))).toMatchObject({
      'vetoNotice.time': 'The veto notice goes out within business hours.',
    });
    expect(cadenceProblems(draft({ vetoNotice: { day: 'sun', time: '12:00' } }))).toMatchObject({
      vetoNotice: 'The veto notice must go out at least 48 hours before the article publishes.',
    });
    expect(cadenceProblems(draft({ postsLight: '4', postsNormal: '3' }))).toMatchObject({
      postsPerWeek: 'Posts per week must be light ≤ normal ≤ maximum.',
    });
    expect(cadenceProblems(draft({ timezone: 'Mars/Olympus' }))).toHaveProperty('timezone');
    expect(cadenceProblems(draft({ stageLeadMinutes: '5' }))).toMatchObject({ stageLeadMinutes: 'Between 15 and 240.' });
  });

  it('refuses two slots on one day while same-day posts are blocked, and the same slot twice', () => {
    const slots = draft().postSlots;
    const sameDay = [...slots, { key: 'x', day: 'tue' as const, time: '15:00', kind: 'optional' as const, windowMinutes: '60' }];
    expect(cadenceProblems(draft({ postSlots: sameDay }))).toMatchObject({
      'postSlots.5.day': 'Two slots on one day while two posts on a day are blocked.',
    });
    expect(cadenceProblems(draft({ postSlots: sameDay, sameDayBlock: false }))).toEqual({});
    const twice = [...slots, { key: 'y', day: 'tue' as const, time: '10:00', kind: 'primary' as const, windowMinutes: '120' }];
    expect(cadenceProblems(draft({ postSlots: twice, sameDayBlock: false }))).toMatchObject({ 'postSlots.5': 'Listed twice.' });
  });

  it('checks the reminder offsets one by one', () => {
    expect(cadenceProblems(draft({ replyMinutes: '15, 15' }))).toMatchObject({ 'reminders.replyMinutes.1': 'Listed twice.' });
    expect(cadenceProblems(draft({ snapshotHours: '48, 900' }))).toMatchObject({
      'reminders.snapshotHours.1': 'Each between 1 and 720.',
    });
    expect(cadenceProblems(draft({ replyMinutes: '1, 2, 3, 4, 5, 6, 7' }))).toMatchObject({
      'reminders.replyMinutes': 'At most 6 reminders.',
    });
  });
});
