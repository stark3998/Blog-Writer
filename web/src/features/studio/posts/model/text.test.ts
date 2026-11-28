// @vitest-environment node
import { describe, expect, it } from 'vitest';

import {
  addHashtag,
  codePointLength,
  DESKTOP_FOLD,
  estimatePayloadUnits,
  firstParagraph,
  foldIndex,
  hashtagSpans,
  hashtagsInText,
  removeHashtag,
  replaceHook,
  utf16Offset,
  wordCount,
} from './text';

describe('foldIndex (a port of linkedin.lint.fold_index)', () => {
  it('shows a short post whole', () => {
    expect(foldIndex('Short post.')).toBe(11);
  });

  it('cuts after three rendered lines, blank lines included', () => {
    // a \n b \n c \n d: the third newline ends the third line.
    expect(foldIndex('a\nb\nc\nd')).toBe(5);
    expect(foldIndex('Hook line\n\nSecond paragraph\nmore')).toBe(27);
  });

  it('wraps a long line every 48 characters and stops at 140', () => {
    const line = 'x'.repeat(200);
    expect(foldIndex(line)).toBe(140);
    // A wrap after 48 characters, another after 96, then the newline ends the third line.
    expect(foldIndex(`${'y'.repeat(100)}\nrest`)).toBe(100);
  });

  it('counts code points, as the server does', () => {
    const text = `${'😀'.repeat(10)}\n\n\nrest`;
    expect(foldIndex(text)).toBe(12);
    expect(utf16Offset(text, 12)).toBe(22);
  });

  it('gives the desktop feed more room', () => {
    const line = 'z'.repeat(300);
    expect(foldIndex(line, DESKTOP_FOLD)).toBe(210);
  });
});

describe('offsets', () => {
  it('converts code points to UTF-16 offsets and clamps', () => {
    expect(utf16Offset('a😀b', 0)).toBe(0);
    expect(utf16Offset('a😀b', 2)).toBe(3);
    expect(utf16Offset('a😀b', 99)).toBe(4);
    expect(codePointLength('a😀b')).toBe(3);
  });
});

describe('estimatePayloadUnits', () => {
  it('adds a backslash for every reserved character', () => {
    expect(estimatePayloadUnits('Hello (world)', [])).toBe(15);
    expect(estimatePayloadUnits('a_b*c', [])).toBe(7);
  });

  it('leaves the # of an approved hashtag unescaped, any other # escaped', () => {
    expect(estimatePayloadUnits('Done #Entra', ['Entra'])).toBe(11);
    expect(estimatePayloadUnits('Done #entra.', ['Entra'])).toBe(12);
    expect(estimatePayloadUnits('Done #Other', ['Entra'])).toBe(12);
    expect(estimatePayloadUnits('C#Sharp', ['Sharp'])).toBe(8);
  });

  it('counts emoji as two units', () => {
    expect(estimatePayloadUnits('😀', [])).toBe(2);
  });
});

describe('hashtags in the text', () => {
  const approved = ['IdentityModernization', 'Entra', 'CIAM'];

  it('finds the approved ones, in their approved spelling', () => {
    expect(hashtagsInText('Body\n\n#entra #IdentityModernization #Other', approved)).toEqual([
      'Entra',
      'IdentityModernization',
    ]);
    expect(hashtagsInText('No tags, C#Sharp and foo#bar', approved)).toEqual([]);
  });

  it('marks where they are', () => {
    expect(hashtagSpans('Go #Entra now', approved)).toEqual([{ start: 3, end: 9 }]);
  });

  it('adds a tag to the closing hashtag line, or as a new paragraph', () => {
    expect(addHashtag('Body\n\n#Entra', 'CIAM')).toBe('Body\n\n#Entra #CIAM');
    expect(addHashtag('Body text.', 'CIAM')).toBe('Body text.\n\n#CIAM');
    expect(addHashtag('', 'CIAM')).toBe('#CIAM');
  });

  it('removes a tag, and an emptied hashtag line with it', () => {
    expect(removeHashtag('Body\n\n#Entra #CIAM', 'Entra')).toBe('Body\n\n#CIAM');
    expect(removeHashtag('Body\n\n#Entra', 'entra')).toBe('Body');
    expect(removeHashtag('Moving to #Entra, finally.', 'Entra')).toBe('Moving to, finally.');
  });
});

describe('the hook', () => {
  it('is the first paragraph', () => {
    expect(firstParagraph('Hook here.\n\nBody.')).toBe('Hook here.');
    expect(firstParagraph('Only one paragraph')).toBe('Only one paragraph');
  });

  it('is replaced without touching the body', () => {
    expect(replaceHook('Old hook.\n\nBody.\n\n#Entra', 'New hook.')).toBe('New hook.\n\nBody.\n\n#Entra');
    expect(replaceHook('Old hook.\nBody on the next line.', 'New hook.')).toBe('New hook.\nBody on the next line.');
    expect(replaceHook('Just a hook', '  New hook.  ')).toBe('New hook.');
  });
});

describe('wordCount', () => {
  it('counts runs of non-space', () => {
    expect(wordCount('  two words ')).toBe(2);
    expect(wordCount('')).toBe(0);
    expect(wordCount(null)).toBe(0);
  });
});
