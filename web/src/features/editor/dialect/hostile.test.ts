/**
 * Hostile input: editor states that never come from parsing Markdown, and
 * sources built to hurt the parser.
 *
 * Parsed documents never hold a line ending inside a text node or an
 * attribute, but edited ones can: `tr.insertText` (an "accept AI proposal"
 * button), `insertContent` with `preserveWhitespace`, pasted HTML attributes
 * (`data-citation`, `class="language-..."`, `href="...&#13;..."`) or JSON.
 * The serializer must never let such a value start a new Markdown line,
 * where it would become a heading, a list item, a fence or a citation
 * definition. The expected Markdown is the spike's (the server's markdown-it
 * read each one as exactly the blocks the document has); here the
 * Markdown is pinned and TipTap's reading is checked.
 *
 * Sources over the editor's limits fail fast with a typed error instead of
 * freezing the tab or overflowing the stack.
 */
import { Editor, type JSONContent } from '@tiptap/core';
import { afterAll, describe, expect, test } from 'vitest';

import { ArticleCodec } from './codec';
import { DialectError, toDialectError } from './errors';
import { createArticleExtensions } from './kit';
import { EDITOR_MAX_BYTES, MAX_NESTING_DEPTH } from './limits';

const codec = new ArticleCodec();
afterAll(() => {
  codec.destroy();
});

const INJECTED = '\n# Injected heading\n- injected item\n\n[^x]: injected definition\n```\nfence';
const FLATTENED = '# Injected heading - injected item \\[^x\\]: injected definition \\`\\`\\` fence';

function serialize(doc: JSONContent): string {
  return codec.serialize({ frontmatter: null, doc });
}

function editorBlocks(markdown: string): string[] {
  return (codec.parse(markdown).doc.content ?? [])
    .filter((node, index, all) => !(index === all.length - 1 && node.type === 'paragraph' && !node.content?.length))
    .map((node) => node.type ?? '?');
}

function allText(node: JSONContent): string {
  return node.type === 'text' ? (node.text ?? '') : (node.content ?? []).map(allText).join('');
}

/** No line of the Markdown starts one of the injected blocks. */
function expectNoInjectedBlocks(markdown: string): void {
  expect(markdown).not.toMatch(/^(?:[ >]*)# Injected/m);
  expect(markdown).not.toMatch(/^(?:[ >]*)- injected/m);
  expect(markdown).not.toMatch(/^(?:[ >]*)\[\^x\]:/m);
  expect(markdown).not.toMatch(/^(?:[ >]*)```\s*$/m);
}

const paragraph = (...content: JSONContent[]): JSONContent => ({ type: 'paragraph', content });
const text = (value: string, marks?: JSONContent['marks']): JSONContent => ({
  type: 'text',
  text: value,
  ...(marks ? { marks } : {}),
});

describe('line endings inside text nodes cannot create blocks', () => {
  test.each([
    ['LF', '\n'],
    ['CR', '\r'],
    ['CRLF', '\r\n'],
  ])('%s in a paragraph', (_name, eol) => {
    const doc = { type: 'doc', content: [paragraph(text(`Before${INJECTED.replaceAll('\n', eol)} after`))] };
    const markdown = serialize(doc);
    expect(markdown).toBe(`Before ${FLATTENED} after\n`);
    expect(editorBlocks(markdown)).toEqual(['paragraph']);
    expect(allText(codec.parse(markdown).doc)).toBe(
      'Before # Injected heading - injected item [^x]: injected definition ``` fence after',
    );
    // The visible line breaks are gone, so the editor must still warn before saving.
    const result = codec.check({ frontmatter: null, doc });
    expect(result.lossless).toBe(false);
    expect(result.warnings.map((warning) => warning.code)).toEqual(['content-changed']);
  });

  test.each<[string, JSONContent, string, string[]]>([
    [
      'heading',
      { type: 'heading', attrs: { level: 2 }, content: [text(`Title${INJECTED}`)] },
      `## Title ${FLATTENED}\n`,
      ['heading'],
    ],
    [
      'list item',
      { type: 'bulletList', content: [{ type: 'listItem', content: [paragraph(text(`Item${INJECTED}`))] }] },
      `- Item ${FLATTENED}\n`,
      ['bulletList'],
    ],
    [
      'task item',
      {
        type: 'taskList',
        content: [{ type: 'taskItem', attrs: { checked: false }, content: [paragraph(text(`Task${INJECTED}`))] }],
      },
      `- [ ] Task ${FLATTENED}\n`,
      ['taskList'],
    ],
    [
      'blockquote',
      { type: 'blockquote', content: [paragraph(text(`Quote${INJECTED}`))] },
      `> Quote ${FLATTENED}\n`,
      ['blockquote'],
    ],
    [
      'callout',
      { type: 'callout', attrs: { type: 'tip' }, content: [paragraph(text(`Tip${INJECTED}`))] },
      `:::tip\nTip ${FLATTENED}\n:::\n`,
      ['callout'],
    ],
    [
      'table cell',
      {
        type: 'table',
        content: [
          { type: 'tableRow', content: [{ type: 'tableHeader', content: [paragraph(text('h'))] }] },
          { type: 'tableRow', content: [{ type: 'tableCell', content: [paragraph(text(`Cell${INJECTED}`))] }] },
        ],
      },
      `| h |\n| --- |\n| Cell ${FLATTENED} |\n`,
      ['table'],
    ],
    [
      'inline code',
      paragraph(text('a '), text(`code${INJECTED}`, [{ type: 'code' }])),
      'a `code # Injected heading - injected item  [^x]: injected definition ``` fence`\n',
      ['paragraph'],
    ],
    [
      'bold text',
      paragraph(text('a '), text(`bold${INJECTED}`, [{ type: 'bold' }])),
      `a **bold ${FLATTENED}**\n`,
      ['paragraph'],
    ],
    [
      'link text',
      paragraph(text(`link${INJECTED}`, [{ type: 'link', attrs: { href: 'https://example.com' } }])),
      '[link # Injected heading - injected item &#91;^x&#93;: injected definition \\`\\`\\` fence](https://example.com)\n',
      ['paragraph'],
    ],
  ])('%s', (_name, block, expected, blocks) => {
    const markdown = serialize({ type: 'doc', content: [block] });
    expect(markdown).toBe(expected);
    expectNoInjectedBlocks(markdown);
    expect(editorBlocks(markdown)).toEqual(blocks);
  });

  test('in a footnote definition', () => {
    const markdown = serialize({
      type: 'doc',
      content: [
        paragraph(text('Claim.'), { type: 'citation', attrs: { id: 'src' } }),
        {
          type: 'footnotes',
          content: [{ type: 'footnoteDefinition', attrs: { id: 'src' }, content: [text(`Source${INJECTED}`)] }],
        },
      ],
    });
    expect(markdown).toBe(`Claim.[^src]\n\n[^src]: Source ${FLATTENED}\n`);
    expect(editorBlocks(markdown)).toEqual(['paragraph', 'footnotes']);
  });

  test('inline code keeps its edge spaces when its edges were line endings', () => {
    const markdown = serialize({ type: 'doc', content: [paragraph(text('a '), text('\nx\n', [{ type: 'code' }]), text(' b'))] });
    expect(markdown).toBe('a `  x  ` b\n');
    const code = codec.parse(markdown).doc.content?.[0]?.content?.find((node) => node.marks?.some((mark) => mark.type === 'code'));
    expect(code?.text).toBe(' x ');
  });
});

describe('attributes cannot break out of their syntax', () => {
  test.each([
    ['CR', 'https://example.com/a\r# Injected', '[see](<https://example.com/a%0D# Injected>)\n'],
    ['LF', 'https://example.com/a\n# Injected', '[see](<https://example.com/a%0A# Injected>)\n'],
  ])('a %s in a link destination is percent-encoded', (_name, href, expected) => {
    const markdown = serialize({ type: 'doc', content: [paragraph(text('see', [{ type: 'link', attrs: { href } }]))] });
    expect(markdown).toBe(expected);
    expect(editorBlocks(markdown)).toEqual(['paragraph']);
  });

  test('a link title is written on one line', () => {
    const markdown = serialize({
      type: 'doc',
      content: [paragraph(text('see', [{ type: 'link', attrs: { href: 'https://example.com', title: 'one\n\n# two' } }]))],
    });
    expect(markdown).toBe('[see](https://example.com "one # two")\n');
    const link = codec.parse(markdown).doc.content?.[0]?.content?.[0]?.marks?.[0];
    expect(link?.attrs).toMatchObject({ href: 'https://example.com', title: 'one # two' });
  });

  test.each([
    ['a backtick', 'a`b', '```\n# not a heading\n- nor a list\n```\n'],
    ['a backtick after the language', 'ts `x`', '```ts\n# not a heading\n- nor a list\n```\n'],
    ['a line break', 'ts\n# Injected', '```ts # Injected\n# not a heading\n- nor a list\n```\n'],
  ])('a code block language with %s keeps the fence intact', (_name, language, expected) => {
    const markdown = serialize({
      type: 'doc',
      content: [{ type: 'codeBlock', attrs: { language }, content: [text('# not a heading\n- nor a list')] }],
    });
    expect(markdown).toBe(expected);
    expect(editorBlocks(markdown)).toEqual(['codeBlock']);
    expect(allText(codec.parse(markdown).doc)).toBe('# not a heading\n- nor a list');
  });

  test.each([
    ['a newline', 'x\n\n# Injected', '[^x--#-Injected]'],
    ['a bracket and a link', 'x](https://evil.example) [y', '[^x-(https://evil.example)-[y]'],
    ['a space', 'two words', '[^two-words]'],
  ])('a citation id with %s stays one reference, paired with its definition', (_name, id, label) => {
    const doc = {
      type: 'doc',
      content: [
        paragraph(text('Claim.'), { type: 'citation', attrs: { id } }),
        { type: 'footnotes', content: [{ type: 'footnoteDefinition', attrs: { id }, content: [text('Source.')] }] },
      ],
    };
    const markdown = serialize(doc);
    expect(markdown).toBe(`Claim.${label}\n\n${label}: Source.\n`);
    const parsed = codec.parse(markdown).doc;
    expect(editorBlocks(markdown)).toEqual(['paragraph', 'footnotes']);
    expect(parsed.content?.[0]?.content?.filter((node) => node.type === 'citation')).toHaveLength(1);
    expect(JSON.stringify(parsed)).not.toContain('"link"');
  });
});

describe('real editor input paths', () => {
  const editor = new Editor({ element: null, extensions: createArticleExtensions() });
  afterAll(() => {
    editor.destroy();
  });

  test('tr.insertText with line endings (an "accept proposal" button)', () => {
    editor.commands.setContent('<p>Start</p>');
    editor.commands.command(({ tr }) => {
      tr.insertText(' and\n- injected item\n\n[^x]: injected definition', 6);
      return true;
    });
    expect(editor.getMarkdown()).toBe('Start and - injected item \\[^x\\]: injected definition\n');
  });

  test('insertContent with preserveWhitespace', () => {
    editor.commands.setContent('<p>Start</p>');
    editor.commands.insertContentAt(6, ' then\n## Injected', { parseOptions: { preserveWhitespace: 'full' } });
    expect(editor.getMarkdown()).toBe('Start then ## Injected\n');
  });

  test('pasted HTML: data-citation, a language class with a backtick, an href with &#13;', () => {
    editor.commands.setContent(
      '<p>Claim<sup data-citation="x&#10;&#10;# Injected">[x]</sup> <a href="https://example.com/a&#13;# Injected">link</a></p>' +
        '<pre><code class="language-a`b"># not a heading</code></pre>',
    );
    const markdown = editor.getMarkdown();
    expect(markdown).toBe('Claim[^x--#-Injected] [link](<https://example.com/a%0D# Injected>)\n\n```\n# not a heading\n```\n');
    expect(editorBlocks(markdown)).toEqual(['paragraph', 'codeBlock']);
  });

  test('Markdown inserted through the editor obeys the input limits', () => {
    expect(() => editor.commands.insertContent('>'.repeat(10_000), { contentType: 'markdown' })).toThrow(DialectError);
  });
});

describe('parsing cost', () => {
  // The citation tokenizer's start() scanned the rest of the source on every
  // text step: 16 KB of "[" took 1.1 s, 64 KB took 18 s (quadratic). Now ~20 ms.
  test('64 KB of brackets parses in linear time', () => {
    const source = `${'['.repeat(64 * 1024)}\n`;
    const started = performance.now();
    const markdown = codec.normalize(source);
    expect(performance.now() - started).toBeLessThan(3000);
    expect(markdown).toBe(`${'\\['.repeat(64 * 1024)}\n`);
  });

  test('citations are still found after many brackets', () => {
    expect(codec.normalize(`${'['.repeat(500)} claim[^a]\n\n[^a]: Source.\n`)).toBe(
      `${'\\['.repeat(500)} claim[^a]\n\n[^a]: Source.\n`,
    );
  });
});

describe('input limits', () => {
  function codeOf(action: () => unknown): string | undefined {
    try {
      action();
    } catch (error) {
      return error instanceof DialectError ? error.code : `not a DialectError: ${String(error)}`;
    }
    return undefined;
  }

  test('a source of exactly 128 KB opens; one byte more is refused', () => {
    const fits = `${'a'.repeat(EDITOR_MAX_BYTES - 1)}\n`;
    expect(codeOf(() => codec.parse(fits))).toBeUndefined();
    expect(codeOf(() => codec.parse(`a${fits}`))).toBe('too-large');
  });

  test('the limit counts UTF-8 bytes, not characters', () => {
    // 65,537 characters, 131,074 bytes.
    expect(codeOf(() => codec.parse('é'.repeat(EDITOR_MAX_BYTES / 2 + 1)))).toBe('too-large');
  });

  test.each([
    ['8 KB of ">"', `${'>'.repeat(8 * 1024)} deep\n`],
    ['2,000 stacked callouts', ':::note\n'.repeat(2000)],
    ['300 list levels', Array.from({ length: 300 }, (_, level) => `${'  '.repeat(level)}- item`).join('\n')],
    ['200 ordered list levels', Array.from({ length: 200 }, (_, level) => `${'   '.repeat(level)}1. item`).join('\n')],
    ['quotes and lists alternating', `${'> - '.repeat(2000)}deep\n`],
  ])('%s is refused as too deep, quickly and without a stack overflow', (_name, source) => {
    const started = performance.now();
    expect(codeOf(() => codec.parse(source))).toBe('too-deep');
    expect(performance.now() - started).toBeLessThan(1000);
  });

  test(`nesting up to ${MAX_NESTING_DEPTH} levels still opens`, () => {
    const quotes = `${'> '.repeat(MAX_NESTING_DEPTH)}deep\n`;
    expect(codeOf(() => codec.parse(quotes))).toBeUndefined();
    expect(codeOf(() => codec.parse(`>${quotes}`))).toBe('too-deep');
  });
});

describe('typed errors', () => {
  test('invalid frontmatter is a FrontmatterError with every issue', () => {
    expect(() => codec.parse('---\ntitle: "x"\n---\n\nBody\n')).toThrow(
      expect.objectContaining({ code: 'invalid-frontmatter', name: 'FrontmatterError' }),
    );
    expect(() => codec.parse('---\ntitle: "x"\n')).toThrow(expect.objectContaining({ code: 'invalid-frontmatter' }));
  });

  test('content the schema cannot hold is invalid-content', () => {
    expect(() => codec.serialize({ frontmatter: null, doc: { type: 'doc', content: [{ type: 'nonsense' }] } })).toThrow(
      expect.objectContaining({ code: 'invalid-content' }),
    );
    expect(() =>
      codec.serialize({ frontmatter: null, doc: { type: 'doc', content: [{ type: 'footnoteDefinition' }] } }),
    ).toThrow(expect.objectContaining({ code: 'invalid-content' }));
  });

  test.each([
    ['a V8 or JavaScriptCore stack overflow', new RangeError('Maximum call stack size exceeded'), 'too-deep'],
    ['a Firefox stack overflow', new Error('too much recursion'), 'too-deep'],
    [
      'a wrapped stack overflow',
      new Error('[tiptap error]: Invalid JSON content', { cause: new RangeError('Maximum call stack size exceeded') }),
      'too-deep',
    ],
    [
      "TipTap's content check",
      new Error('[tiptap error]: Invalid JSON content', { cause: new RangeError('Invalid content for node doc') }),
      'invalid-content',
    ],
    ["ProseMirror's content check", new RangeError('Invalid content for node paragraph: <>'), 'invalid-content'],
    ['anything else', new TypeError('x is undefined'), 'internal'],
    ['a thrown string', 'boom', 'internal'],
  ])('toDialectError maps %s', (_name, thrown, code) => {
    const error = toDialectError(thrown);
    expect(error).toBeInstanceOf(DialectError);
    expect(error.code).toBe(code);
    expect(error.cause).toBe(thrown);
  });

  test('error messages never quote the article', () => {
    const secret = 'confidential-draft-sentence';
    const sources = [
      `${secret}\n${'>'.repeat(5000)}\n`,
      `${secret}${'x'.repeat(EDITOR_MAX_BYTES)}`,
      `---\ntitle: "${secret}"\n---\n`,
    ];
    for (const source of sources) {
      try {
        codec.parse(source);
        expect.unreachable();
      } catch (error) {
        expect(error).toBeInstanceOf(DialectError);
        expect((error as DialectError).message).not.toContain(secret);
      }
    }
  });
});

describe('empty and nearly empty documents', () => {
  test.each([
    ['an empty source', '', ''],
    ['blank lines only', '\n\n\n', ''],
    ['a lone citation definition', '[^a]: Source.\n', '[^a]: Source.\n'],
    [
      'frontmatter only',
      '---\ntitle: "T"\nslug: t\nexcerpt: "E"\npillar: agents\nformat: case-study\ntags: []\n---\n',
      '---\ntitle: "T"\nslug: t\nexcerpt: "E"\npillar: agents\nformat: case-study\ntags: []\n---\n',
    ],
  ])('%s opens and round-trips', (_name, source, normalized) => {
    expect(codec.normalize(source)).toBe(normalized);
    expect(codec.normalize(normalized)).toBe(normalized);
    expect(codec.check(source).lossless).toBe(true);
  });
});
