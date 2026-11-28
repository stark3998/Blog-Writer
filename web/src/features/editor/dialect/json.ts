/** Small helpers for TipTap JSON documents. */
import type { JSONContent } from '@tiptap/core';

/** Concatenated text of a node's direct text children (code blocks, mermaid). */
export function textContent(node: JSONContent): string {
  return (node.content ?? []).map((child) => child.text ?? '').join('');
}

/** Deep copy without prototypes or undefined values (JSON documents only). */
export function cloneJSON<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

/** An attribute of a node, typed as unknown (TipTap types attributes as `any`). */
export function attribute(node: JSONContent | undefined, name: string): unknown {
  return node?.attrs?.[name];
}

/** A string attribute, or undefined when it is missing or not a string. */
export function stringAttribute(node: JSONContent | undefined, name: string): string | undefined {
  const value = attribute(node, name);
  return typeof value === 'string' ? value : undefined;
}

/**
 * An id attribute value as text. Parsed ids are strings; ids from JSON may be
 * numbers. Anything else (null, objects) has no usable id.
 */
export function idText(value: unknown): string {
  if (typeof value === 'string') {
    return value;
  }
  return typeof value === 'number' ? String(value) : '';
}

/** The `id` of a citation or footnote definition as text (see idText). */
export function nodeId(node: JSONContent | undefined): string {
  return idText(attribute(node, 'id'));
}

/** Depth-first visit of every node; `parent` is undefined for the root. */
export function walk(
  node: JSONContent,
  visit: (node: JSONContent, parent: JSONContent | undefined) => void,
  parent?: JSONContent,
): void {
  visit(node, parent);
  for (const child of node.content ?? []) {
    walk(child, visit, node);
  }
}

export function marksEqual(a: JSONContent['marks'], b: JSONContent['marks']): boolean {
  return JSON.stringify(a ?? []) === JSON.stringify(b ?? []);
}

/** Merge adjacent text nodes that carry the same marks; drop empty text nodes. */
export function mergeAdjacentText(content: JSONContent[]): JSONContent[] {
  const merged: JSONContent[] = [];
  for (const node of content) {
    if (node.type === 'text' && (node.text ?? '') === '') {
      continue;
    }
    const previous = merged.at(-1);
    if (previous?.type === 'text' && node.type === 'text' && marksEqual(previous.marks, node.marks)) {
      previous.text = (previous.text ?? '') + (node.text ?? '');
    } else {
      merged.push(node);
    }
  }
  return merged;
}

export function isEmptyParagraph(node: JSONContent): boolean {
  return node.type === 'paragraph' && (node.content ?? []).length === 0;
}
