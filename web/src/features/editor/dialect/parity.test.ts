/**
 * Targeted parity cases between TipTap (marked plus the dialect tokenizers)
 * and the server's reading, plus the known divergences.
 *
 * Each case pins the normalization and the block types TipTap reads. The
 * expected outputs are the Phase 0 spike's, which a markdown-it oracle
 * configured like the server (commonmark preset, `html: false`, tables, task
 * lists, footnotes without inline footnotes, strict `:::note|tip|warning`
 * containers) read as the same document as the source, case by case
 * (`spikes/editor-dialect/web/test/parity.test.ts`). Matching them byte for
 * byte keeps that parity here without markdown-it in web/.
 */
import type { JSONContent } from '@tiptap/core';
import { afterAll, describe, expect, test } from 'vitest';

import { ArticleCodec } from './codec';

const codec = new ArticleCodec();
afterAll(() => {
  codec.destroy();
});

function types(doc: JSONContent): string[] {
  return (doc.content ?? []).map((node) => node.type ?? '?').filter((type, index, all) => {
    // TipTap's trailing node: an empty paragraph after a closing block.
    const last = index === all.length - 1;
    return !(last && type === 'paragraph' && (doc.content?.[index]?.content ?? []).length === 0);
  });
}

type Case = [name: string, source: string, normalized: string, blocks: string[]];

/** markdown-it reads each source and its normalization as the same document. */
const PARITY: Case[] = [
  ['callout basic', ':::note\ntext\n:::\n', ':::note\ntext\n:::\n', ['callout']],
  ['callout spaces around the type', ':::  tip  \ntext\n:::\n', ':::tip\ntext\n:::\n', ['callout']],
  ['callout longer closing fence', ':::warning\ntext\n:::::\n', ':::warning\ntext\n:::\n', ['callout']],
  ['callout unclosed runs to the end', ':::note\ntext\n\nmore\n', ':::note\ntext\n\nmore\n:::\n', ['callout']],
  ['callout interrupts a paragraph', 'para\n:::note\ntext\n:::\n', 'para\n\n:::note\ntext\n:::\n', ['paragraph', 'callout']],
  ['callout title not allowed', ':::note Title\ntext\n:::\n', '\\:::note Title text :::\n', ['paragraph']],
  ['callout unknown type', ':::danger\ntext\n:::\n', '\\:::danger text :::\n', ['paragraph']],
  ['callout indented 4 spaces is code', '    :::note\n    text\n', '```\n:::note\ntext\n```\n', ['codeBlock']],
  ['callout shorter inner fence is content', '::::note\n:::\n::::\n', ':::note\n\\:::\n:::\n', ['callout']],
  ['callout ::: inside a fence closes early', ':::note\n```\n:::\n```\n:::\n', ':::note\n```\n```\n:::\n\n```\n:::\n```\n', ['callout', 'codeBlock']],
  ['callout nested in a list item', '- item\n\n  :::note\n  inside\n  :::\n', '- item\n\n  inside\n', ['bulletList']],
  ['citation defined reference', 'Claim.[^a]\n\n[^a]: Source.\n', 'Claim.[^a]\n\n[^a]: Source.\n', ['paragraph', 'footnotes']],
  ['citation undefined reference stays text', 'Claim.[^a]\n', 'Claim.\\[^a\\]\n', ['paragraph']],
  ['citation inline link wins', '[^a](https://example.com)\n\n[^a]: Source.\n', '[^a](https://example.com)\n\n[^a]: Source.\n', ['paragraph', 'footnotes']],
  ['citation labels may hold punctuation', 'Claim.[^a.b_c:1]\n\n[^a.b_c:1]: Source.\n', 'Claim.[^a.b_c:1]\n\n[^a.b_c:1]: Source.\n', ['paragraph', 'footnotes']],
  ['citation definition before reference', '[^a]: Source.\n\nClaim.[^a]\n', 'Claim.[^a]\n\n[^a]: Source.\n', ['paragraph', 'footnotes']],
  ['citation definition interrupts a paragraph', 'Claim.[^a]\n[^a]: Source.\n', 'Claim.[^a]\n\n[^a]: Source.\n', ['paragraph', 'footnotes']],
  ['citation lazy continuation line', 'Claim.[^a]\n\n[^a]: Source\ncontinued.\n', 'Claim.[^a]\n\n[^a]: Source continued.\n', ['paragraph', 'footnotes']],
  ['citation empty first line, indented text', 'Claim.[^a]\n\n[^a]:\n    Source.\n', 'Claim.[^a]\n\n[^a]: Source.\n', ['paragraph', 'footnotes']],
  ['citation duplicate definition: last wins', 'Claim.[^a]\n\n[^a]: First.\n[^a]: Second.\n', 'Claim.[^a]\n\n[^a]: Second.\n', ['paragraph', 'footnotes']],
  ['citation definition inside a callout', ':::note\nClaim.[^a]\n\n[^a]: Source.\n:::\n', ':::note\nClaim.[^a]\n:::\n\n[^a]: Source.\n', ['callout', 'footnotes']],
  ['citation unreferenced definition is kept', 'Text.\n\n[^a]: Unused.\n', 'Text.\n\n[^a]: Unused.\n', ['paragraph', 'footnotes']],
  ['citation empty definition', 'Claim.[^a]\n\n[^a]:\n', 'Claim.[^a]\n\n[^a]:\n', ['paragraph', 'footnotes']],
  ['mermaid lowercase', '```mermaid\ngraph TD\n```\n', '```mermaid\ngraph TD\n```\n', ['mermaid']],
  ['mermaid any case, extra words', '```Mermaid title="x"\ngraph TD\n```\n', '```mermaid\ngraph TD\n```\n', ['mermaid']],
  ['mermaid tilde fence', '~~~mermaid\ngraph TD\n~~~\n', '```mermaid\ngraph TD\n```\n', ['mermaid']],
  ['mermaid indented code is not a diagram', '    graph TD\n', '```\ngraph TD\n```\n', ['codeBlock']],
  ['html img onerror', '<img src=x onerror=alert(1)>\n', '\\<img src=x onerror=alert(1)>\n', ['paragraph']],
  ['html script block', '<script>alert(1)</script>\n', '\\<script>alert(1)\\</script>\n', ['paragraph']],
  ['html inline tags keep inner Markdown', 'a <b>*em*</b> b\n', 'a \\<b>*em*\\</b> b\n', ['paragraph']],
  ['html comment', 'a <!-- hidden --> b\n', 'a \\<!-- hidden --> b\n', ['paragraph']],
  ['html javascript link stays a link', '[x](javascript:alert(1))\n', '[x](javascript:alert(1))\n', ['paragraph']],
  ['cm code span strips one space each side', '` a `\n', '`a`\n', ['paragraph']],
  ['cm code span keeps inner spaces', '`  a  `\n', '`  a  `\n', ['paragraph']],
  ['cm entities are decoded', '&copy; &eacute; &#35; &#x26; &amp;lt;\n', '© é # & \\&lt;\n', ['paragraph']],
  ['cm unescaped pipe in code splits a table cell', '| a |\n| - |\n| `x|y` |\n', '| a |\n| --- |\n| \\`x |\n', ['table']],
  ['cm ordered list delimiter change starts a new list', '1. a\n2) b\n', '1. a\n\n2) b\n', ['orderedList', 'orderedList']],
  ['cm table interrupts a paragraph', 'para\n| a |\n| - |\n', 'para\n\n| a |\n| --- |\n', ['paragraph', 'table']],
  ['cm setext heading', 'Title\n=====\n', '# Title\n', ['heading']],
  ['cm hard break with spaces', 'a  \nb\n', 'a\\\nb\n', ['paragraph']],
  ['cm bare URL is text', 'see https://example.com\n', 'see https://example.com\n', ['paragraph']],
  ['cm strikethrough is text', '~~gone~~\n', '\\~~gone~~\n', ['paragraph']],
  ['cm image inside text', 'an ![icon](/i.png) inline\n', 'an ![icon](/i.png) inline\n', ['paragraph']],
  ['nbsp after ##', '##\u00A0not a heading\n', '\\##\u00A0not a heading\n', ['paragraph']],
  ['ideographic space after ###', '###\u3000not a heading\n', '\\###\u3000not a heading\n', ['paragraph']],
  ['nbsp after #', '#\u00A0not a heading\n', '\\#\u00A0not a heading\n', ['paragraph']],
  ['tilde fence backtick in the only word', '~~~a`b\n# not a heading\n- nor a list\n~~~\n', '```\n# not a heading\n- nor a list\n```\n', ['codeBlock']],
  ['tilde fence backtick in a later word', '~~~python `x`\nprint(1)\n~~~\n', '```python\nprint(1)\n```\n', ['codeBlock']],
];

/**
 * Links inside link text. CommonMark lets the inner link win and keeps the
 * outer brackets as text; stock marked kept both, which the schema cannot
 * hold, so these sources failed to open. Fixed in this port (marked.ts) and
 * checked against the same markdown-it oracle before pinning.
 */
const NESTED_LINKS: Case[] = [
  ['a link as the whole link text', '[[a](x)](y)\n', '\\[[a](x)\\](y)\n', ['paragraph']],
  ['a link inside link text', '[a [b](x) c](y)\n', '\\[a [b](x) c\\](y)\n', ['paragraph']],
  [
    'a reference link inside link text',
    '[a [b][r] c](y)\n\n[r]: https://r.example\n',
    '\\[a [b](https://r.example) c\\](y)\n',
    ['paragraph'],
  ],
  [
    'in running text',
    'see [the [RFC](https://r.example) text](https://x.example) here\n',
    'see \\[the [RFC](https://r.example) text\\](https://x.example) here\n',
    ['paragraph'],
  ],
  ['inside emphasis', '*[a [b](x)](y)*\n', '*\\[a [b](x)\\](y)*\n', ['paragraph']],
];

/**
 * Known divergences: markdown-it reads the source differently from its
 * normalization. All are input outside the dialect that the server's linter
 * rejects (raw-html, citation-definition-blocks, linked-image,
 * citation-in-link, ...). Pinned so a dependency upgrade that changes one is
 * noticed.
 */
const DIVERGENT: Case[] = [
  ['html block line', 'text\n<div>\nmore\n', 'text\n\n\\<div> more\n', ['paragraph', 'paragraph']],
  ['block content in a definition', 'Claim.[^a]\n\n[^a]: - item\n', 'Claim.[^a]\n\n[^a]: \\- item\n', ['paragraph', 'footnotes']],
  ['nbsp heading interrupts a paragraph', 'para\n##\u00A0not a heading\n', 'para\n\n\\##\u00A0not a heading\n', ['paragraph', 'paragraph']],
  ['linked image', '[![Alt](/blog-media/a/b.png)](https://example.com)\n', '![Alt](/blog-media/a/b.png)\n', ['paragraph']],
  ['c1 numeric reference', '&#128;\n', '€\n', ['paragraph']],
  ['citation inside link text', '[see [^a]](https://example.com)\n\n[^a]: Source.\n', '[see](https://example.com) [^a]\n\n[^a]: Source.\n', ['paragraph', 'footnotes']],
];

describe('parity with the server reading', () => {
  test.each(PARITY)('%s', (_name, source, normalized, blocks) => {
    const result = codec.normalize(source);
    expect(result).toBe(normalized);
    expect(types(codec.parse(source).doc)).toEqual(blocks);
    expect(codec.normalize(result)).toBe(result);
  });
});

describe('links inside link text (the inner link wins)', () => {
  test.each(NESTED_LINKS)('%s', (_name, source, normalized, blocks) => {
    const result = codec.normalize(source);
    expect(result).toBe(normalized);
    expect(types(codec.parse(source).doc)).toEqual(blocks);
    expect(codec.normalize(result)).toBe(result);
  });
});

describe('known divergences (input outside the dialect)', () => {
  test.each(DIVERGENT)('%s', (_name, source, normalized, blocks) => {
    const result = codec.normalize(source);
    expect(result).toBe(normalized);
    expect(types(codec.parse(source).doc)).toEqual(blocks);
    expect(codec.normalize(result)).toBe(result);
  });
});

describe('callouts', () => {
  test('a callout whose content has a ::: line is written with a longer fence', () => {
    const doc = {
      type: 'doc',
      content: [
        {
          type: 'callout',
          attrs: { type: 'note' },
          content: [{ type: 'codeBlock', content: [{ type: 'text', text: ':::\n::::' }] }],
        },
      ],
    };
    expect(codec.serialize({ frontmatter: null, doc })).toBe(':::::note\n```\n:::\n::::\n```\n:::::\n');
  });

  test('a callout nested in a list item is lifted (callouts are top-level only)', () => {
    expect(JSON.stringify(codec.parse('- item\n\n  :::note\n  inside\n  :::\n').doc)).not.toContain('"callout"');
  });
});

describe('adjacent lists of the same kind', () => {
  const paragraph = (text: string): JSONContent => ({ type: 'paragraph', content: [{ type: 'text', text }] });
  const item = (type: string, text: string): JSONContent => ({
    type,
    ...(type === 'taskItem' ? { attrs: { checked: false } } : {}),
    content: [paragraph(text)],
  });
  const list = (type: string, text: string): JSONContent => ({
    type,
    content: [item(type === 'taskList' ? 'taskItem' : 'listItem', text)],
  });

  test('alternate markers keep them apart (lossless)', () => {
    const doc = {
      type: 'doc',
      content: [
        list('bulletList', 'a'),
        list('bulletList', 'b'),
        list('taskList', 'c'),
        list('orderedList', 'd'),
        list('orderedList', 'e'),
      ],
    };
    const result = codec.check({ frontmatter: null, doc });
    expect(result.markdown).toBe('- a\n\n* b\n\n- [ ] c\n\n1. d\n\n1) e\n');
    expect(result.lossless).toBe(true);
    expect(types(result.reparsed.doc)).toEqual(['bulletList', 'bulletList', 'taskList', 'orderedList', 'orderedList']);
  });

  test('a list after a paragraph uses the canonical marker again', () => {
    const doc = { type: 'doc', content: [list('bulletList', 'a'), paragraph('p'), list('bulletList', 'b')] };
    expect(codec.serialize({ frontmatter: null, doc })).toBe('- a\n\np\n\n- b\n');
  });
});

describe('documents Markdown cannot represent (detected by check)', () => {
  test('a code span with a backslash before a pipe in a table cell loses the backslash', () => {
    const cell = (type: string, content: JSONContent[]): JSONContent => ({ type, content: [{ type: 'paragraph', content }] });
    const doc = {
      type: 'doc',
      content: [
        { type: 'paragraph', content: [{ type: 'text', text: 'Intro.' }] },
        {
          type: 'table',
          content: [
            { type: 'tableRow', content: [cell('tableHeader', [{ type: 'text', text: 'h' }])] },
            { type: 'tableRow', content: [cell('tableCell', [{ type: 'text', text: 'a\\|b', marks: [{ type: 'code' }] }])] },
          ],
        },
      ],
    };
    const result = codec.check({ frontmatter: null, doc });
    expect(result.lossless).toBe(false);
    expect(result.warnings).toEqual([
      { code: 'content-changed', message: expect.stringContaining('block 2') as unknown, blocks: { from: 1, to: 2 } },
    ]);
  });

  test('bold covering part of an inline code span cannot be expressed', () => {
    const doc = {
      type: 'doc',
      content: [
        {
          type: 'paragraph',
          content: [
            { type: 'text', text: 'ab', marks: [{ type: 'bold' }, { type: 'code' }] },
            { type: 'text', text: 'cd', marks: [{ type: 'code' }] },
          ],
        },
      ],
    };
    expect(codec.check({ frontmatter: null, doc }).lossless).toBe(false);
  });

  test('overlapping bold and italic: TipTap falls back to raw HTML, which the dialect reads as text', () => {
    const doc = {
      type: 'doc',
      content: [
        {
          type: 'paragraph',
          content: [
            { type: 'text', text: '123', marks: [{ type: 'bold' }] },
            { type: 'text', text: '456', marks: [{ type: 'bold' }, { type: 'italic' }] },
            { type: 'text', text: '789', marks: [{ type: 'italic' }] },
          ],
        },
      ],
    };
    const result = codec.check({ frontmatter: null, doc });
    expect(result.markdown).toBe('**123*456***<em>789</em>\n');
    expect(result.lossless).toBe(false);
  });
});
