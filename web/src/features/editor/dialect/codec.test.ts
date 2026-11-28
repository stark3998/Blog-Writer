/** ArticleCodec's API: parse, serialize, normalize and check, with typed errors. */
import type { JSONContent } from '@tiptap/core';
import { afterAll, describe, expect, test } from 'vitest';

import { type Article, ArticleCodec } from './codec';
import { DialectError, FrontmatterError } from './errors';
import type { ArticleFrontmatter } from './frontmatter';

const codec = new ArticleCodec();
afterAll(() => {
  codec.destroy();
});

const FRONTMATTER: ArticleFrontmatter = {
  title: 'Least-privilege tool calls',
  slug: 'least-privilege-tool-calls',
  excerpt: 'How to scope one tool call.',
  pillar: 'agents',
  format: 'deep-explainer',
  tags: ['agents', 'identity'],
};

const HEAD = `---
title: "Least-privilege tool calls"
slug: least-privilege-tool-calls
excerpt: "How to scope one tool call."
pillar: agents
format: deep-explainer
tags: [agents, identity]
---
`;

const paragraph = (text: string): JSONContent => ({ type: 'paragraph', content: [{ type: 'text', text }] });
const doc = (...content: JSONContent[]): JSONContent => ({ type: 'doc', content });

describe('parse', () => {
  test('returns the frontmatter and the TipTap JSON of the body', () => {
    const article = codec.parse(`${HEAD}\n## Scope\n\nOne *tool* call.\n`);
    expect(article.frontmatter).toEqual(FRONTMATTER);
    expect(article.doc.type).toBe('doc');
    expect(article.doc.content?.slice(0, 2)).toEqual([
      { type: 'heading', attrs: { level: 2 }, content: [{ type: 'text', text: 'Scope' }] },
      {
        type: 'paragraph',
        content: [
          { type: 'text', text: 'One ' },
          { type: 'text', text: 'tool', marks: [{ type: 'italic' }] },
          { type: 'text', text: ' call.' },
        ],
      },
    ]);
  });

  test('a source without frontmatter has frontmatter null', () => {
    expect(codec.parse('Just text.\n').frontmatter).toBeNull();
  });

  test('CRLF line endings and a byte-order mark are accepted', () => {
    expect(codec.normalize('\uFEFFLine one\r\nline two\r\n')).toBe('Line one line two\n');
  });
});

describe('serialize', () => {
  test('writes canonical frontmatter, one blank line, then the body', () => {
    const article = codec.parse(`${HEAD}Body.\n`);
    expect(codec.serialize(article)).toBe(`${HEAD}\nBody.\n`);
  });

  test('frontmatter keys are written in canonical order whatever the object order', () => {
    const reordered: ArticleFrontmatter = {
      tags: ['agents', 'identity'],
      format: 'deep-explainer',
      pillar: 'agents',
      excerpt: 'How to scope one tool call.',
      slug: 'least-privilege-tool-calls',
      title: 'Least-privilege tool calls',
    };
    expect(codec.serialize({ frontmatter: reordered, doc: doc(paragraph('Body.')) })).toBe(`${HEAD}\nBody.\n`);
  });

  test('an article with frontmatter and no body is the frontmatter alone', () => {
    expect(codec.serialize({ frontmatter: FRONTMATTER, doc: doc({ type: 'paragraph' }) })).toBe(HEAD);
  });

  test.each<[string, Partial<ArticleFrontmatter>, string]>([
    ['markup in the title', { title: '<b>Bold</b>' }, 'title: must not contain "<" (plain text only)'],
    ['an empty excerpt', { excerpt: '' }, 'excerpt: must not be empty'],
    ['an uppercase tag', { tags: ['Agents'] }, 'tags[0]: must be lowercase kebab-case ([a-z0-9-])'],
    ['a fractional series part', { series: { id: 'a', title: 'A', part: 1.5 } }, 'series.part: must be a positive integer (1-9999)'],
  ])('refuses to write frontmatter it could not read back: %s', (_name, change, issue) => {
    try {
      codec.serialize({ frontmatter: { ...FRONTMATTER, ...change }, doc: doc(paragraph('Body.')) });
      expect.unreachable();
    } catch (error) {
      expect(error).toBeInstanceOf(FrontmatterError);
      expect((error as FrontmatterError).issues.map((item) => `${item.path}: ${item.message}`)).toContain(issue);
    }
  });

  test('a document the schema cannot hold is invalid-content', () => {
    expect(() => codec.serialize({ frontmatter: null, doc: doc({ type: 'heading' }, { type: 'unknown-node' }) })).toThrow(
      DialectError,
    );
  });
});

describe('normalize', () => {
  test('is serialize(parse(source))', () => {
    const source = 'Setext\n======\n\n* a\n* b\n';
    expect(codec.normalize(source)).toBe(codec.serialize(codec.parse(source)));
    expect(codec.normalize(source)).toBe('# Setext\n\n- a\n- b\n');
  });
});

describe('check', () => {
  test('a canonical article is lossless', () => {
    const article: Article = { frontmatter: FRONTMATTER, doc: doc(paragraph('One.'), paragraph('Two.')) };
    const result = codec.check(article);
    expect(result).toMatchObject({ lossless: true, warnings: [], markdown: `${HEAD}\nOne.\n\nTwo.\n` });
    expect(result.reparsed.frontmatter).toEqual(FRONTMATTER);
  });

  test('a Markdown source is parsed first: check(md) tells whether normalizing md keeps the document', () => {
    const result = codec.check('Setext\n======\n\nSoft\nbreak.\n');
    expect(result.lossless).toBe(true);
    expect(result.markdown).toBe('# Setext\n\nSoft break.\n');
  });

  test('frontmatter key order does not count as a change', () => {
    const reordered = { ...FRONTMATTER };
    const shuffled = Object.fromEntries(Object.entries(reordered).reverse()) as unknown as ArticleFrontmatter;
    expect(codec.check({ frontmatter: shuffled, doc: doc(paragraph('x')) }).warnings).toEqual([]);
  });

  test('names the block that would change, as an index into the checked document', () => {
    const article: Article = {
      frontmatter: null,
      doc: doc(paragraph('Intro.'), { type: 'paragraph' }, paragraph('Typed\nline break.'), paragraph('Outro.')),
    };
    const result = codec.check(article);
    expect(result.lossless).toBe(false);
    expect(result.warnings).toEqual([
      {
        code: 'content-changed',
        message: 'Markdown cannot hold block 3 exactly; saving would change it.',
        blocks: { from: 2, to: 3 },
      },
    ]);
  });

  test('a range of blocks: two adjacent changes', () => {
    const article: Article = {
      frontmatter: null,
      doc: doc(paragraph('Keep.'), paragraph('a\nb'), paragraph('c\nd'), paragraph('Keep.')),
    };
    expect(codec.check(article).warnings).toEqual([
      {
        code: 'content-changed',
        message: 'Markdown cannot hold blocks 2-3 exactly; saving would change them.',
        blocks: { from: 1, to: 3 },
      },
    ]);
  });

  test('a change in the footnotes points at the footnotes block', () => {
    const article: Article = {
      frontmatter: null,
      doc: doc(
        { type: 'paragraph', content: [{ type: 'text', text: 'Claim.' }, { type: 'citation', attrs: { id: 'a' } }] },
        {
          type: 'footnotes',
          content: [{ type: 'footnoteDefinition', attrs: { id: 'a' }, content: [{ type: 'text', text: 'Line\nbreak.' }] }],
        },
      ),
    };
    expect(codec.check(article).warnings).toEqual([
      expect.objectContaining({ code: 'content-changed', blocks: { from: 1, to: 2 } }),
    ]);
  });

  test('errors are typed: a document over the size limit cannot be checked', () => {
    const big: Article = { frontmatter: null, doc: doc(paragraph('x'.repeat(140 * 1024))) };
    expect(() => codec.check(big)).toThrow(expect.objectContaining({ code: 'too-large' }));
  });
});
