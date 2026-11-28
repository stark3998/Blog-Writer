// @vitest-environment node
import { describe, expect, it } from 'vitest';

import { machinesResponse } from '@/test/machines';

import {
  ARTICLE_STATUS,
  articleWriter,
  firstLine,
  humanize,
  isWorkingStatus,
  lastErrorSentence,
  machineAllows,
  nextActionFor,
  ownerCanMoveArticle,
  piecePath,
  POST_STATUS,
} from './piece-status';
import { MACHINES as TABLES } from './testing/fixtures';

const MACHINES = machinesResponse(TABLES);

describe('status words', () => {
  it('uses the contract glossary for every article and post status', () => {
    expect(Object.fromEntries(Object.entries(ARTICLE_STATUS).map(([status, meta]) => [status, meta.label]))).toEqual({
      planned: 'Planned',
      researching: 'Researching',
      interviewing: 'Interview',
      drafting: 'Drafting',
      gating: 'Checking',
      in_review: 'In review',
      approved: 'Approved',
      publishing: 'Publishing',
      published: 'Live',
      publish_failed: 'Publish failed',
      unpublishing: 'Unpublishing',
      unpublished: 'Unpublished',
      needs_attention: 'Needs you',
      archived: 'Archived',
    });
    expect(Object.fromEntries(Object.entries(POST_STATUS).map(([status, meta]) => [status, meta.label]))).toEqual({
      generating: 'Writing',
      draft: 'Draft',
      approved: 'Approved',
      staged: 'Ready',
      publishing: 'Publishing',
      posted: 'Posted',
      kit: 'Kit',
      needs_attention: 'Needs you',
      missed: 'Missed',
      expired: 'Expired',
      deleted: 'Deleted',
      archived: 'Archived',
    });
  });

  it('keeps the stream open only for statuses a job works in', () => {
    expect(isWorkingStatus('researching', 'article')).toBe(true);
    expect(isWorkingStatus('in_review', 'article')).toBe(false);
    expect(isWorkingStatus('generating', 'post')).toBe(true);
    expect(isWorkingStatus('staged', 'post')).toBe(false);
  });

  it('says who writes an article in each status, as the server says (read-only editor unless owner)', () => {
    expect(articleWriter(MACHINES, 'planned')).toBe('owner');
    expect(articleWriter(MACHINES, 'in_review')).toBe('owner');
    expect(articleWriter(MACHINES, 'needs_attention')).toBe('owner');
    expect(articleWriter(MACHINES, 'drafting')).toBe('pipeline');
    expect(articleWriter(MACHINES, 'publishing')).toBe('publish');
    expect(articleWriter(MACHINES, 'approved')).toBe('nobody');
    expect(articleWriter(MACHINES, 'archived')).toBe('nobody');
    expect(articleWriter(undefined, 'in_review')).toBeUndefined(); // unknown: never guessed
  });
});

describe('machine rules', () => {
  it('enables an edge only when the machines are loaded and list it', () => {
    expect(machineAllows(undefined, 'article', 'planned', 'researching')).toBe(false);
    expect(machineAllows(MACHINES, 'article', 'planned', 'researching')).toBe(true);
    expect(machineAllows(MACHINES, 'article', 'published', 'archived')).toBe(false);
    expect(machineAllows(MACHINES, 'post', 'draft', 'archived')).toBe(true);
    expect(machineAllows(MACHINES, 'post', 'posted', 'archived')).toBe(false);
    // When the machines could not be loaded at all, the caller lets the route decide.
    expect(machineAllows(undefined, 'article', 'planned', 'researching', true)).toBe(true);
    expect(machineAllows(MACHINES, 'article', 'published', 'archived', true)).toBe(false);
  });

  it('lets the owner make only the edges the server lists as the owner’s', () => {
    expect(ownerCanMoveArticle(MACHINES, 'planned', 'archived')).toBe(true);
    expect(ownerCanMoveArticle(MACHINES, 'archived', 'planned')).toBe(true);
    expect(ownerCanMoveArticle(MACHINES, 'publish_failed', 'in_review')).toBe(true);
    // Edges of the machine the system makes: the pipeline's, the gates', the publish job's.
    expect(ownerCanMoveArticle(MACHINES, 'drafting', 'archived')).toBe(false);
    expect(ownerCanMoveArticle(MACHINES, 'drafting', 'gating')).toBe(false);
    expect(ownerCanMoveArticle(MACHINES, 'publishing', 'published')).toBe(false);
    expect(ownerCanMoveArticle(undefined, 'planned', 'archived')).toBe(false);
    expect(ownerCanMoveArticle(undefined, 'planned', 'archived', true)).toBe(true); // the route decides
  });
});

describe('the next step', () => {
  it('starts research for a planned article, or says when the plan starts it', () => {
    expect(nextActionFor({ kind: 'article', id: 'art-1', status: 'planned' })).toMatchObject({
      needsOwner: true,
      primary: { type: 'start-research', label: 'Start research' },
    });
    const queued = nextActionFor({
      kind: 'article',
      id: 'art-1',
      status: 'planned',
      researchStartsAt: '2026-11-30T14:00:00.000000Z',
    });
    expect(queued.sentence).toMatch(/^Research starts .+ as the weekly plan set it/);
    expect(queued.primary).toMatchObject({ type: 'start-research', label: 'Start research now' });
  });

  it('sends the owner to the tab where the work is', () => {
    expect(nextActionFor({ kind: 'article', id: 'art-1', status: 'interviewing' }).primary).toEqual({
      type: 'tab',
      tab: 'interview',
      label: 'Answer the interview',
    });
    expect(nextActionFor({ kind: 'article', id: 'art-1', status: 'in_review' }).primary).toMatchObject({ tab: 'draft' });
    expect(nextActionFor({ kind: 'post', id: 'post-9', status: 'staged' }).primary).toEqual({
      type: 'tab',
      tab: 'go-live',
      label: 'Open go-live',
      search: '?post=post-9',
    });
  });

  it('explains a stopped article and points research failures at the Brief tab', () => {
    const research = nextActionFor({ kind: 'article', id: 'art-1', status: 'needs_attention', lastError: 'research_failed' });
    expect(research.sentence).toBe('It needs you. Research didn’t produce a brief.');
    expect(research.primary).toMatchObject({ tab: 'brief' });
    const other = nextActionFor({ kind: 'article', id: 'art-1', status: 'needs_attention', lastError: 'something_new' });
    expect(other.sentence).toBe('It needs you. Something stopped the work on it.');
    expect(other.primary).toMatchObject({ tab: 'draft' });
  });

  it('offers the live page only for an https link', () => {
    const live = nextActionFor({ kind: 'article', id: 'art-1', status: 'published', liveUrl: 'https://example.com/blog/x' });
    expect(live.secondary).toEqual({ type: 'external', href: 'https://example.com/blog/x', label: 'Open the live page' });
    expect(nextActionFor({ kind: 'article', id: 'art-1', status: 'published', liveUrl: 'javascript:alert(1)' }).secondary).toBeUndefined();
    expect(nextActionFor({ kind: 'post', id: 'post-1', status: 'posted', postUrl: 'http://x.test' }).primary).toBeUndefined();
  });

  it('restores an archived article', () => {
    expect(nextActionFor({ kind: 'article', id: 'art-1', status: 'archived' }).primary).toEqual({
      type: 'restore',
      label: 'Restore',
    });
  });

  it('opens a piece from the list at the tab of its next step', () => {
    expect(piecePath({ kind: 'article', id: 'art-1', status: 'interviewing' })).toBe('/studio/art-1/interview');
    expect(piecePath({ kind: 'article', id: 'art-1', status: 'planned' })).toBe('/studio/art-1/brief');
    expect(piecePath({ kind: 'post', id: 'post-1', status: 'missed' })).toBe('/studio/post-1/go-live?post=post-1');
    expect(piecePath({ kind: 'post', id: 'post-1', status: 'deleted' })).toBe('/studio/post-1/brief');
  });
});

describe('text helpers', () => {
  it('turns codes into words', () => {
    expect(humanize('deep-explainer')).toBe('Deep explainer');
    expect(humanize('critique_revise')).toBe('Critique revise');
    expect(lastErrorSentence(undefined)).toBeUndefined();
    expect(lastErrorSentence('voice_guide_missing')).toContain('voice guide');
  });

  it('takes a post’s first non-empty line as its title', () => {
    expect(firstLine('\n  Hook line  \nmore')).toBe('Hook line');
    expect(firstLine('   ')).toBeUndefined();
    expect(firstLine('x'.repeat(200), 10)).toBe('xxxxxxxxx…');
  });
});
