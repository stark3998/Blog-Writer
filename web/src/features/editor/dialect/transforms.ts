/**
 * JSON-level normalization around TipTap's Markdown parser and serializer.
 *
 * `normalizeParsedDocument` runs on every parse result; `prepareForSerialization`
 * runs on every document (parsed or edited) before it is turned into Markdown.
 * Both are pure functions over TipTap JSON: they never modify their input.
 */
import type { JSONContent } from '@tiptap/core';

import { cloneJSON, isEmptyParagraph, mergeAdjacentText, nodeId, stringAttribute } from './json';

const CODE_NODE_TYPES = new Set(['codeBlock', 'mermaid']);
/** Containers whose empty paragraphs can be dropped as long as one child remains. */
const FLEXIBLE_CONTAINERS = new Set(['blockquote', 'callout']);
const BULLET_LIKE = new Set(['bulletList', 'taskList']);

export function normalizeParsedDocument(doc: JSONContent): JSONContent {
  const result = cloneJSON(doc);
  liftNestedCallouts(result);
  normalizeSoftBreaks(result);
  const definitions = extractFootnoteDefinitions(result);
  const defined = new Set(definitions.map((definition) => nodeId(definition)));
  unresolveUndefinedCitations(result, defined);
  removeEmptyParagraphs(result);
  attachFootnotes(result, definitions);
  return result;
}

export function prepareForSerialization(doc: JSONContent): JSONContent {
  const result = cloneJSON(doc);
  removeEmptyParagraphs(result);
  dropTrailingHardBreaks(result);
  flattenHeadingBreaks(result);
  attachFootnotes(result, extractFootnoteDefinitions(result));
  alternateAdjacentListMarkers(result);
  return result;
}

/**
 * CommonMark merges two adjacent lists of the same kind into one. To keep
 * them apart, a list that directly follows another uses the other marker:
 * bullets alternate `-` / `*`, ordered delimiters alternate `.` / `)`.
 * The choice is written to serialization-only attributes read by the
 * list-item renderers (`markdownBullet`, `markdownDelimiter`).
 */
function alternateAdjacentListMarkers(node: JSONContent): void {
  const content = node.content ?? [];
  content.forEach((child, index) => {
    const previous = index > 0 ? content[index - 1] : undefined;
    if (child.type && BULLET_LIKE.has(child.type)) {
      const previousBullet =
        previous?.type && BULLET_LIKE.has(previous.type) ? (stringAttribute(previous, 'markdownBullet') ?? '-') : null;
      child.attrs = { ...child.attrs, markdownBullet: previousBullet === '-' ? '*' : '-' };
    } else if (child.type === 'orderedList') {
      const previousDelimiter =
        previous?.type === 'orderedList' ? (stringAttribute(previous, 'markdownDelimiter') ?? '.') : null;
      child.attrs = { ...child.attrs, markdownDelimiter: previousDelimiter === '.' ? ')' : '.' };
    }
    alternateAdjacentListMarkers(child);
  });
}

/**
 * A soft line break inside a paragraph renders as whitespace, so the dialect
 * keeps one paragraph per line: `\n` (plus surrounding spaces) becomes one
 * space. Tabs in prose render as spaces too and become spaces.
 */
function normalizeSoftBreaks(node: JSONContent): void {
  if (node.type && CODE_NODE_TYPES.has(node.type)) {
    return;
  }

  for (const child of node.content ?? []) {
    if (child.type === 'text') {
      const text = child.text ?? '';
      if (/[\n\t]/.test(text) && !child.marks?.some((mark) => mark.type === 'code')) {
        child.text = text.replace(/[ \t]*\n[ \t]*/g, ' ').replace(/\t/g, ' ');
      }
    } else {
      normalizeSoftBreaks(child);
    }
  }
}

function removeEmptyParagraphs(node: JSONContent): void {
  const content = node.content;
  if (!content) {
    return;
  }

  for (const child of content) {
    removeEmptyParagraphs(child);
  }

  if (node.type === 'doc') {
    node.content = content.filter((child) => !isEmptyParagraph(child));
    return;
  }

  if (node.type && FLEXIBLE_CONTAINERS.has(node.type)) {
    const kept = content.filter((child) => !isEmptyParagraph(child));
    node.content = kept.length > 0 ? kept : content.slice(0, 1);
    return;
  }

  if (node.type === 'listItem' || node.type === 'taskItem') {
    // The first paragraph is required by the schema; later empty ones are noise.
    const kept = content.filter((child, index) => index === 0 || !isEmptyParagraph(child));
    node.content = kept;
    // `- &nbsp;` is how an empty item is written (see EMPTY_ITEM_TEXT in overrides.ts).
    const [first] = kept;
    if (first?.type === 'paragraph' && first.content?.length === 1 && first.content[0]?.text === '\u00A0') {
      first.content = [];
    }
  }
}

/**
 * Callouts are top-level blocks only. markdown-it also parses a container
 * inside a list item, quote or another callout; the schema cannot hold that,
 * so such a callout is replaced by its content (the linter reports it).
 */
function liftNestedCallouts(node: JSONContent, isRoot = true): void {
  if (!node.content) {
    return;
  }

  node.content = node.content.flatMap((child) => {
    liftNestedCallouts(child, false);
    return child.type === 'callout' && !isRoot ? (child.content ?? []) : [child];
  });
}

/** Remove every footnote definition (wherever the parser put it) and return them in source order. */
function extractFootnoteDefinitions(node: JSONContent): JSONContent[] {
  const found: JSONContent[] = [];

  const visit = (parent: JSONContent) => {
    if (!parent.content) {
      return;
    }

    const kept: JSONContent[] = [];
    for (const child of parent.content) {
      if (child.type === 'footnoteDefinition') {
        found.push(child);
      } else if (child.type === 'footnotes') {
        found.push(...(child.content ?? []).filter((definition) => definition.type === 'footnoteDefinition'));
      } else {
        visit(child);
        kept.push(child);
      }
    }
    parent.content = kept;
  };

  visit(node);
  return found;
}

function citationAsText(citation: JSONContent): JSONContent {
  return {
    type: 'text',
    text: `[^${nodeId(citation)}]`,
    ...(citation.marks ? { marks: citation.marks } : {}),
  };
}

/** markdown-it-footnote only treats `[^x]` as a reference when `x` is defined. */
function unresolveUndefinedCitations(node: JSONContent, defined: Set<string>): void {
  if (!node.content) {
    return;
  }

  const content: JSONContent[] = [];
  let changed = false;
  for (const child of node.content) {
    if (child.type === 'citation' && !defined.has(nodeId(child))) {
      changed = true;
      content.push(citationAsText(child));
    } else {
      unresolveUndefinedCitations(child, defined);
      content.push(child);
    }
  }

  node.content = changed ? mergeAdjacentText(content) : content;
}

/**
 * Put the definitions into a single `footnotes` node at the end of the
 * document, ordered by first reference (the numbering readers see), then any
 * unreferenced definitions in source order. When a label is defined twice the
 * last definition wins, as in markdown-it-footnote.
 */
function attachFootnotes(doc: JSONContent, definitions: JSONContent[]): void {
  if (definitions.length === 0) {
    return;
  }

  const byId = new Map<string, JSONContent>();
  const sourceOrder: string[] = [];
  for (const definition of definitions) {
    const id = nodeId(definition);
    if (!byId.has(id)) {
      sourceOrder.push(id);
    }
    byId.set(id, sanitizeDefinition(definition));
  }

  const referenceOrder: string[] = [];
  const collect = (node: JSONContent) => {
    if (node.type === 'citation') {
      const id = nodeId(node);
      if (byId.has(id) && !referenceOrder.includes(id)) {
        referenceOrder.push(id);
      }
    }
    for (const child of node.content ?? []) {
      collect(child);
    }
  };
  collect(doc);

  const ordered = [...referenceOrder, ...sourceOrder.filter((id) => !referenceOrder.includes(id))];
  const content = ordered.flatMap((id) => {
    const definition = byId.get(id);
    return definition ? [definition] : [];
  });
  doc.content = [...(doc.content ?? []), { type: 'footnotes', content }];
}

/** A definition holds plain marked-up text only: no nested citations, breaks or images. */
function sanitizeDefinition(definition: JSONContent): JSONContent {
  const content = (definition.content ?? []).map((child): JSONContent => {
    if (child.type === 'citation') {
      return citationAsText(child);
    }
    if (child.type === 'hardBreak') {
      return { type: 'text', text: ' ' };
    }
    if (child.type === 'image') {
      return { type: 'text', text: stringAttribute(child, 'alt') ?? '' };
    }
    return child;
  });

  const merged = mergeAdjacentText(content);
  return merged.length > 0
    ? { ...definition, content: merged }
    : { ...(definition.type ? { type: definition.type } : {}), ...(definition.attrs ? { attrs: definition.attrs } : {}) };
}

/**
 * A hard break at the end of a paragraph or heading is not a break in
 * CommonMark (`text\` at the end of a block is a literal backslash); drop it.
 */
function dropTrailingHardBreaks(node: JSONContent): void {
  for (const child of node.content ?? []) {
    if ((child.type === 'paragraph' || child.type === 'heading') && child.content) {
      while (child.content.at(-1)?.type === 'hardBreak') {
        child.content.pop();
      }
    } else {
      dropTrailingHardBreaks(child);
    }
  }
}

/** ATX headings are one line: a hard break inside a heading becomes a space. */
function flattenHeadingBreaks(node: JSONContent): void {
  for (const child of node.content ?? []) {
    if (child.type === 'heading' && child.content?.some((inline) => inline.type === 'hardBreak')) {
      child.content = mergeAdjacentText(
        child.content.map((inline) => (inline.type === 'hardBreak' ? { type: 'text', text: ' ' } : inline)),
      );
    } else {
      flattenHeadingBreaks(child);
    }
  }
}
