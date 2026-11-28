import type { ErrorMap } from '../lib/errors';
import { parsePercent, parseWhole, percentText, wholeError } from '../lib/rules';
import type { LaneSettings, LanesIn, SeriesDef } from '../lib/types';

/** LinkedIn's commentary limit; lane rules cannot ask for more (`kb.checks.MAX_POST_CHARS`). */
const MAX_POST_CHARS = 3000;

export interface SeriesDraft {
  readonly key: string;
  readonly id: string;
  readonly pillar: string;
  readonly title: string;
  readonly active: boolean;
  readonly extra: Readonly<Record<string, unknown>>;
}

/** `LaneSettings` as the editor holds it: numbers as the text typed. */
export interface LanesDraft {
  readonly derivativesMaxPerWeek: string;
  readonly nativeMinPerWeek: string;
  readonly nativeOtherPillar: boolean;
  readonly onPillarMinShare: string;
  readonly hookVariants: string;
  readonly quickTake: {
    readonly minChars: string;
    readonly maxChars: string;
    readonly maxAgeHours: string;
    readonly ownerTakeRequired: boolean;
  };
  readonly formats: {
    readonly carouselMinSlides: string;
    readonly carouselMaxSlides: string;
    readonly textMinChars: string;
    readonly textMaxChars: string;
    readonly imageCardEnabled: boolean;
    readonly pollMaxPerMonth: string;
  };
  readonly repurpose: {
    readonly minWeeks: string;
    readonly maxWeeks: string;
    readonly requiresNoveltyCheck: boolean;
  };
  readonly series: readonly SeriesDraft[];
  /** Fields of the nested rules this app does not know, sent back unchanged. */
  readonly extra: {
    readonly quickTake: Readonly<Record<string, unknown>>;
    readonly formats: Readonly<Record<string, unknown>>;
    readonly repurpose: Readonly<Record<string, unknown>>;
  };
}

function rest(value: object | undefined, known: readonly string[]): Record<string, unknown> {
  return Object.fromEntries(Object.entries(value ?? {}).filter(([field]) => !known.includes(field)));
}

const QUICK_TAKE = ['minChars', 'maxChars', 'maxAgeHours', 'ownerTakeRequired'] as const;
const FORMATS = [
  'carouselMinSlides',
  'carouselMaxSlides',
  'textMinChars',
  'textMaxChars',
  'imageCardEnabled',
  'pollMaxPerMonth',
] as const;
const REPURPOSE = ['minWeeks', 'maxWeeks', 'requiresNoveltyCheck'] as const;

function seriesToDraft(series: SeriesDef): SeriesDraft {
  return {
    key: `saved:${series.id}`,
    id: series.id,
    pillar: series.pillar,
    title: series.title,
    active: series.active,
    extra: rest(series, ['id', 'pillar', 'title', 'active']),
  };
}

/** The editor's starting point from `GET /api/kb/lanes`; the defaults fill what is missing. */
export function lanesToDraft(lanes: LaneSettings): LanesDraft {
  const quick = lanes.quickTake;
  const formats = lanes.formats;
  const repurpose = lanes.repurpose;
  return {
    derivativesMaxPerWeek: String(lanes.derivativesMaxPerWeek),
    nativeMinPerWeek: String(lanes.nativeMinPerWeek),
    nativeOtherPillar: lanes.nativeOtherPillar,
    onPillarMinShare: percentText(lanes.onPillarMinShare),
    hookVariants: String(lanes.hookVariants),
    quickTake: {
      minChars: String(quick?.minChars ?? 1200),
      maxChars: String(quick?.maxChars ?? 1500),
      maxAgeHours: String(quick?.maxAgeHours ?? 72),
      ownerTakeRequired: quick?.ownerTakeRequired ?? true,
    },
    formats: {
      carouselMinSlides: String(formats?.carouselMinSlides ?? 8),
      carouselMaxSlides: String(formats?.carouselMaxSlides ?? 12),
      textMinChars: String(formats?.textMinChars ?? 1200),
      textMaxChars: String(formats?.textMaxChars ?? 2000),
      imageCardEnabled: formats?.imageCardEnabled ?? true,
      pollMaxPerMonth: String(formats?.pollMaxPerMonth ?? 1),
    },
    repurpose: {
      minWeeks: String(repurpose?.minWeeks ?? 8),
      maxWeeks: String(repurpose?.maxWeeks ?? 12),
      requiresNoveltyCheck: repurpose?.requiresNoveltyCheck ?? true,
    },
    series: (lanes.series ?? []).map(seriesToDraft),
    extra: {
      quickTake: rest(quick, QUICK_TAKE),
      formats: rest(formats, FORMATS),
      repurpose: rest(repurpose, REPURPOSE),
    },
  };
}

function whole(text: string): number {
  return parseWhole(text) ?? 0;
}

/** `PUT /api/kb/lanes`: every field (`LanesIn`). */
export function lanesToBody(draft: LanesDraft): LanesIn {
  return {
    derivativesMaxPerWeek: whole(draft.derivativesMaxPerWeek),
    nativeMinPerWeek: whole(draft.nativeMinPerWeek),
    nativeOtherPillar: draft.nativeOtherPillar,
    onPillarMinShare: parsePercent(draft.onPillarMinShare) ?? 0,
    hookVariants: whole(draft.hookVariants),
    quickTake: {
      ...draft.extra.quickTake,
      minChars: whole(draft.quickTake.minChars),
      maxChars: whole(draft.quickTake.maxChars),
      maxAgeHours: whole(draft.quickTake.maxAgeHours),
      ownerTakeRequired: draft.quickTake.ownerTakeRequired,
    },
    formats: {
      ...draft.extra.formats,
      carouselMinSlides: whole(draft.formats.carouselMinSlides),
      carouselMaxSlides: whole(draft.formats.carouselMaxSlides),
      textMinChars: whole(draft.formats.textMinChars),
      textMaxChars: whole(draft.formats.textMaxChars),
      imageCardEnabled: draft.formats.imageCardEnabled,
      pollMaxPerMonth: whole(draft.formats.pollMaxPerMonth),
    },
    repurpose: {
      ...draft.extra.repurpose,
      minWeeks: whole(draft.repurpose.minWeeks),
      maxWeeks: whole(draft.repurpose.maxWeeks),
      requiresNoveltyCheck: draft.repurpose.requiresNoveltyCheck,
    },
    series: draft.series.map((series) => ({
      ...series.extra,
      id: series.id,
      pillar: series.pillar,
      title: series.title.trim(),
      active: series.active,
    })),
  };
}

/** What `kb.checks.lane_problems` and the model would refuse, by request path. */
export function validateLanes(draft: LanesDraft, pillarIds: ReadonlySet<string>): ErrorMap {
  const errors: Record<string, string> = {};
  const set = (path: string, message: string | undefined) => {
    if (message !== undefined && !(path in errors)) errors[path] = message;
  };
  const range = (path: string, text: string, min: number, max: number) => {
    set(path, wholeError(text, min, max));
  };
  const ordered = (path: string, low: string, high: string) => {
    const a = parseWhole(low);
    const b = parseWhole(high);
    if (a !== undefined && b !== undefined && a > b) set(path, 'The minimum is above the maximum.');
  };

  range('derivativesMaxPerWeek', draft.derivativesMaxPerWeek, 0, 7);
  range('nativeMinPerWeek', draft.nativeMinPerWeek, 0, 7);
  range('hookVariants', draft.hookVariants, 1, 5);
  const share = parsePercent(draft.onPillarMinShare);
  if (share === undefined || share > 1) set('onPillarMinShare', 'Enter a percentage from 0 to 100.');

  range('quickTake.minChars', draft.quickTake.minChars, 1, MAX_POST_CHARS);
  range('quickTake.maxChars', draft.quickTake.maxChars, 1, MAX_POST_CHARS);
  ordered('quickTake.minChars', draft.quickTake.minChars, draft.quickTake.maxChars);
  range('quickTake.maxAgeHours', draft.quickTake.maxAgeHours, 1, 168);

  range('formats.carouselMinSlides', draft.formats.carouselMinSlides, 2, 20);
  range('formats.carouselMaxSlides', draft.formats.carouselMaxSlides, 2, 20);
  ordered('formats.carouselMinSlides', draft.formats.carouselMinSlides, draft.formats.carouselMaxSlides);
  range('formats.textMinChars', draft.formats.textMinChars, 1, MAX_POST_CHARS);
  range('formats.textMaxChars', draft.formats.textMaxChars, 1, MAX_POST_CHARS);
  ordered('formats.textMinChars', draft.formats.textMinChars, draft.formats.textMaxChars);
  range('formats.pollMaxPerMonth', draft.formats.pollMaxPerMonth, 0, 31);

  range('repurpose.minWeeks', draft.repurpose.minWeeks, 1, 104);
  range('repurpose.maxWeeks', draft.repurpose.maxWeeks, 1, 104);
  ordered('repurpose.minWeeks', draft.repurpose.minWeeks, draft.repurpose.maxWeeks);

  if (draft.series.length > 50) set('series', 'At most 50 series.');
  draft.series.forEach((series, index) => {
    const base = `series.${String(index)}`;
    const title = series.title.trim();
    if (title === '') set(`${base}.title`, 'Give the series a title.');
    else if (title.length > 150) set(`${base}.title`, 'At most 150 characters.');
    if (pillarIds.size > 0 && !pillarIds.has(series.pillar)) set(`${base}.pillar`, 'Not one of your pillars.');
  });
  return errors;
}

/** Plain text of the lane rules, for the conflict dialog's comparison. */
export function describeLanes(draft: LanesDraft): string {
  return [
    `Derivatives per week: at most ${draft.derivativesMaxPerWeek}. Native posts per week: at least ${draft.nativeMinPerWeek}${draft.nativeOtherPillar ? ', on another pillar' : ''}.`,
    `On-pillar share: at least ${draft.onPillarMinShare}%. Hook variants: ${draft.hookVariants}.`,
    `Quick takes: ${draft.quickTake.minChars}-${draft.quickTake.maxChars} characters, within ${draft.quickTake.maxAgeHours} hours${draft.quickTake.ownerTakeRequired ? ', your take required' : ''}.`,
    `Carousels: ${draft.formats.carouselMinSlides}-${draft.formats.carouselMaxSlides} slides. Text posts: ${draft.formats.textMinChars}-${draft.formats.textMaxChars} characters. Image cards ${draft.formats.imageCardEnabled ? 'on' : 'off'}. Polls: ${draft.formats.pollMaxPerMonth} a month.`,
    `Repurposing: after ${draft.repurpose.minWeeks}-${draft.repurpose.maxWeeks} weeks${draft.repurpose.requiresNoveltyCheck ? ', with a novelty check' : ''}.`,
    ...draft.series.map((series) => `Series "${series.title}" (${series.pillar})${series.active ? '' : ', paused'}`),
  ].join('\n\n');
}
