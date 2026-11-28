// @vitest-environment node
import { describe, expect, it } from 'vitest';

import {
  articleFixture,
  illustrationFixture,
  mediaFixture,
  modelsViewFixture,
  SAMPLE_MARKDOWN,
  titleCardMedia,
} from '../testing/fixtures';

import {
  bodyImageCount,
  candidatesNewestFirst,
  candidateUse,
  droppedReasons,
  droppedSentence,
  estimateUsd,
  generateBlockedReason,
  headingLabel,
  heroOf,
  imagePrices,
  imagesAvailability,
  imagesLockedReason,
  isNewerArticle,
  largeImagesInBody,
  markdownHeadings,
  mediaInBody,
  PREVIEW_INLINE_MAX_BYTES,
  previewLeavesOut,
  runOutcome,
  suggestedHeading,
} from './illustrations';
import type { ArticleStatus } from './types';

describe('the hero', () => {
  it('is the title card until the owner picks an image, even with the pipeline’s copy of the card', () => {
    const card = titleCardMedia();
    expect(heroOf({ media: [] })).toEqual({ image: undefined, card: undefined });
    expect(heroOf({ media: card })).toEqual({ image: undefined, card: card[0] });
  });

  it('is the owner’s hero image when one is set', () => {
    const chosen = { ...illustrationFixture('1').media, role: 'hero_image' as const };
    const view = heroOf({ media: [...titleCardMedia().slice(0, 1), chosen] });
    expect(view.image).toEqual(chosen);
    expect(view.card?.role).toBe('og_image');
  });

  it('knows whether a candidate is the hero or in the draft', () => {
    const one = illustrationFixture('1');
    const two = illustrationFixture('2');
    const article = articleFixture({
      media: [{ ...one.media, role: 'hero_image' }],
      markdown: `${SAMPLE_MARKDOWN}\n![A lock](/api/media/${two.media.name})\n`,
    });
    expect(candidateUse(article, one)).toEqual({ hero: true, inBody: false });
    expect(candidateUse(article, two)).toEqual({ hero: false, inBody: true });
    expect(candidateUse(article, illustrationFixture('3'))).toEqual({ hero: false, inBody: false });
    expect(mediaInBody(article.markdown, two.media.name)).toBe(true);
    expect(bodyImageCount(article.markdown)).toBe(1);
    expect(bodyImageCount('![a \\] b](/api/media/x.png) and ![c](/api/media/y.png)')).toBe(2);
  });

  it('lists the newest run first and keeps a run’s hero first', () => {
    const older = illustrationFixture('1', { createdAt: '2026-11-25T10:00:00.000000Z' });
    const hero = illustrationFixture('2', { role: 'hero' });
    const inline = illustrationFixture('3');
    expect(candidatesNewestFirst([older, hero, inline]).map((item) => item.id)).toEqual([hero.id, inline.id, older.id]);
  });

  it('knows which images the preview frame leaves out', () => {
    expect(previewLeavesOut(mediaFixture({ sizeBytes: PREVIEW_INLINE_MAX_BYTES }))).toBe(false);
    expect(previewLeavesOut(mediaFixture({ sizeBytes: PREVIEW_INLINE_MAX_BYTES + 1 }))).toBe(true);
    expect(previewLeavesOut(mediaFixture({ contentType: 'application/pdf' }))).toBe(true);
    const big = illustrationFixture('2', { media: mediaFixture({ sha256: '2'.repeat(64), sizeBytes: 3_000_000 }) });
    const small = illustrationFixture('3');
    const markdown = `${SAMPLE_MARKDOWN}\n![a](/api/media/${big.media.name})\n\n![b](/api/media/${small.media.name})\n`;
    expect(largeImagesInBody({ illustrations: [big, small], markdown })).toBe(1);
  });
});

describe('headings an image can follow', () => {
  it('reads the h2 to h4 headings like the server, outside fenced code', () => {
    const markdown = [
      '# Not a section',
      '## Why agents need identity',
      'Text.',
      '```bash',
      '## not a heading',
      '```',
      '### Least privilege ###',
      '~~~~',
      '#### hidden',
      '~~~',
      '~~~~',
      '#### Short-lived tokens',
      '##### Too deep',
      '   ## Indented three',
      '    ## Code by indentation',
      '##Missing space',
    ].join('\n');
    expect(markdownHeadings(markdown).map(({ level, source }) => [level, source])).toEqual([
      [2, 'Why agents need identity'],
      [3, 'Least privilege'],
      [4, 'Short-lived tokens'],
      [2, 'Indented three'],
    ]);
  });

  it('shows a heading the way a reader sees it, and keeps what the server matches', () => {
    const [heading] = markdownHeadings('## Why `MCP` needs **owners** \\*now\\* & [links](https://example.org) too\n');
    expect(heading?.source).toBe('Why `MCP` needs **owners** \\*now\\* & [links](https://example.org) too');
    expect(heading?.label).toBe('Why MCP needs owners *now* & links too');
    expect(headingLabel('snake_case and _emphasis_ &amp; more')).toBe('snake_case and emphasis & more');
  });

  it('finds the heading the plan suggested, ignoring case and markup', () => {
    const headings = markdownHeadings('## Why agents need **identity**\n\n## What to do first\n');
    expect(suggestedHeading(headings, 'why agents need identity')).toBe(0);
    expect(suggestedHeading(headings, 'What to do first')).toBe(1);
    expect(suggestedHeading(headings, 'A heading that went away')).toBeUndefined();
    expect(suggestedHeading(headings, null)).toBeUndefined();
  });
});

describe('when images can be made and changed', () => {
  it('needs an image route first, then the owner’s switch', () => {
    expect(imagesAvailability(modelsViewFixture())).toBe('ready');
    expect(imagesAvailability(modelsViewFixture({ imagesEnabled: false }))).toBe('disabled');
    expect(imagesAvailability(modelsViewFixture({ imageRoute: false }))).toBe('unavailable');
    expect(imagesAvailability(modelsViewFixture({ imageRoute: false, imagesEnabled: false }))).toBe('unavailable');
  });

  it('prices a run from the ledger’s per-image prices', () => {
    const prices = imagePrices(modelsViewFixture());
    expect(prices).toEqual({ hero: 0.25, inline: 0.17 });
    expect(estimateUsd({ hero: 0.25, inline: 0.17 }, 1)).toBeCloseTo(0.25);
    expect(estimateUsd({ hero: 0.25, inline: 0.17 }, 3)).toBeCloseTo(0.59);
    expect(imagePrices(modelsViewFixture({ imageRoute: false }))).toBeUndefined();
  });

  it('starts a run only from review, with a title', () => {
    expect(generateBlockedReason(articleFixture(), 'owner')).toBeUndefined();
    expect(generateBlockedReason(articleFixture({ status: 'needs_attention' }), 'owner')).toBeUndefined();
    expect(generateBlockedReason(articleFixture({ status: 'planned' }), 'owner')).toMatch(/Draft the article first/);
    expect(generateBlockedReason(articleFixture({ status: 'approved' }), 'none')).toMatch(/Pull it back to review/);
    expect(generateBlockedReason(articleFixture(), undefined)).toMatch(/Loading/);
    const untitled = articleFixture({ meta: { ...articleFixture().meta, title: '  ' } });
    expect(generateBlockedReason(untitled, 'owner')).toMatch(/title/);
  });

  it('explains every status where the images are locked', () => {
    const locked: [ArticleStatus, 'pipeline' | 'publish' | 'none'][] = [
      ['researching', 'pipeline'],
      ['drafting', 'pipeline'],
      ['approved', 'none'],
      ['publishing', 'publish'],
      ['published', 'none'],
      ['publish_failed', 'none'],
      ['unpublished', 'none'],
      ['archived', 'none'],
    ];
    for (const [status, writer] of locked) expect(imagesLockedReason(status, writer)).toBeTruthy();
    expect(imagesLockedReason('in_review', 'owner')).toBeUndefined();
    expect(imagesLockedReason('approved', undefined)).toBeUndefined();
  });

  it('orders copies of the article by version, then by when they were saved', () => {
    const at = (version: number, updatedAt: string) => ({ version, updatedAt });
    expect(isNewerArticle(at(8, '2026-11-26T15:00:00.000000Z'), at(7, '2026-11-26T16:00:00.000000Z'))).toBe(true);
    expect(isNewerArticle(at(7, '2026-11-26T16:00:00.000000Z'), at(7, '2026-11-26T15:00:00.000000Z'))).toBe(true);
    expect(isNewerArticle(at(7, '2026-11-26T15:00:00.000000Z'), at(7, '2026-11-26T15:00:00.000000Z'))).toBe(false);
  });
});

describe('how a run ended', () => {
  const event = (seq: number, step: string | null, data: Record<string, unknown>, type = 'progress') => ({
    seq,
    at: '2026-11-26T16:00:00.000000Z',
    type,
    step,
    data,
  });

  it('reads what the last step kept, and why it dropped the others', () => {
    expect(runOutcome([])).toBeUndefined();
    const events = [
      event(1, 'generate', { done: 3, total: 3, label: 'images made' }),
      event(2, 'attach', { done: 1, total: 3, label: 'images kept', reasons: ['legible_text', 7, 'person'] }),
      event(3, 'attach', { entity: 'article' }, 'entity.changed'),
    ];
    expect(runOutcome(events)).toEqual({ kept: 1, planned: 3, reasons: ['legible_text', 'person'] });
    expect(runOutcome([event(1, 'attach', { done: 4, total: 3 })])).toBeUndefined();
    expect(runOutcome([event(1, 'attach', { done: 'two', total: 3 })])).toBeUndefined();
  });

  it('says why images were dropped in words, each reason once', () => {
    expect(droppedReasons(['legible_text', 'legible_text', 'branding', 'interrupted', 'media_integrity'])).toEqual([
      'text in the image',
      'a logo or branding',
      'a technical problem',
    ]);
    expect(droppedSentence(undefined)).toBeUndefined();
    expect(droppedSentence({ kept: 3, planned: 3, reasons: [] })).toBeUndefined();
    expect(droppedSentence({ kept: 2, planned: 3, reasons: ['content_filter'] })).toBe(
      'Kept 2 of 3: one didn’t pass the checks (the image model’s safety filter).',
    );
    expect(droppedSentence({ kept: 1, planned: 3, reasons: [] })).toBe('Kept 1 of 3: 2 didn’t pass the checks.');
  });
});
