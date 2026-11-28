/**
 * Mermaid diagrams: a fenced code block whose info string starts with
 * `mermaid`. A separate node type (not a code block language) so the editor
 * can show a rendered diagram while the source stays plain text.
 *
 * The node only stores the diagram source; nothing here renders it. The
 * editor UI asks the server to render it (strict mode), as publishing does.
 *
 * It claims the same marked `code` token as the code block, with a higher
 * priority, and declines anything that is not a mermaid fence.
 */
import { type JSONContent, Node } from '@tiptap/core';

import { MERMAID_LANGUAGE } from '../constants';
import { codeFenceFor } from '../escape';
import { textContent } from '../json';

/** First word of a fenced code block info string, lowercased (the CommonMark "language"). */
export function infoLanguage(info: unknown): string {
  if (typeof info !== 'string') {
    return '';
  }
  return info.trim().split(/\s+/)[0]?.toLowerCase() ?? '';
}

export const Mermaid = Node.create({
  name: 'mermaid',

  group: 'block',

  content: 'text*',

  marks: '',

  code: true,

  defining: true,

  // Higher than CodeBlock (100) so this parser sees `code` tokens first.
  priority: 200,

  parseHTML() {
    return [{ tag: 'pre[data-mermaid]', preserveWhitespace: 'full' }];
  },

  renderHTML() {
    return ['pre', { 'data-mermaid': '', class: 'mermaid-source' }, ['code', 0]];
  },

  markdownTokenName: 'code',

  parseMarkdown: (token, helpers) => {
    const fenced = token.codeBlockStyle !== 'indented';
    if (!fenced || infoLanguage(token.lang) !== MERMAID_LANGUAGE) {
      return [];
    }

    const text = token.text ?? '';
    return helpers.createNode('mermaid', undefined, text ? [helpers.createTextNode(text)] : []);
  },

  renderMarkdown: (node: JSONContent) => {
    const text = textContent(node);
    const fence = codeFenceFor(text);
    return text === '' ? `${fence}${MERMAID_LANGUAGE}\n${fence}` : `${fence}${MERMAID_LANGUAGE}\n${text}\n${fence}`;
  },
});
