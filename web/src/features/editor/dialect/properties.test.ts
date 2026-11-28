/**
 * Property-based escaping round trips (ported from the spike's fast-check
 * suite onto a seeded generator, `../testing/random.ts`).
 *
 * Property: arbitrary text placed in each inline context the dialect allows
 * serializes to Markdown that TipTap reads back as exactly the same text, and
 * that text never starts a new Markdown line (the line count of the output is
 * fixed by the document's structure). The spike also checked the server's
 * reading with a markdown-it oracle; that half needs markdown-it as a dev
 * dependency (see README.md).
 *
 * Deterministic by default; explore further with
 * BW_PROPERTY_SEED=7 BW_PROPERTY_RUNS=20000 npx vitest run src/features/editor/dialect/properties.test.ts
 */
import type { JSONContent } from '@tiptap/core';
import { afterAll, describe, expect, test } from 'vitest';

import { checkProperty, type PropertyOptions, type Random, SKIP, textFrom, webUrl } from '../testing/random';
import { ArticleCodec } from './codec';

const codec = new ArticleCodec();
afterAll(() => {
  codec.destroy();
});

const RUNS = Number(process.env.BW_PROPERTY_RUNS ?? 1500);
const SEED = Number(process.env.BW_PROPERTY_SEED ?? 20261125);
const TIMEOUT_MS = 120_000;

/** Each property gets its own stream of inputs. */
function options(index: number, runs = RUNS): PropertyOptions {
  return { runs, seed: SEED + index * 7919 };
}

const ALPHABET = [
  ...Array.from('\\`*_[]<>&#-+=:|~!()^.,;?"\'{}$%@/'),
  ...Array.from('0123456789'),
  ...Array.from('abcxyzABC'),
  ' ',
  '\u00A0',
  '\u3000',
  '\t',
  '\u200B',
  'é',
  '日',
  '😀',
  'ß',
  '&amp;',
  '&copy;',
  '&#35;',
  '&#x26;',
  '1.',
  '1)',
  '---',
  '===',
  ':::',
  '```',
  '~~~',
  '[^x]',
  '](',
  '![',
  '<a',
  '</',
  '<!--',
  '\\\n',
  // Line endings: a text node holding one (insertText, pasted or generated
  // text) used to start a new block.
  '\n',
  '\r',
  '\r\n',
];

/** 1-24 alphabet pieces with at least one non-blank character. */
function text(random: Random): string {
  for (;;) {
    const value = textFrom(random, ALPHABET, 24);
    if (/[^ \t\r\n]/.test(value)) {
      return value;
    }
  }
}

function serialize(doc: JSONContent): string {
  return codec.serialize({ frontmatter: null, doc });
}

/** Text of the parsed document as TipTap reads it (hard breaks as "\n"). */
function tiptapText(doc: JSONContent): string {
  const out: string[] = [];
  const visit = (node: JSONContent) => {
    if (node.type === 'text') {
      out.push(node.text ?? '');
    } else if (node.type === 'hardBreak') {
      out.push('\n');
    }
    for (const child of node.content ?? []) {
      visit(child);
    }
  };
  visit(doc);
  return out.join('');
}

/** Lines of the Markdown, without the final newline. */
function lines(markdown: string): string[] {
  return markdown.replace(/\n$/, '').split('\n');
}

/**
 * Parsers trim Unicode whitespace (JS trim) at block boundaries and spaces
 * after a hard break; the serializer writes tabs in prose as spaces, and a
 * run of line endings (with the blanks around it) as one space.
 */
function prose(value: string): string {
  return value.replace(/[ \t]*(?:(?:\r\n?|\n)[ \t]*)+/g, ' ').replace(/\t/g, ' ');
}

const paragraph = (...content: JSONContent[]): JSONContent => ({ type: 'paragraph', content });
const textNode = (value: string, marks?: JSONContent['marks']): JSONContent => ({
  type: 'text',
  text: value,
  ...(marks ? { marks } : {}),
});

describe('escaping round trips (property-based)', () => {
  test(
    'paragraph text',
    () => {
      checkProperty(options(1), text, (value) => {
        const expected = prose(value).trim();
        if (expected === '') return SKIP;
        const markdown = serialize({ type: 'doc', content: [paragraph(textNode(value))] });
        expect(lines(markdown)).toHaveLength(1);
        expect(tiptapText(codec.parse(markdown).doc)).toBe(expected);
        return undefined;
      });
    },
    TIMEOUT_MS,
  );

  test(
    'text on both sides of a hard break',
    () => {
      checkProperty(
        options(2),
        (random) => [text(random), text(random)] as const,
        ([before, after]) => {
          const left = prose(before).trimStart();
          const right = prose(after).replace(/^ +/, '').trimEnd();
          if (left.trim() === '' || right === '') return SKIP;
          const markdown = serialize({
            type: 'doc',
            content: [paragraph(textNode(before), { type: 'hardBreak' }, textNode(after))],
          });
          expect(lines(markdown)).toHaveLength(2);
          expect(tiptapText(codec.parse(markdown).doc)).toBe(`${left}\n${right}`);
          return undefined;
        },
      );
    },
    TIMEOUT_MS,
  );

  test(
    'heading text',
    () => {
      checkProperty(options(3), text, (value) => {
        const expected = prose(value).trim();
        if (expected === '') return SKIP;
        const markdown = serialize({
          type: 'doc',
          content: [{ type: 'heading', attrs: { level: 2 }, content: [textNode(value)] }],
        });
        expect(lines(markdown)).toHaveLength(1);
        expect(markdown.startsWith('## ')).toBe(true);
        const doc = codec.parse(markdown).doc;
        expect(doc.content?.[0]?.type).toBe('heading');
        expect(tiptapText(doc)).toBe(expected);
        return undefined;
      });
    },
    TIMEOUT_MS,
  );

  test(
    'list item text',
    () => {
      checkProperty(options(4), text, (value) => {
        const expected = prose(value).trim();
        if (expected === '') return SKIP;
        const markdown = serialize({
          type: 'doc',
          content: [{ type: 'bulletList', content: [{ type: 'listItem', content: [paragraph(textNode(value))] }] }],
        });
        expect(lines(markdown)).toHaveLength(1);
        const doc = codec.parse(markdown).doc;
        expect(doc.content?.map((node) => node.type)).toEqual(['bulletList']);
        expect(tiptapText(doc)).toBe(expected);
        return undefined;
      });
    },
    TIMEOUT_MS,
  );

  test(
    'table cell text',
    () => {
      checkProperty(options(5), text, (value) => {
        const expected = prose(value).trim();
        if (expected === '' || expected !== prose(value)) return SKIP;
        const cell = (type: string, content: string) => ({ type, content: [paragraph(textNode(content))] });
        const markdown = serialize({
          type: 'doc',
          content: [
            {
              type: 'table',
              content: [
                { type: 'tableRow', content: [cell('tableHeader', 'h')] },
                { type: 'tableRow', content: [cell('tableCell', value)] },
              ],
            },
          ],
        });
        expect(lines(markdown)).toHaveLength(3);
        expect(tiptapText(codec.parse(markdown).doc)).toBe(`h${expected}`);
        return undefined;
      });
    },
    TIMEOUT_MS,
  );

  test(
    'inline code content',
    () => {
      checkProperty(options(6), text, (value) => {
        // Inside a code span a line ending reads as a space.
        const expected = value.replace(/\r\n?|\n/g, ' ');
        if (/^ +$/.test(expected)) return SKIP;
        const markdown = serialize({
          type: 'doc',
          content: [paragraph(textNode('x '), textNode(value, [{ type: 'code' }]), textNode(' y'))],
        });
        expect(lines(markdown)).toHaveLength(1);
        const inline = codec.parse(markdown).doc.content?.[0]?.content ?? [];
        expect(tiptapText(codec.parse(markdown).doc)).toBe(`x ${expected} y`);
        expect(inline.filter((node) => node.marks?.some((mark) => mark.type === 'code')).map((node) => node.text)).toEqual([
          expected,
        ]);
        return undefined;
      });
    },
    TIMEOUT_MS,
  );

  test(
    'link text and destination',
    () => {
      checkProperty(
        options(7, Math.ceil(RUNS / 3)),
        (random) => [text(random), webUrl(random)] as const,
        ([value, href]) => {
          const expected = prose(value).trim();
          if (expected === '' || expected !== prose(value)) return SKIP;
          const markdown = serialize({
            type: 'doc',
            content: [paragraph(textNode('see '), textNode(value, [{ type: 'link', attrs: { href } }]))],
          });
          expect(lines(markdown)).toHaveLength(1);
          const parsed = codec.parse(markdown).doc;
          const link = parsed.content?.[0]?.content?.find((node) => node.marks?.some((mark) => mark.type === 'link'));
          const destination: unknown = link?.marks?.find((mark) => mark.type === 'link')?.attrs?.href;
          expect(destination).toBe(href);
          expect(tiptapText(parsed)).toBe(`see ${expected}`);
          return undefined;
        },
      );
    },
    TIMEOUT_MS,
  );

  test(
    'image alt text',
    () => {
      checkProperty(options(8), text, (value) => {
        const expected = value.replace(/\s+/g, ' ').trim();
        if (expected === '') return SKIP;
        const markdown = serialize({
          type: 'doc',
          content: [paragraph({ type: 'image', attrs: { src: '/blog-media/a/b.png', alt: value } })],
        });
        expect(lines(markdown)).toHaveLength(1);
        const image = codec.parse(markdown).doc.content?.[0]?.content?.[0];
        expect(image?.type).toBe('image');
        expect(image?.attrs?.alt).toBe(expected);
        return undefined;
      });
    },
    TIMEOUT_MS,
  );

  test(
    'callout paragraph text',
    () => {
      checkProperty(options(9), text, (value) => {
        const expected = prose(value).trim();
        if (expected === '') return SKIP;
        const markdown = serialize({
          type: 'doc',
          content: [{ type: 'callout', attrs: { type: 'note' }, content: [paragraph(textNode(value))] }],
        });
        expect(lines(markdown)).toHaveLength(3);
        const doc = codec.parse(markdown).doc;
        expect(doc.content?.[0]?.type).toBe('callout');
        expect(tiptapText(doc)).toBe(expected);
        return undefined;
      });
    },
    TIMEOUT_MS,
  );

  test(
    'bold and italic text between words',
    () => {
      const word = (random: Random) => textFrom(random, Array.from('abcdefghijklmnopqrstuvwxyz0123456789'), 6);
      checkProperty(
        options(10),
        (random) => [word(random), text(random), word(random), random.pick(['bold', 'italic'] as const)] as const,
        ([head, middle, tail, mark]) => {
          const inner = `${head}${prose(middle).trim()}${tail}`;
          const markdown = serialize({
            type: 'doc',
            content: [paragraph(textNode('a '), textNode(inner, [{ type: mark }]), textNode(' z'))],
          });
          expect(lines(markdown)).toHaveLength(1);
          const parsed = codec.parse(markdown).doc.content?.[0]?.content ?? [];
          expect(parsed.map((node) => [node.text, node.marks?.map((item) => item.type) ?? []])).toEqual([
            ['a ', []],
            [inner, [mark]],
            [' z', []],
          ]);
          return undefined;
        },
      );
    },
    TIMEOUT_MS,
  );

  test(
    'footnote definition text',
    () => {
      checkProperty(options(11), text, (value) => {
        const expected = prose(value).trim();
        if (expected === '' || expected !== prose(value)) return SKIP;
        const markdown = serialize({
          type: 'doc',
          content: [
            paragraph(textNode('Claim.'), { type: 'citation', attrs: { id: 'src-1' } }),
            { type: 'footnotes', content: [{ type: 'footnoteDefinition', attrs: { id: 'src-1' }, content: [textNode(value)] }] },
          ],
        });
        expect(lines(markdown)).toHaveLength(3);
        const definition = codec.parse(markdown).doc.content?.[1]?.content?.[0];
        expect(definition?.type).toBe('footnoteDefinition');
        expect(tiptapText(definition ?? {})).toBe(expected);
        return undefined;
      });
    },
    TIMEOUT_MS,
  );
});
