/**
 * Callout containers: `:::note`, `:::tip`, `:::warning`.
 *
 * The tokenizer mirrors `mdit_py_plugins.container` (a port of
 * markdown-it-container) line for line, because the server renders the same
 * Markdown with it:
 *
 * - the opening line is up to 3 spaces, 3+ colons, then the callout type and
 *   nothing else (the server's `validate` is strict, so `:::note Title` is
 *   not a callout);
 * - the closing line is up to 3 spaces, at least as many colons as the
 *   opening, then only spaces;
 * - the closing line is searched for *before* the content is parsed, so a
 *   `:::` line inside a fenced code block closes the callout. The serializer
 *   therefore lengthens the fence (`::::note`) when the content has such a line;
 * - an unclosed callout runs to the end of its parent.
 */
import { type JSONContent, type MarkdownToken, mergeAttributes, Node } from '@tiptap/core';

import { CALLOUT_TYPES, type CalloutType, isCalloutType } from '../constants';
import { stringAttribute } from '../json';

const OPENING_LINE = /^( {0,3})(:{3,})([^\n]*)$/;
const CLOSING_LINE = /^ {0,3}(:+) *$/;
/**
 * An opening line anywhere after the first character. marked calls `start`
 * with `src.slice(1)` to find where a paragraph must stop, so only a match
 * right after a newline is a real line start.
 */
const OPENING_AFTER_NEWLINE = new RegExp(`\\n {0,3}:{3,}[ \\t]*(?:${CALLOUT_TYPES.join('|')})[ \\t]*(?=\\n|$)`);

/** Block content allowed inside a callout: no headings, no nested callouts, no footnotes. */
export const CALLOUT_CONTENT =
  '(paragraph | bulletList | orderedList | taskList | codeBlock | mermaid | blockquote | table | horizontalRule)+';

/**
 * Length of the colon fence for a callout whose rendered content is `inner`:
 * three, or one more than the longest colon run that starts a line of the
 * content (a shorter or equal run would close the callout early).
 */
export function calloutFenceLength(inner: string): number {
  let longest = 0;
  for (const match of inner.matchAll(/^ {0,3}(:{3,})/gm)) {
    longest = Math.max(longest, match[1]?.length ?? 0);
  }

  return Math.max(3, longest + 1);
}

function calloutType(value: unknown): CalloutType {
  return typeof value === 'string' && isCalloutType(value) ? value : 'note';
}

export const Callout = Node.create({
  name: 'callout',

  group: 'topBlock',

  content: CALLOUT_CONTENT,

  defining: true,

  addAttributes() {
    return {
      type: {
        default: 'note' satisfies CalloutType,
        parseHTML: (element) => calloutType(element.getAttribute('data-callout')),
        renderHTML: (attributes) => ({ 'data-callout': calloutType(attributes.type) }),
      },
    };
  },

  parseHTML() {
    return [{ tag: 'aside[data-callout]' }];
  },

  renderHTML({ node, HTMLAttributes }) {
    return ['aside', mergeAttributes({ class: `callout callout-${calloutType(node.attrs.type)}` }, HTMLAttributes), 0];
  },

  markdownTokenName: 'callout',

  parseMarkdown: (token, helpers) => {
    const parseBlocks = helpers.parseBlockChildren ?? helpers.parseChildren;
    return helpers.createNode('callout', { type: calloutType(token.calloutType) }, parseBlocks(token.tokens ?? []));
  },

  renderMarkdown: (node: JSONContent, helpers) => {
    const type = calloutType(stringAttribute(node, 'type'));
    const inner = helpers.renderChildren(node.content ?? [], '\n\n');
    const fence = ':'.repeat(calloutFenceLength(inner));
    return inner === '' ? `${fence}${type}\n${fence}` : `${fence}${type}\n${inner}\n${fence}`;
  },

  markdownTokenizer: {
    name: 'callout',
    level: 'block',
    start: (src: string) => {
      const match = OPENING_AFTER_NEWLINE.exec(src);
      return match ? match.index + 1 : -1;
    },
    tokenize: (src, _tokens, lexer): MarkdownToken | undefined => {
      const firstBreak = src.indexOf('\n');
      const firstLine = firstBreak === -1 ? src : src.slice(0, firstBreak);
      const opening = OPENING_LINE.exec(firstLine);
      const type = opening?.[3]?.trim() ?? '';
      if (!opening || !isCalloutType(type)) {
        return undefined;
      }

      const markerLength = opening[2]?.length ?? 3;
      const contentStart = firstBreak === -1 ? src.length : firstBreak + 1;
      let contentEnd = src.length;
      let rawEnd = src.length;
      let position = contentStart;

      while (position < src.length) {
        const lineBreak = src.indexOf('\n', position);
        const lineEnd = lineBreak === -1 ? src.length : lineBreak;
        const closing = CLOSING_LINE.exec(src.slice(position, lineEnd));
        if (closing && (closing[1]?.length ?? 0) >= markerLength) {
          contentEnd = position;
          rawEnd = lineBreak === -1 ? src.length : lineBreak + 1;
          break;
        }
        position = lineBreak === -1 ? src.length : lineBreak + 1;
      }

      const content = src.slice(contentStart, contentEnd);

      return {
        type: 'callout',
        raw: src.slice(0, rawEnd),
        calloutType: type,
        tokens: content.trim() === '' ? [] : lexer.blockTokens(content),
      };
    },
  },
});
