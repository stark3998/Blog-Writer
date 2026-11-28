// @vitest-environment node
import { describe, expect, it } from 'vitest';

import { brandToBody, brandToDraft, validateBrand } from './brand/model';
import { formatsToBody, formatsToDraft, validateFormats } from './formats/model';
import { lanesToBody, lanesToDraft, validateLanes } from './pillars/lanes-model';
import { draftToBody, newPillar, pillarsToDraft, shareTotal, validatePillars, type PillarsDraft } from './pillars/model';
import { formToPovIn, formToStoryIn, mergeQueue, storyToForm, validateStory } from './stories/model';
import { brandKit, formatPacks, hex, laneSettings, pillar, pillarSettings, pov, story, styleGuide } from './testing/fixtures';
import { guideToBody, guideToDraft, rulesChange, rulesOf, validateGuide } from './voice/model';

/** The item at `index`, or a failed test. */
function at<T>(items: readonly T[], index: number): T {
  const item = items[index];
  if (item === undefined) throw new Error(`no item at ${String(index)}`);
  return item;
}

function withPillars(draft: PillarsDraft, change: (pillars: PillarsDraft['pillars']) => PillarsDraft['pillars']): PillarsDraft {
  return { pillars: change(draft.pillars) };
}

describe('pillars', () => {
  it('round-trips the server’s pillars, unknown fields included', () => {
    const settings = pillarSettings([pillar({ futureField: 'kept' })]);
    const body = draftToBody(pillarsToDraft(settings));
    expect(body.pillars[0]).toMatchObject({
      id: 'agents',
      targetShare: 0.34,
      renames: { 'Azure Sentinel': 'Microsoft Sentinel' },
      futureField: 'kept',
    });
  });

  it('accepts the defaults and refuses shares above 100%', () => {
    const draft = pillarsToDraft(pillarSettings());
    expect(validatePillars(draft)).toEqual({});
    expect(shareTotal(draft.pillars)).toBeCloseTo(1);
    const over = withPillars(draft, (pillars) => pillars.map((item, index) => (index === 0 ? { ...item, share: '50' } : item)));
    expect(validatePillars(over).pillars).toBe('The target shares add up to more than 100%.');
  });

  it('points at the exact field: ids, hashtags, sites and renames', () => {
    const draft = pillarsToDraft(pillarSettings());
    const broken = withPillars(draft, (pillars) => [
      { ...at(pillars, 0), hashtags: ['AISecurity', 'aisecurity'], researchDomains: ['not a site'] },
      { ...at(pillars, 1), hashtags: ['aisecurity'], renames: [{ from: 'Azure AD', to: 'Entra ID' }, { from: 'Entra ID', to: 'Microsoft Entra ID' }] },
      { ...newPillar(), name: 'Data', id: 'Data!', share: '0' },
    ]);
    const errors = validatePillars(broken);
    expect(errors['pillars.0.hashtags.1']).toBe('Listed twice.');
    expect(errors['pillars.1.hashtags.0']).toBe('Another pillar spells this hashtag with different capitals.');
    expect(errors['pillars.0.researchDomains.0']).toMatch(/site name/);
    expect(errors['pillars.1.renames.0']).toBe('The current name is renamed again. Rename once, to the final name.');
    expect(errors['pillars.2.id']).toMatch(/Lower-case letters/);
  });

  it('drops empty rename rows on save', () => {
    const draft = pillarsToDraft(pillarSettings([pillar({ renames: {} })]));
    const edited = withPillars(draft, (pillars) => [{ ...at(pillars, 0), renames: [{ from: ' ', to: '' }, { from: 'Azure AD', to: 'Microsoft Entra ID' }] }]);
    expect(validatePillars(edited)).toEqual({});
    expect(draftToBody(edited).pillars[0]?.renames).toEqual({ 'Azure AD': 'Microsoft Entra ID' });
  });
});

describe('lanes', () => {
  it('round-trips every field and checks ranges and order', () => {
    const draft = lanesToDraft(laneSettings());
    expect(lanesToBody(draft)).toMatchObject({ onPillarMinShare: 0.8, quickTake: { minChars: 1200 }, series: [{ id: 'okta-to-entra' }] });
    expect(validateLanes(draft, new Set(['identity']))).toEqual({});
    const broken = { ...draft, hookVariants: '9', quickTake: { ...draft.quickTake, minChars: '1600' }, series: [{ ...at(draft.series, 0), pillar: 'gone' }] };
    expect(validateLanes(broken, new Set(['identity']))).toMatchObject({
      hookVariants: 'Between 1 and 5.',
      'quickTake.minChars': 'The minimum is above the maximum.',
      'series.0.pillar': 'Not one of your pillars.',
    });
  });
});

describe('formats', () => {
  it('round-trips and refuses a range that runs backwards', () => {
    const draft = formatsToDraft(formatPacks());
    expect(formatsToBody(draft).packs[0]).toMatchObject({ id: 'case-study', minWords: 1500, maxWords: 2500 });
    const backwards = { packs: draft.packs.map((pack, index) => (index === 0 ? { ...pack, maxWords: '1000' } : pack)) };
    expect(validateFormats(backwards)['packs.0.maxWords']).toBe('The longest is shorter than the shortest.');
  });
});

describe('brand', () => {
  it('sends exactly BrandIn, colours normalized, briefs for current pillars only', () => {
    const draft = brandToDraft(brandKit({ artBriefs: { agents: 'Isometric line art', gone: 'Old' } }));
    const body = brandToBody({ ...draft, colors: { ...draft.colors, ink: '0e2a24' } }, ['agents', 'identity']);
    expect(Object.keys(body).sort()).toEqual(
      ['artBriefs', 'authorHeadline', 'authorName', 'colors', 'font', 'headshot', 'nameMark', 'siteName'].sort(),
    );
    expect(body.colors.ink).toBe(hex('0E2A24'));
    expect(body.artBriefs).toEqual({ agents: 'Isometric line art' });
    expect(body.siteName).toBeNull();
  });

  it('flags an unreadable colour and a failing pair', () => {
    const draft = brandToDraft(brandKit());
    const errors = validateBrand({ ...draft, colors: { ...draft.colors, paper: hex('333333'), rule: 'grey' } });
    expect(errors['colors.rule']).toMatch(/six hex digits/);
    expect(errors.colors).toMatch(/below 4.5:1/);
  });
});

describe('stories', () => {
  it('round-trips a story and drops empty metric rows', () => {
    const form = storyToForm(story());
    const body = formToStoryIn({ ...form, metrics: [...form.metrics, { label: '', value: '', bucketed: true, publicSource: '' }] });
    expect(body.metrics).toEqual([{ label: 'Apps migrated', value: '2,000+', bucketed: true, publicSource: null }]);
    expect(validateStory({ ...form, pillars: [] }).pillars).toBe('Pick at least one pillar.');
    expect(validateStory({ ...form, tags: ['Not a tag'] })['tags.0']).toMatch(/lower-case/);
  });

  it('keeps an idea take tied to its idea', () => {
    const take = pov({ origin: 'idea_take', ideaId: 'idea-1' });
    expect(formToPovIn({ pillar: 'agents', statement: ' Mine ', context: '', confidentiality: 'public' }, take)).toEqual({
      pillar: 'agents',
      statement: 'Mine',
      context: null,
      confidentiality: 'public',
      origin: 'idea_take',
      ideaId: 'idea-1',
    });
  });

  it('queues the newest proposals first across kinds', () => {
    const queue = mergeQueue(
      [{ item: story({ id: 'story-old', updatedAt: '2026-11-01T00:00:00.000000Z' }), etag: '"a"' }],
      [{ item: pov({ id: 'pov-new', updatedAt: '2026-11-02T00:00:00.000000Z' }), etag: '"b"' }],
    );
    expect(queue.map((entry) => entry.entry.item.id)).toEqual(['pov-new', 'story-old']);
  });
});

describe('voice guide', () => {
  it('sends only the known rules and lists what a version changes', () => {
    const draft = guideToDraft(styleGuide());
    expect(guideToBody(draft).rules).toEqual({
      prefer: ['Lead with the decision'],
      avoid: ['Rhetorical questions'],
      bannedPhrases: ['game changer'],
      vocabulary: {},
    });
    const change = rulesChange(rulesOf(styleGuide().rules), { ...draft.rules, prefer: ['Open with the outcome'] });
    expect(change.prefer).toEqual({ added: ['Open with the outcome'], removed: ['Lead with the decision'] });
    expect(validateGuide({ ...draft, body: '  ' }).body).toBe('The guide cannot be empty.');
  });
});
