/**
 * Dialect overrides of stock TipTap v3 nodes and marks.
 *
 * Each override fixes a concrete round-trip or parity problem found while
 * running the golden corpus against `@tiptap/markdown` 3.35.0; the reason is
 * noted next to it and in `docs/v2/spikes/editor-dialect.md` ("Patches").
 */
import {
  decodeHtmlEntities,
  type JSONContent,
  type MarkdownParseHelpers,
  type MarkdownParseResult,
  type MarkdownRendererHelpers,
  type MarkdownToken,
  type MarkdownTokenizer,
  type RenderContext,
} from '@tiptap/core';
import { Code } from '@tiptap/extension-code';
import { CodeBlock } from '@tiptap/extension-code-block';
import { CodeBlockLowlight } from '@tiptap/extension-code-block-lowlight';
import { Document } from '@tiptap/extension-document';
import { HardBreak } from '@tiptap/extension-hard-break';
import { Heading } from '@tiptap/extension-heading';
import { Image } from '@tiptap/extension-image';
import { Link } from '@tiptap/extension-link';
import { BulletList, ListItem, OrderedList, TaskItem, TaskList } from '@tiptap/extension-list';
import { Paragraph } from '@tiptap/extension-paragraph';
import { Table } from '@tiptap/extension-table';
import { Text } from '@tiptap/extension-text';

import { CODE_PIPE_PLACEHOLDER } from '../constants';
import {
  bracketsAsReferences,
  codeFenceFor,
  escapeInlineText,
  fenceInfo,
  formatLinkDestination,
  formatLinkTitle,
  shortestUnusedBacktickRun,
} from '../escape';
import { attribute, isEmptyParagraph, stringAttribute, textContent } from '../json';

/**
 * Document: body blocks (including top-level-only callouts), then at most one
 * footnotes section. Blocks are separated by exactly one blank line and empty
 * paragraphs (TipTap's trailing node, blank-line paragraphs) are dropped.
 */
export const DialectDocument = Document.extend({
  content: '(block | topBlock)+ footnotes?',

  renderMarkdown: (node: JSONContent, helpers) => {
    const children = (node.content ?? []).filter((child) => !isEmptyParagraph(child));
    return helpers.renderChildren(children, '\n\n');
  },
});

/**
 * Text: TipTap only routes *block-level* `text` tokens through this handler
 * (inline ones are handled by the manager). marked emits those for the
 * paragraphs of tight list items; parse their inline tokens into a paragraph
 * instead of keeping the raw Markdown as text (stock TipTap's behaviour).
 */
export const DialectText = Text.extend({
  parseMarkdown: (token: MarkdownToken, helpers) => {
    if (token.tokens && token.tokens.length > 0) {
      return helpers.createNode('paragraph', undefined, helpers.parseInline(token.tokens));
    }
    return { type: 'text', text: decodeHtmlEntities(token.text ?? '') };
  },
});

/**
 * Paragraph: no `&nbsp;` "empty paragraph" convention (a non-breaking space is
 * content in CommonMark) and no unwrapping of image-only paragraphs (images
 * are inline, as in CommonMark; see DialectImage). Line starts and the end
 * are trimmed because CommonMark strips that whitespace on parse anyway.
 */
export const DialectParagraph = Paragraph.extend({
  parseMarkdown: (token, helpers) => helpers.createNode('paragraph', undefined, helpers.parseInline(token.tokens ?? [])),

  renderMarkdown: (node: JSONContent, helpers) => {
    const content = node.content ?? [];
    if (content.length === 0) {
      return '';
    }
    return trimParagraphLines(helpers.renderChildren(content));
  },
});

/**
 * Heading: keep every level when parsing (the linter, not the parser, rejects
 * h1, h5 and h6 in a body) and always emit the `#` run, even for an empty heading.
 */
export const DialectHeading = Heading.extend({
  // Stock `levels` already allows 1-6; this parser ignores the option anyway.
  parseMarkdown: (token, helpers) =>
    helpers.createNode('heading', { level: token.depth ?? 1 }, helpers.parseInline(token.tokens ?? [])),

  renderMarkdown: (node: JSONContent, helpers) => {
    const level = Number(attribute(node, 'level') ?? 1);
    const text = trimParagraphLines(helpers.renderChildren(node.content ?? []));
    return text === '' ? '#'.repeat(level) : `${'#'.repeat(level)} ${text}`;
  },
});

/**
 * Code block Markdown: accept every fenced or indented `code` token (stock
 * TipTap drops fences indented 1-3 spaces) and emit a backtick fence longer
 * than any backtick run inside the code (stock TipTap always emits ``` and
 * corrupts code that contains a fence). Shared by both code block variants.
 */
function parseCodeBlockMarkdown(token: MarkdownToken, helpers: MarkdownParseHelpers): MarkdownParseResult {
  if (token.type !== 'code') {
    return [];
  }
  const info = typeof token.lang === 'string' ? token.lang.trim() : '';
  const text = token.text ?? '';
  return helpers.createNode('codeBlock', { language: info === '' ? null : info }, text ? [helpers.createTextNode(text)] : []);
}

function renderCodeBlockMarkdown(node: JSONContent): string {
  const text = textContent(node);
  const language = fenceInfo(stringAttribute(node, 'language') ?? '');
  const fence = codeFenceFor(text);
  return text === '' ? `${fence}${language}\n${fence}` : `${fence}${language}\n${text}\n${fence}`;
}

/** Code block without highlighting: the headless codec and the normalize worker. */
export const DialectCodeBlock = CodeBlock.extend({
  parseMarkdown: parseCodeBlockMarkdown,
  renderMarkdown: renderCodeBlockMarkdown,
});

/**
 * Code block with lowlight highlighting for the visible editor. Same node
 * name, schema and Markdown as DialectCodeBlock; it only adds decorations.
 * Configure it with a lowlight instance (`createArticleExtensions({ lowlight })`).
 */
export const DialectCodeBlockLowlight = CodeBlockLowlight.extend({
  parseMarkdown: parseCodeBlockMarkdown,
  renderMarkdown: renderCodeBlockMarkdown,
});

/**
 * Inline code: may combine with other marks (`` **`x`** ``, a link around
 * code), always nests innermost, and is padded with one space when the
 * content starts or ends with a backtick or with a space on both sides
 * (CommonMark strips one such space when parsing). Stock TipTap moves the
 * spaces outside.
 */
export const DialectCode = Code.extend({
  excludes: '',

  priority: 50,

  renderMarkdown: (node: JSONContent, helpers, context) => {
    // Line endings are written as spaces (encodeDialectText); decide the padding on that text.
    const meta: unknown = context.meta?.markText;
    const markText = (typeof meta === 'string' ? meta : '').replace(/\r\n?|\n/g, ' ');
    const fence = '`'.repeat(shortestUnusedBacktickRun(markText));
    const spacePadded = markText.startsWith(' ') && markText.endsWith(' ') && /[^ ]/.test(markText);
    const padding = markText.startsWith('`') || markText.endsWith('`') || spacePadded ? ' ' : '';
    return `${fence}${padding}${helpers.renderChildren(node.content ?? [])}${padding}${fence}`;
  },
});

/** Hard break: a backslash before the newline (trailing double spaces are invisible and get stripped by editors). */
export const DialectHardBreak = HardBreak.extend({
  renderMarkdown: () => '\\\n',
});

/** Link: destinations and titles are escaped (stock TipTap writes them raw). */
export const DialectLink = Link.extend({
  renderMarkdown: (node: JSONContent, helpers) => {
    const href = stringAttribute(node, 'href') ?? '';
    const title = stringAttribute(node, 'title') ?? '';
    const text = helpers.renderChildren(node.content ?? []);
    return `[${text}](${formatLinkDestination(href)}${title ? ` ${formatLinkTitle(title)}` : ''})`;
  },
});

/**
 * Image: an inline node, exactly as in CommonMark (stock TipTap makes images
 * blocks and unwraps image-only paragraphs, so an image inside text breaks
 * the schema). The dialect's "an image stands alone in its paragraph" rule is
 * enforced by the linter, and the editor can style such paragraphs as figures.
 * `alt` is the plain-text content of the image description, as in CommonMark
 * (stock TipTap keeps the raw Markdown source, so `*` and `\` leak into the
 * alt attribute), and is escaped on the way out.
 */
export const DialectImage = Image.extend({
  parseMarkdown: (token, helpers) =>
    helpers.createNode('image', {
      src: typeof token.href === 'string' ? token.href : '',
      title: typeof token.title === 'string' ? token.title : null,
      alt: plainText(token.tokens ?? []),
    }),

  renderMarkdown: (node: JSONContent) => {
    const src = stringAttribute(node, 'src') ?? '';
    const alt = stringAttribute(node, 'alt') ?? '';
    const title = stringAttribute(node, 'title') ?? '';
    const altMarkdown = bracketsAsReferences(
      escapeInlineText(alt.replace(/\s+/g, ' ').trim(), { lineStart: false, headingEnd: false }),
    );
    return `![${altMarkdown}](${formatLinkDestination(src)}${title ? ` ${formatLinkTitle(title)}` : ''})`;
  },
});

/** A tokenizer that never matches: hands the syntax back to marked's CommonMark tokenizer. */
function disabledTokenizer(name: string): MarkdownTokenizer {
  return { name, level: 'block', start: () => -1, tokenize: () => undefined };
}

/**
 * Table: compact rows (`| a | b |`, no column padding, so an edit to one cell
 * does not rewrite the table), alignment kept, header row required (the first
 * row is promoted when the document has none), and every `|` inside a cell
 * escaped, including inside inline code, which GFM requires.
 *
 * Stock TipTap's tokenizer also "repairs" unescaped pipes inside code spans,
 * which markdown-it does not do; it is disabled so both read tables the same.
 */
export const DialectTable = Table.extend({
  renderMarkdown: (node: JSONContent, helpers) => renderTable(node, helpers),

  markdownTokenizer: disabledTokenizer('table'),
});

/**
 * Bullet list: also builds task lists. Stock TipTap uses its own task-list
 * tokenizer, which only reads the first line of an item (hard breaks and
 * continuation lines fall out of the item). marked's GFM list tokenizer
 * already flags task items, so they are built from marked's tokens here.
 *
 * A list mixing task and plain items (one list in CommonMark) cannot be held
 * by the schema; it is split into consecutive lists. The linter rejects such
 * lists, so this only keeps the editor from losing content.
 */
export const DialectBulletList = BulletList.extend({
  parseMarkdown: (token, helpers) => {
    if (token.type !== 'list' || token.ordered) {
      return [];
    }

    const groups: { task: boolean; items: MarkdownToken[] }[] = [];
    for (const item of token.items ?? []) {
      const task = item.task === true;
      const last = groups.at(-1);
      if (last?.task === task) {
        last.items.push(item);
      } else {
        groups.push({ task, items: [item] });
      }
    }

    return groups.map((group) =>
      group.task
        ? { type: 'taskList', content: group.items.map((item) => taskItemFromListItem(item, helpers)) }
        : { type: 'bulletList', content: helpers.parseChildren(group.items) },
    );
  },
});

/**
 * Ordered list: marked's tokenizer instead of TipTap's custom one, which
 * folds tables and other blocks inside items into the item paragraph and
 * ignores the `.` / `)` delimiter change that starts a new list.
 */
export const DialectOrderedList = OrderedList.extend({
  markdownTokenizer: disabledTokenizer('orderedList'),

  parseMarkdown: (token, helpers) => {
    if (token.type !== 'list' || !token.ordered) {
      return [];
    }

    const start = typeof token.start === 'number' ? token.start : 1;
    const content = helpers.parseChildren(token.items ?? []);
    return start === 1 ? { type: 'orderedList', content } : { type: 'orderedList', attrs: { start }, content };
  },
});

/**
 * Task list: built by DialectBulletList from marked's list tokens. The custom
 * tokenizer is disabled, and the parse handler is registered under an unused
 * token name so the manager does not route mixed lists through TipTap's
 * first-line-only task item parser.
 */
export const DialectTaskList = TaskList.extend({
  markdownTokenName: 'dialectTaskListUnused',
  markdownTokenizer: disabledTokenizer('taskList'),
});

/** The parent list's attributes, as the manager passes them to item renderers. */
function parentAttributes(context: RenderContext): Record<string, unknown> {
  const attrs: unknown = context.meta?.parentAttrs;
  return typeof attrs === 'object' && attrs !== null ? (attrs as Record<string, unknown>) : {};
}

function bulletMarker(attrs: Record<string, unknown>): string {
  return typeof attrs.markdownBullet === 'string' ? attrs.markdownBullet : '-';
}

/** List item: continuation lines and nested blocks are indented to the content column; blank lines stay empty. */
export const DialectListItem = ListItem.extend({
  renderMarkdown: (node: JSONContent, helpers, context) => {
    const listAttrs = parentAttributes(context);
    let marker = `${bulletMarker(listAttrs)} `;
    if (context.parentType === 'orderedList') {
      const start = Number(listAttrs.start ?? 1);
      const delimiter = typeof listAttrs.markdownDelimiter === 'string' ? listAttrs.markdownDelimiter : '.';
      marker = `${start + context.index}${delimiter} `;
    }
    return renderListItem(node, helpers, marker, ' '.repeat(marker.length));
  },
});

export const DialectTaskItem = TaskItem.extend({
  renderMarkdown: (node: JSONContent, helpers, context) => {
    const bullet = bulletMarker(parentAttributes(context));
    return renderListItem(node, helpers, `${bullet} [${attribute(node, 'checked') ? 'x' : ' '}] `, '  ');
  },
});

/**
 * An item with an empty first paragraph is written `- &nbsp;`: a bare nested
 * `-` under a paragraph would be read as a setext heading underline, and an
 * empty item cannot interrupt a paragraph. Parsing maps it back to an empty
 * paragraph (transforms.ts).
 */
export const EMPTY_ITEM_TEXT = '&nbsp;';

function renderListItem(node: JSONContent, helpers: MarkdownRendererHelpers, marker: string, indent: string): string {
  const children = node.content ?? [];
  const [first, ...rest] = children;
  const renderedFirst = first ? helpers.renderChildren([first]) : '';
  const main = renderedFirst.trim() === '' ? EMPTY_ITEM_TEXT : renderedFirst;
  let output = `${marker}${indentLines(main, indent, false)}`;

  let previous = first;
  rest.forEach((child, index) => {
    const rendered = helpers.renderChild?.(child, index + 1) ?? helpers.renderChildren([child]);
    output += `${previous ? blockSeparator(previous, child) : '\n\n'}${indentLines(rendered, indent, true)}`;
    previous = child;
  });

  return output;
}

/**
 * Separator between two blocks inside a list item. A single newline keeps the
 * list tight, but is only safe where the second block can interrupt the
 * first: lists, fences, quotes and tables interrupt a paragraph (an ordered
 * list only when it starts at 1), and anything can follow a closed fence.
 * Everywhere else a blank line is required (a thematic break would turn the
 * paragraph into a setext heading; a line after a list, quote or table would
 * be a lazy continuation of it).
 */
function blockSeparator(previous: JSONContent, next: JSONContent): string {
  if (previous.type === 'codeBlock' || previous.type === 'mermaid') {
    return '\n';
  }

  if (previous.type === 'paragraph') {
    switch (next.type ?? '') {
      case 'bulletList':
      case 'taskList':
      case 'codeBlock':
      case 'mermaid':
      case 'blockquote':
      case 'table':
        return '\n';
      case 'orderedList':
        return Number(attribute(next, 'start') ?? 1) === 1 ? '\n' : '\n\n';
      default:
        return '\n\n';
    }
  }

  return '\n\n';
}

/** Indent every non-empty line (optionally skipping the first); empty lines stay empty. */
function indentLines(text: string, indent: string, includeFirst: boolean): string {
  return text
    .split('\n')
    .map((line, index) => ((index > 0 || includeFirst) && line !== '' ? `${indent}${line}` : line))
    .join('\n');
}

function taskItemFromListItem(item: MarkdownToken, helpers: MarkdownParseHelpers): JSONContent {
  const listItemToken: MarkdownToken = {
    ...item,
    type: 'list_item',
    task: false,
    tokens: (item.tokens ?? []).filter((child) => child.type !== 'checkbox'),
  };
  const [parsed] = helpers.parseChildren([listItemToken]);
  return {
    type: 'taskItem',
    attrs: { checked: item.checked === true },
    content: parsed?.content ?? [{ type: 'paragraph', content: [] }],
  };
}

function renderTable(node: JSONContent, helpers: MarkdownRendererHelpers): string {
  const rows = (node.content ?? []).map((row) => row.content ?? []);
  if (rows.length === 0) {
    return '';
  }

  const columnCount = Math.max(...rows.map((cells) => cells.length));
  if (columnCount === 0) {
    return '';
  }

  const alignments: (string | null)[] = Array.from({ length: columnCount }, (_, column) => {
    for (const cells of rows) {
      const align = stringAttribute(cells[column], 'align');
      if (align === 'left' || align === 'center' || align === 'right') {
        return align;
      }
    }
    return null;
  });

  const renderRow = (cells: JSONContent[]) => {
    const texts = Array.from({ length: columnCount }, (_, column) => renderCell(cells[column], helpers));
    return `|${texts.map((text) => (text === '' ? ' ' : ` ${text} `)).join('|')}|`;
  };

  const delimiter = `|${alignments
    .map((align) => {
      switch (align ?? '') {
        case 'left':
          return ' :--- ';
        case 'center':
          return ' :---: ';
        case 'right':
          return ' ---: ';
        default:
          return ' --- ';
      }
    })
    .join('|')}|`;

  const [header = [], ...body] = rows;
  return [renderRow(header), delimiter, ...body.map(renderRow)].join('\n');
}

function renderCell(cell: JSONContent | undefined, helpers: MarkdownRendererHelpers): string {
  if (!cell) {
    return '';
  }

  const paragraphs = (cell.content ?? []).map((paragraph) => {
    const inline = (paragraph.content ?? []).map((child) => protectCodePipes(child));
    return helpers.renderChildren(inline).trim();
  });

  return paragraphs
    .filter((text) => text !== '')
    .join(' ')
    .replace(/\\.|\|/g, (match) => (match === '|' ? '\\|' : match))
    .replaceAll(CODE_PIPE_PLACEHOLDER, '\\|');
}

function protectCodePipes(node: JSONContent): JSONContent {
  if (node.type === 'text' && node.marks?.some((mark) => mark.type === 'code') && node.text?.includes('|')) {
    return { ...node, text: node.text.replaceAll('|', CODE_PIPE_PLACEHOLDER) };
  }
  if (node.type === 'hardBreak') {
    return { type: 'text', text: ' ' };
  }
  return node;
}

/**
 * Trim whitespace the parsers strip anyway: Unicode whitespace at the start
 * and end of the block (JS `trim` semantics, as marked and markdown-it use),
 * spaces and tabs at the start of lines after a hard break.
 */
function trimParagraphLines(markdown: string): string {
  return markdown
    .split('\n')
    .map((line, index) => (index === 0 ? line.trimStart() : line.replace(/^[ \t]+/, '')))
    .join('\n')
    .trimEnd();
}

/** CommonMark's plain-text reading of inline tokens (used for image alt text). */
function plainText(tokens: MarkdownToken[]): string {
  return tokens
    .map((token) => {
      switch (token.type ?? '') {
        case 'text':
          return token.tokens && token.tokens.length > 0 ? plainText(token.tokens) : decodeHtmlEntities(token.text ?? '');
        case 'escape':
        case 'codespan':
          return token.text ?? '';
        case 'br':
          return '\n';
        case 'image':
          return plainText(token.tokens ?? []);
        case 'citation':
          return `[^${typeof token.label === 'string' ? token.label : ''}]`;
        default:
          return token.tokens ? plainText(token.tokens) : (token.text ?? '');
      }
    })
    .join('');
}
