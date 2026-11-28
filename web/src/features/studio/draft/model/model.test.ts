// @vitest-environment node
import { getSchema } from '@tiptap/core';
import { describe, expect, it } from 'vitest';

import { ArticleCodec, createArticleExtensions } from '@/features/editor';
import { SERVER_MACHINES } from '@/test/machines';

import { articleFixture, finding, gateResult, gatesViewFixture, MACHINES, SAMPLE_MARKDOWN } from '../testing/fixtures';

import { citationIds, countWords, outlineOf, readingMinutes } from './document';
import { fromLocalInput, plural } from './format';
import { gateCleared, gateOverridable, gatePassed, gateState, sortFindings, summarizeGates } from './gates';
import { draftFromMeta, metaForSave, metaKey, missingForPublish, parseTags, slugify, validateMetaDraft, EMPTY_META_DRAFT } from './meta';
import { locateSpan, visibleText } from './spans';
import { articleActions, hasEdge, lastErrorMessage, NO_ACTIONS, ownerMove, readOnlyReason, STATUS_LABELS, writerOf } from './status';

const schema = getSchema(createArticleExtensions());

function docOf(markdown: string) {
  const codec = new ArticleCodec();
  try {
    return schema.nodeFromJSON(codec.parse(markdown).doc);
  } finally {
    codec.destroy();
  }
}

describe('status rules', () => {
  it('takes who writes from the server, and keeps the text read-only until it is known', () => {
    expect(writerOf('in_review', MACHINES)).toBe('owner');
    expect(writerOf('drafting', MACHINES)).toBe('pipeline');
    expect(writerOf('publishing', MACHINES)).toBe('publish');
    expect(writerOf('publish_failed', MACHINES)).toBe('none');
    expect(writerOf('in_review', undefined)).toBeUndefined();
    const ownerStates = Object.entries(SERVER_MACHINES.article.writers)
      .filter(([, writer]) => writer === 'owner')
      .map(([status]) => status);
    expect(ownerStates.sort()).toEqual(['in_review', 'needs_attention', 'planned']);
    expect(readOnlyReason('in_review', 'owner')).toBeUndefined();
    expect(readOnlyReason('drafting', 'pipeline')).toMatch(/pipeline/);
    expect(readOnlyReason('publishing', 'publish')).toMatch(/publishing runs/);
    expect(readOnlyReason('publish_failed', 'none')).toMatch(/back to review/);
    expect(readOnlyReason('drafting', undefined)).toBeUndefined();
  });

  it('labels every status with the glossary’s words', () => {
    expect(STATUS_LABELS.gating).toBe('Checking');
    expect(STATUS_LABELS.published).toBe('Live');
    expect(STATUS_LABELS.needs_attention).toBe('Needs you');
    expect(STATUS_LABELS.interviewing).toBe('Interview');
  });

  it('reads edges and the owner’s edges from the machines, and guesses none while they are unknown', () => {
    expect(hasEdge(MACHINES, 'in_review', 'approved')).toBe(true);
    expect(hasEdge(MACHINES, 'archived', 'approved')).toBe(false);
    expect(hasEdge(undefined, 'in_review', 'approved')).toBe(false);
    expect(ownerMove(MACHINES, 'publish_failed', 'in_review')).toBe(true);
    expect(ownerMove(MACHINES, 'publishing', 'published')).toBe(false); // the publish job's edge
    expect(ownerMove(MACHINES, 'drafting', 'gating')).toBe(false); // the pipeline's edge
    expect(ownerMove(undefined, 'approved', 'in_review')).toBe(false);
    expect(articleActions(articleFixture(), undefined)).toEqual(NO_ACTIONS);
  });

  it('offers the actions the state allows', () => {
    const review = articleActions(articleFixture(), MACHINES);
    expect(review).toMatchObject({ approve: true, revise: true, publishNow: true, startPipeline: true, archive: true, ownerEdits: true });
    expect(review.unpublish).toBe(false);

    const live = articleActions(articleFixture({ status: 'published', publish: { live: true, auto: false } }), MACHINES);
    expect(live).toMatchObject({ correct: true, unpublish: true, archive: false, approve: false, ownerEdits: false });

    const drafting = articleActions(articleFixture({ status: 'drafting' }), MACHINES);
    expect(drafting).toMatchObject({ cancelPipeline: true, runGates: false, approve: false });

    const failedUnpublish = articleActions(
      articleFixture({ status: 'publish_failed', publish: { live: true, auto: false, lastOperation: 'unpublish' } }),
      MACHINES,
    );
    expect(failedUnpublish).toMatchObject({ unpublish: true, publishNow: false, backToReview: true });

    const failedPublish = articleActions(
      articleFixture({ status: 'publish_failed', publish: { live: false, auto: false, lastOperation: 'publish' } }),
      MACHINES,
    );
    expect(failedPublish).toMatchObject({ publishNow: true, unpublish: false, backToReview: true, ownerEdits: false });

    const evalArticle = articleActions(articleFixture({ purpose: 'eval' }), MACHINES);
    expect(evalArticle.approve).toBe(false);
    expect(articleActions(articleFixture({ markdown: '  ' }), MACHINES).revise).toBe(false);
    expect(articleActions(articleFixture({ status: 'approved', publish: { live: false, auto: true } }), MACHINES).veto).toBe(true);
  });

  it('follows the server when its owner edges change', () => {
    const article = SERVER_MACHINES.article;
    const narrowed = { article: { ...article, ownerEdges: { ...article.ownerEdges, publish_failed: ['unpublishing'] } } };
    const failed = articleActions(
      articleFixture({ status: 'publish_failed', publish: { live: false, auto: false, lastOperation: 'publish' } }),
      narrowed,
    );
    expect(failed.backToReview).toBe(false);
  });

  it('explains the last error in words', () => {
    expect(lastErrorMessage('cancelled')).toBe('The pipeline run was cancelled.');
    expect(lastErrorMessage('something_new')).toBe('The last run stopped with an error.');
    expect(lastErrorMessage(null)).toBeUndefined();
  });
});

describe('gate rules (domain.articles.GateResult)', () => {
  it('passes on pass and warn, and counts an override only where allowed', () => {
    expect(gatePassed(gateResult('lint', { verdict: 'warn' }))).toBe(true);
    const soft = gateResult('originality', { verdict: 'fail', findings: [finding({ severity: 'block', overridable: true })] });
    expect(gateOverridable(soft)).toBe(true);
    expect(gateCleared({ ...soft, override: { reason: 'Deliberate.', at: '2026-11-26T15:00:00.000000Z' } })).toBe(true);
    expect(gateState({ ...soft, override: { reason: 'Deliberate.', at: '2026-11-26T15:00:00.000000Z' } })).toBe('overridden');

    const hard = gateResult('lint', { verdict: 'fail', findings: [finding({ severity: 'block', overridable: false })] });
    expect(gateOverridable(hard)).toBe(false);
    expect(gateOverridable(gateResult('lint', { verdict: 'error' }))).toBe(false);
    expect(gateOverridable(gateResult('rubric', { verdict: 'skipped' }))).toBe(true);
    expect(gateState(undefined)).toBe('not-run');
  });

  it('sorts blockers first and summarises the report', () => {
    const sorted = sortFindings([
      finding({ severity: 'info', start: 1 }),
      finding({ severity: 'block', start: 50 }),
      finding({ severity: 'block', start: 5 }),
    ]);
    expect(sorted.map((item) => [item.severity, item.start])).toEqual([
      ['block', 5],
      ['block', 50],
      ['info', 1],
    ]);
    expect(summarizeGates(undefined).title).toBe('The gates haven’t run on this article yet.');
    expect(summarizeGates(gatesViewFixture()).title).toBe('All seven gates passed.');
    expect(summarizeGates(gatesViewFixture([], { blocking: ['lint'], manualPublishReady: false, autoPublishReady: false })).detail).toBe(
      'Formatting needs a fix or, where allowed, an override.',
    );
  });
});

describe('article details', () => {
  it('checks fields with the dialect’s rules and allows empty ones while drafting', () => {
    expect(validateMetaDraft(EMPTY_META_DRAFT)).toEqual({});
    const errors = validateMetaDraft({
      ...EMPTY_META_DRAFT,
      title: ' Leading space',
      slug: 'Not A Slug',
      excerpt: 'Has <b>',
      tags: 'agents, Agents!, agents',
      series: { id: '', title: 'Series', part: '0', total: '' },
    });
    expect(errors.title).toMatch(/spaces/);
    expect(errors.slug).toMatch(/lowercase/);
    expect(errors.excerpt).toMatch(/Plain text/);
    expect(errors.tags).toMatch(/Agents!/);
    expect(errors['series.id']).toBe('Give the series an id.');
    expect(errors['series.part']).toMatch(/1 to 9999/);
    expect(validateMetaDraft({ ...EMPTY_META_DRAFT, series: { id: 'run', title: 'Run', part: '3', total: '2' } })['series.total']).toMatch(/lower/);
  });

  it('saves valid fields as typed and keeps the last saved value for invalid ones', () => {
    const saved = articleFixture().meta;
    const draft = { ...draftFromMeta(saved), title: 'New title', slug: 'Bad Slug', tags: 'agents, mcp' };
    const meta = metaForSave(draft, validateMetaDraft(draft), saved);
    expect(meta).toMatchObject({ title: 'New title', slug: 'agent-identity-in-practice', tags: ['agents', 'mcp'], series: null });
    expect(metaKey(meta)).toBe(metaKey({ ...meta }));
    expect(parseTags(' a, ,b ')).toEqual(['a', 'b']);
    expect(missingForPublish({ title: 'T', tags: [] })).toEqual(['Slug', 'Excerpt', 'Pillar', 'Format']);
  });

  it('makes slugs from titles at a word boundary', () => {
    expect(slugify('Least-privilege tool calls: a field guide!')).toBe('least-privilege-tool-calls-a-field-guide');
    expect(slugify('Café & crème')).toBe('cafe-creme');
    expect(slugify('one two three', 8)).toBe('one-two');
  });
});

describe('document facts', () => {
  it('counts prose words like the publish renderer: no code, diagrams or sources', () => {
    const doc = docOf(
      '## Two words\n\nOne two three.\n\n```ts\nconst skipped = true\n```\n\n```mermaid\nflowchart LR\n  a --> b\n```\n\n![alt text here](https://example.com/a.png)\n\nCited.[^src]\n\n[^src]: Not counted at all.\n',
    );
    expect(countWords(doc)).toBe(2 + 3 + 3 + 1);
    expect(readingMinutes(0)).toBe(0);
    expect(readingMinutes(1)).toBe(1);
    expect(readingMinutes(461)).toBe(3);
    expect(citationIds(doc)).toEqual(['src']);
  });

  it('lists the headings for the outline', () => {
    const outline = outlineOf(docOf(SAMPLE_MARKDOWN));
    expect(outline.map((entry) => [entry.level, entry.text])).toEqual([
      [2, 'Why agents need identity'],
      [2, 'What to do first'],
    ]);
  });
});

describe('spans', () => {
  it('reduces Markdown to the text a reader sees', () => {
    expect(visibleText('**Bold** and [a link](https://example.com) with `code`.[^src]')).toBe('Bold and a link with code.');
    expect(visibleText('## Heading\n- item')).toBe('Heading item');
  });

  it('finds a finding’s span in the document by its text and place', () => {
    const doc = docOf(SAMPLE_MARKDOWN);
    const start = SAMPLE_MARKDOWN.indexOf('Short-lived tokens');
    const range = locateSpan(doc, SAMPLE_MARKDOWN, start, start + 'Short-lived tokens'.length);
    expect(range).not.toBeNull();
    if (!range) return;
    expect(doc.textBetween(range.from, range.to)).toBe('Short-lived tokens');
  });

  it('falls back to the block at the same place when the text is not there', () => {
    const doc = docOf(SAMPLE_MARKDOWN);
    const start = SAMPLE_MARKDOWN.indexOf('Start with');
    const range = locateSpan(doc, SAMPLE_MARKDOWN.replace('Start with an inventory', 'Begin by counting'), start, start + 5);
    expect(range).not.toBeNull();
    if (!range) return;
    expect(doc.textBetween(range.from, range.to).length).toBeGreaterThan(0);
  });
});

describe('format', () => {
  it('pluralises with the locale’s digits and reads datetime-local values', () => {
    expect(plural(1, 'word', 'words')).toBe('1 word');
    expect(plural(2, 'word', 'words')).toMatch(/^2 words$/);
    expect(fromLocalInput('not a time')).toBeUndefined();
    expect(fromLocalInput('2026-11-30T09:00')).toMatch(/^2026-11-30T/);
  });
});
