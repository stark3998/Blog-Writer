import type { components } from '@/lib/api/schema';
import { PILLARS } from '@/lib/pillars';

import { DEFAULT_TIMEZONE, isKnownZone, type WeekdayId } from './time';

/**
 * The settings the calendar reads (`/api/kb/cadence`, `/api/kb/lanes`, `/api/kb/pillars`,
 * `/api/kb/formats`), with every optional field filled in from the server's defaults
 * (`blogwriter.domain.settings`), so a view can render before the owner ever saved them, and
 * still render (with a note) when they could not be loaded.
 */

type Schemas = components['schemas'];
export type CadenceSettings = Schemas['CadenceSettings'];
export type LaneSettings = Schemas['LaneSettings'];
export type PillarSettings = Schemas['PillarSettings'];
export type FormatPacks = Schemas['FormatPacks'];
export type WeekMode = Schemas['WeekMode'];

export interface WeeklyTime {
  readonly day: WeekdayId;
  readonly time: string;
}

export interface SlotTemplate {
  readonly day: WeekdayId;
  readonly time: string;
  readonly kind: 'primary' | 'optional';
}

export interface CadenceView {
  readonly timezone: string;
  readonly planWeek: WeeklyTime;
  readonly planLeadMinutes: number;
  readonly researchStart: WeeklyTime;
  readonly interviewDue: WeeklyTime;
  readonly draftDay: WeeklyTime;
  readonly vetoNotice: WeeklyTime;
  readonly articlePublish: WeeklyTime;
  readonly minVetoLeadHours: number;
  readonly postSlots: readonly SlotTemplate[];
  readonly postsPerWeek: { readonly normal: number; readonly light: number; readonly away: number; readonly max: number };
  readonly sameDayBlock: boolean;
  readonly minGapHoursWarn: number;
  readonly missedSlotGraceMinutes: number;
  readonly defaultMode: WeekMode;
}

export const DEFAULT_CADENCE: CadenceView = {
  timezone: DEFAULT_TIMEZONE,
  planWeek: { day: 'sun', time: '18:00' },
  planLeadMinutes: 60,
  researchStart: { day: 'mon', time: '08:00' },
  interviewDue: { day: 'wed', time: '18:00' },
  draftDay: { day: 'thu', time: '09:00' },
  vetoNotice: { day: 'fri', time: '12:00' },
  articlePublish: { day: 'mon', time: '09:00' },
  minVetoLeadHours: 48,
  postSlots: [
    { day: 'tue', time: '10:00', kind: 'primary' },
    { day: 'wed', time: '10:00', kind: 'primary' },
    { day: 'thu', time: '10:00', kind: 'primary' },
    { day: 'mon', time: '12:00', kind: 'optional' },
    { day: 'fri', time: '15:00', kind: 'optional' },
  ],
  postsPerWeek: { normal: 3, light: 2, away: 0, max: 5 },
  sameDayBlock: true,
  minGapHoursWarn: 24,
  missedSlotGraceMinutes: 120,
  defaultMode: 'normal',
};

function weekly(value: Schemas['WeeklyTime'] | undefined, fallback: WeeklyTime): WeeklyTime {
  return value ? { day: value.day, time: value.time } : fallback;
}

/** The cadence with defaults; an unknown zone falls back to New York so labels never throw. */
export function cadenceView(settings: CadenceSettings | undefined): CadenceView {
  if (!settings) return DEFAULT_CADENCE;
  const perWeek = settings.postsPerWeek;
  return {
    timezone: isKnownZone(settings.timezone) ? settings.timezone : DEFAULT_TIMEZONE,
    planWeek: weekly(settings.planWeek, DEFAULT_CADENCE.planWeek),
    planLeadMinutes: settings.planLeadMinutes,
    researchStart: weekly(settings.researchStart, DEFAULT_CADENCE.researchStart),
    interviewDue: weekly(settings.interviewDue, DEFAULT_CADENCE.interviewDue),
    draftDay: weekly(settings.draftDay, DEFAULT_CADENCE.draftDay),
    vetoNotice: weekly(settings.vetoNotice, DEFAULT_CADENCE.vetoNotice),
    articlePublish: weekly(settings.articlePublish, DEFAULT_CADENCE.articlePublish),
    minVetoLeadHours: settings.minVetoLeadHours,
    postSlots: settings.postSlots
      ? settings.postSlots.map((slot) => ({ day: slot.day, time: slot.time, kind: slot.kind }))
      : DEFAULT_CADENCE.postSlots,
    postsPerWeek: perWeek
      ? { normal: perWeek.normal, light: perWeek.light, away: perWeek.away, max: perWeek.max }
      : DEFAULT_CADENCE.postsPerWeek,
    sameDayBlock: settings.sameDayBlock,
    minGapHoursWarn: settings.minGapHoursWarn,
    missedSlotGraceMinutes: settings.missedSlotGraceMinutes,
    defaultMode: settings.defaultMode,
  };
}

export interface LanesView {
  readonly onPillarMinShare: number;
  readonly derivativesMaxPerWeek: number;
  readonly nativeMinPerWeek: number;
  readonly quickTake: { readonly minChars: number; readonly maxChars: number; readonly maxAgeHours: number };
  readonly imageCardEnabled: boolean;
}

export const DEFAULT_LANES: LanesView = {
  onPillarMinShare: 0.8,
  derivativesMaxPerWeek: 2,
  nativeMinPerWeek: 1,
  quickTake: { minChars: 1200, maxChars: 1500, maxAgeHours: 72 },
  imageCardEnabled: true,
};

export function lanesView(settings: LaneSettings | undefined): LanesView {
  if (!settings) return DEFAULT_LANES;
  return {
    onPillarMinShare: settings.onPillarMinShare,
    derivativesMaxPerWeek: settings.derivativesMaxPerWeek,
    nativeMinPerWeek: settings.nativeMinPerWeek,
    quickTake: settings.quickTake
      ? {
          minChars: settings.quickTake.minChars,
          maxChars: settings.quickTake.maxChars,
          maxAgeHours: settings.quickTake.maxAgeHours,
        }
      : DEFAULT_LANES.quickTake,
    imageCardEnabled: settings.formats?.imageCardEnabled ?? true,
  };
}

export interface PillarView {
  readonly id: string;
  readonly name: string;
  readonly topics: string;
  readonly targetShare: number;
}

/** The owner's pillars (1-5); the plan's three until the settings load. */
export function pillarViews(settings: PillarSettings | undefined): readonly PillarView[] {
  const pillars = settings?.pillars;
  if (!pillars || pillars.length === 0) {
    return PILLARS.map((pillar) => ({ id: pillar.id, name: pillar.name, topics: pillar.topics, targetShare: 0.33 }));
  }
  return pillars.map((pillar) => ({
    id: pillar.id,
    name: pillar.name,
    topics: pillar.topics,
    targetShare: pillar.targetShare,
  }));
}

export interface FormatOption {
  readonly id: string;
  readonly name: string;
}

/** The plan's four article formats, until the format packs load. */
export const DEFAULT_ARTICLE_FORMATS: readonly FormatOption[] = [
  { id: 'case-study', name: 'Case study / how I did it' },
  { id: 'framework', name: 'Framework / checklist' },
  { id: 'deep-explainer', name: 'Deep explainer' },
  { id: 'news-analysis', name: 'News analysis' },
];

export function articleFormats(packs: FormatPacks | undefined): readonly FormatOption[] {
  const list = packs?.packs;
  if (!list || list.length === 0) return DEFAULT_ARTICLE_FORMATS;
  return list.map((pack) => ({ id: pack.id, name: pack.name }));
}
