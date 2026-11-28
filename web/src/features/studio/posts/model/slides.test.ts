// @vitest-environment node
import { describe, expect, it } from 'vitest';

import { CAROUSEL_SPEC } from '../testing/fixtures';

import {
  convertSlide,
  moveItem,
  newSlide,
  readSpec,
  sameSpec,
  slideHints,
  slideWords,
  specErrors,
  specJson,
  starterCard,
  starterSpec,
  type CarouselSpec,
} from './slides';

function spec(): CarouselSpec {
  const parsed = readSpec(CAROUSEL_SPEC);
  if (!parsed) throw new Error('the fixture spec did not parse');
  return parsed;
}

describe('reading and writing the stored spec', () => {
  it('round-trips the server’s JSON', () => {
    expect(specJson(spec())).toEqual(CAROUSEL_SPEC);
  });

  it('leaves out empty optional fields and trims text', () => {
    const deck = spec();
    deck.slides[0] = { type: 'cover', hook: '  The hook  ', kicker: '', subtitle: '   ' };
    expect(specJson(deck).slides).toContainEqual({ type: 'cover', hook: 'The hook' });
  });

  it('keeps a slide type it does not know exactly as it was', () => {
    const stored = { title: 'T', lang: 'en', slides: [{ type: 'timeline', points: [1, 2] }] };
    const parsed = readSpec(stored);
    expect(parsed?.slides[0]?.type).toBe('opaque');
    expect(parsed ? specJson(parsed) : undefined).toEqual(stored);
  });

  it('has no spec when the post has none', () => {
    expect(readSpec(null)).toBeUndefined();
    expect(sameSpec(undefined, undefined)).toBe(true);
    expect(sameSpec(spec(), undefined)).toBe(false);
  });

  it('compares specs by what would be stored', () => {
    const a = spec();
    const b = spec();
    b.slides[1] = { type: 'statement', statement: 'Inventory first. Everything else follows from it.  ', note: '' };
    expect(sameSpec(a, b)).toBe(true);
  });
});

describe('the carousel rules as hints', () => {
  it('passes a deck that follows them', () => {
    expect(slideHints(spec())).toEqual([]);
  });

  it('names what a deck breaks, by slide', () => {
    const deck = spec();
    deck.title = 'A document title that runs well past the fifty-eight characters';
    deck.slides = [newSlide('statement'), ...deck.slides.slice(1, 4)];
    const hints = slideHints(deck).map((hint) => [hint.slide, hint.message]);
    expect(hints).toContainEqual([undefined, expect.stringMatching(/^The document title is \d+ characters; keep it to 58\.$/)]);
    expect(hints).toContainEqual([undefined, '4 slides; carousels use 8 to 12.']);
    expect(hints).toContainEqual([0, 'The first slide must be the cover.']);
    expect(hints).toContainEqual([undefined, 'End with the recap slide (a sources slide may follow it).']);
  });

  it('counts the cover’s words and every slide’s words', () => {
    const deck = spec();
    deck.slides[0] = { type: 'cover', hook: 'one two three four five six seven eight nine ten eleven', kicker: null, subtitle: null };
    deck.slides[1] = { type: 'statement', statement: Array.from({ length: 36 }, () => 'word').join(' '), note: null };
    const hints = slideHints(deck);
    expect(hints).toContainEqual({ slide: 0, message: 'The cover’s hook is 11 words; keep it to 10.' });
    expect(hints).toContainEqual({ slide: 1, message: '36 words on this slide; keep it to 35.' });
    expect(slideWords(deck.slides[1])).toBe(36);
  });

  it('puts the sources slide last only', () => {
    const deck = spec();
    const sources = deck.slides.at(-1);
    if (!sources) throw new Error('no slides');
    deck.slides = [...deck.slides.slice(0, 2), sources, ...deck.slides.slice(2, -1)];
    expect(slideHints(deck)).toContainEqual({ slide: 2, message: 'A sources slide can only come last, after the recap.' });
  });

  it('holds an image card to one slide', () => {
    const card = starterCard('Card', 'One line.');
    expect(slideHints(card, { card: true })).toEqual([]);
    expect(slideHints({ ...card, slides: [...card.slides, newSlide('stat')] }, { card: true })).toContainEqual({
      message: 'An image card has exactly one slide.',
    });
  });
});

describe('editing slides', () => {
  it('starts a deck with the hook on the cover and the recap last', () => {
    const deck = starterSpec('Title', 'We moved 2,000 apps off Okta in 14 months without a single outage.');
    expect(deck.slides).toHaveLength(8);
    expect(deck.slides[0]).toMatchObject({ type: 'cover', hook: 'We moved 2,000 apps off Okta in 14 months without' });
    expect(deck.slides.at(-1)?.type).toBe('recap');
  });

  it('keeps the words when a slide changes type', () => {
    expect(convertSlide({ type: 'cover', hook: 'The hook', kicker: null, subtitle: null }, 'statement')).toMatchObject({
      type: 'statement',
      statement: 'The hook',
    });
    expect(convertSlide({ type: 'list', title: 'Do', items: ['a', 'b', 'c'] }, 'recap')).toMatchObject({
      type: 'recap',
      title: 'Do',
      items: ['a', 'b', 'c'],
    });
    expect(convertSlide({ type: 'list', title: 'Steps', items: ['a', 'b'] }, 'steps')).toMatchObject({
      type: 'steps',
      steps: [
        { title: 'a', detail: null },
        { title: 'b', detail: null },
      ],
    });
  });

  it('moves an item, clamped to the list', () => {
    expect(moveItem(['a', 'b', 'c'], 2, 0)).toEqual(['c', 'a', 'b']);
    expect(moveItem(['a', 'b', 'c'], 0, 9)).toEqual(['b', 'c', 'a']);
    expect(moveItem(['a'], 3, 0)).toEqual(['a']);
  });
});

describe('specErrors (422 invalid_spec)', () => {
  it('maps locations to slides and fields, without the union tag', () => {
    expect(
      specErrors([
        { loc: ['slides', 2, 'cover', 'hook'], msg: 'String should have at most 80 characters', type: 'string_too_long' },
        { loc: ['slides', 4, 'list', 'items', 1], msg: 'Field required', type: 'missing' },
        { loc: ['title'], msg: 'Field required', type: 'missing' },
        'garbage',
      ]),
    ).toEqual([
      { slide: 2, field: 'hook', message: 'String should have at most 80 characters.' },
      { slide: 4, field: 'items.1', message: 'Field required.' },
      { field: 'title', message: 'Field required.' },
    ]);
    expect(specErrors(undefined)).toEqual([]);
  });
});
