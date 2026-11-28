// @vitest-environment node
import { describe, expect, it } from 'vitest';

import { articleSummary, postSummary } from '../piece/testing/fixtures';

import {
  articleStatusesFor,
  fromArticle,
  fromPost,
  groupPieces,
  matchesSearch,
  mergePieces,
  parseKind,
  parseShow,
  postStatusesFor,
} from './list-model';

describe('filters', () => {
  it('reads the URL values and falls back to the defaults', () => {
    expect(parseShow('live')).toBe('live');
    expect(parseShow('nonsense')).toBe('active');
    expect(parseShow(null)).toBe('active');
    expect(parseKind('post')).toBe('post');
    expect(parseKind('')).toBe('all');
  });

  it('asks the server for the statuses each filter covers', () => {
    expect(articleStatusesFor('active')).toEqual([
      'planned',
      'researching',
      'interviewing',
      'drafting',
      'gating',
      'in_review',
      'approved',
      'publishing',
      'publish_failed',
      'unpublishing',
      'needs_attention',
    ]);
    expect(articleStatusesFor('live')).toEqual(['published']);
    expect(articleStatusesFor('archived')).toEqual(['archived', 'unpublished']);
    expect(articleStatusesFor('all')).toBeUndefined();
    expect(postStatusesFor('active')).toEqual([
      'generating',
      'draft',
      'approved',
      'staged',
      'publishing',
      'kit',
      'needs_attention',
      'missed',
    ]);
    expect(postStatusesFor('archived')).toEqual(['archived', 'expired', 'deleted']);
  });
});

describe('pieces', () => {
  it('gives untitled pieces an honest title', () => {
    expect(fromArticle(articleSummary({ meta: { title: '  ', slug: null, pillar: null, format: null } }))).toMatchObject({
      title: 'Untitled piece',
      untitled: true,
    });
    expect(fromPost(postSummary({ hook: '' }))).toMatchObject({ title: 'Untitled post', untitled: true });
  });

  it('groups by what the piece needs, the owner first, newest change first', () => {
    const pieces = mergePieces(
      [
        articleSummary({ id: 'art-planned', status: 'planned', updatedAt: '2026-11-27T09:00:00.000000Z' }),
        articleSummary({ id: 'art-review', status: 'in_review', updatedAt: '2026-11-27T08:00:00.000000Z' }),
        articleSummary({ id: 'art-drafting', status: 'drafting' }),
      ],
      [
        postSummary({ id: 'post-draft', status: 'draft', updatedAt: '2026-11-27T11:00:00.000000Z' }),
        postSummary({ id: 'post-ready', status: 'staged' }),
      ],
    );
    const groups = groupPieces(pieces);
    expect(groups.map((group) => group.group)).toEqual(['needs-you', 'working', 'planned', 'scheduled']);
    expect(groups[0]?.pieces.map((piece) => piece.id)).toEqual(['post-draft', 'art-review']);
  });

  it('lists an id once even if both pages carry it', () => {
    const merged = mergePieces([articleSummary({ id: 'art-1' }), articleSummary({ id: 'art-1' })], []);
    expect(merged).toHaveLength(1);
  });

  it('searches titles by every word, ignoring case and accents', () => {
    const piece = fromArticle(articleSummary({ meta: { title: 'Café rules for Entra ID', slug: null, pillar: null, format: null } }));
    expect(matchesSearch(piece, 'entra cafe')).toBe(true);
    expect(matchesSearch(piece, 'ENTRA  RULES')).toBe(true);
    expect(matchesSearch(piece, 'okta')).toBe(false);
    expect(matchesSearch(piece, '   ')).toBe(true);
  });
});
