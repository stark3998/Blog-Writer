/**
 * Article-level codec: frontmatter plus body, through a real headless TipTap
 * editor, so schema validation and ProseMirror normalization run exactly as
 * they do in the visible editor.
 *
 * Every method throws only `DialectError` (FrontmatterError included), with a
 * stable `code` the editor can show.
 */
import { Editor, type JSONContent } from '@tiptap/core';
import type { Node as ProseMirrorNode } from '@tiptap/pm/model';

import { toDialectError } from './errors';
import {
  type ArticleFrontmatter,
  parseFrontmatter,
  sameFrontmatter,
  serializeFrontmatter,
  splitFrontmatter,
} from './frontmatter';
import { isEmptyParagraph } from './json';
import { createArticleExtensions } from './kit';
import { assertEditableSource } from './limits';
import { normalizeSource } from './manager';
import { prepareForSerialization } from './transforms';

export interface Article {
  /** Null when the source has no frontmatter block (fragments, tests). */
  frontmatter: ArticleFrontmatter | null;
  /** TipTap/ProseMirror JSON of the body. */
  doc: JSONContent;
}

export type ContentLossCode = 'frontmatter-changed' | 'content-changed';

/** Something saving would change, so the editor can warn before it saves. */
export interface ContentLossWarning {
  code: ContentLossCode;
  message: string;
  /**
   * For `content-changed`: the top-level blocks of the checked document that
   * would not survive saving, as indexes into its `doc.content` (`to` is
   * exclusive).
   */
  blocks?: { from: number; to: number };
}

export interface ArticleCheck {
  /** The canonical Markdown that saving writes. */
  markdown: string;
  /** True when reading `markdown` back gives exactly the checked article. */
  lossless: boolean;
  /** What would change; empty when lossless. */
  warnings: ContentLossWarning[];
  /** The article read back from `markdown`. */
  reparsed: Article;
}

export class ArticleCodec {
  private readonly editor: Editor;

  constructor() {
    this.editor = new Editor({
      element: null,
      extensions: createArticleExtensions(),
      // JSON, not the default '' (HTML): parsing HTML needs a DOM, and the normalize worker has none.
      content: { type: 'doc', content: [{ type: 'paragraph' }] },
      // Throw instead of silently dropping content the schema cannot hold.
      enableContentCheck: true,
    });
  }

  /**
   * Parse an article source (frontmatter optional). Throws `too-large` or
   * `too-deep` for sources the editor must not open, `invalid-frontmatter`
   * and `invalid-content` for sources it cannot read.
   */
  parse(source: string): Article {
    try {
      const normalized = normalizeSource(source);
      assertEditableSource(normalized);
      const { frontmatter, body } = splitFrontmatter(normalized);
      const parsedFrontmatter = frontmatter === null ? null : parseFrontmatter(frontmatter);
      this.editor.commands.setContent(body, { contentType: 'markdown', emitUpdate: false, errorOnInvalidContent: true });
      return { frontmatter: parsedFrontmatter, doc: this.editor.getJSON() };
    } catch (error) {
      throw toDialectError(error);
    }
  }

  /**
   * Canonical Markdown for an article. The frontmatter is validated on the
   * way out (`invalid-frontmatter`): the codec never writes a block it could
   * not read back.
   */
  serialize(article: Article): string {
    try {
      this.editor.commands.setContent(article.doc, { emitUpdate: false, errorOnInvalidContent: true });
      const body = this.editor.getMarkdown();
      if (!article.frontmatter) {
        return body;
      }

      const head = serializeFrontmatter(article.frontmatter);
      parseFrontmatter(splitFrontmatter(head).frontmatter ?? '');
      return body === '' ? head : `${head}\n${body}`;
    } catch (error) {
      throw toDialectError(error);
    }
  }

  /** parse -> serialize: the dialect's canonical form of `source`. */
  normalize(source: string): string {
    return this.serialize(this.parse(source));
  }

  /**
   * Serialize and verify: read the Markdown back and compare. Each warning
   * names what saving would change, for example two adjacent lists of the
   * same kind, a line break typed inside a paragraph, or bold over part of
   * inline code, none of which Markdown can hold. Call it before saving and
   * warn when `lossless` is false. A string is parsed first, so `check(md)`
   * tells whether normalizing `md` keeps the document as the editor reads it.
   */
  check(input: Article | string): ArticleCheck {
    const article = typeof input === 'string' ? this.parse(input) : input;
    const markdown = this.serialize(article);
    const reparsed = this.parse(markdown);
    const warnings: ContentLossWarning[] = [];

    if (!sameFrontmatter(article.frontmatter, reparsed.frontmatter)) {
      warnings.push({ code: 'frontmatter-changed', message: 'Saving would change the frontmatter.' });
    }

    let changed: { from: number; to: number } | null;
    try {
      changed = this.changedBlocks(article.doc, reparsed.doc);
    } catch (error) {
      throw toDialectError(error);
    }
    if (changed) {
      const count = changed.to - changed.from;
      warnings.push({
        code: 'content-changed',
        message:
          count === 1
            ? `Markdown cannot hold block ${changed.from + 1} exactly; saving would change it.`
            : `Markdown cannot hold blocks ${changed.from + 1}-${changed.to} exactly; saving would change them.`,
        blocks: changed,
      });
    }

    return { markdown, lossless: warnings.length === 0, warnings, reparsed };
  }

  destroy(): void {
    this.editor.destroy();
  }

  /**
   * The range of top-level blocks of `original` that differ after the round
   * trip, or null when the documents are equal. Blocks are compared as
   * ProseMirror nodes after the serializer's own preparation (empty
   * paragraphs dropped, footnotes ordered), so only real changes count.
   */
  private changedBlocks(original: JSONContent, reparsed: JSONContent): { from: number; to: number } | null {
    const prepared = prepareForSerialization(original);
    const expected = this.topLevel(prepared);
    const actual = this.topLevel({ ...reparsed, content: (reparsed.content ?? []).filter((node) => !isEmptyParagraph(node)) });

    let prefix = 0;
    while (prefix < expected.length && prefix < actual.length && sameNode(expected[prefix], actual[prefix])) {
      prefix += 1;
    }
    if (prefix === expected.length && prefix === actual.length) {
      return null;
    }

    let suffix = 0;
    while (
      suffix < expected.length - prefix &&
      suffix < actual.length - prefix &&
      sameNode(expected[expected.length - 1 - suffix], actual[actual.length - 1 - suffix])
    ) {
      suffix += 1;
    }

    // Map indexes in the prepared document back to the original's top level:
    // preparation only drops empty paragraphs and moves the footnotes last.
    const originalContent = original.content ?? [];
    const kept = originalContent.flatMap((node, index) =>
      isEmptyParagraph(node) || node.type === 'footnotes' ? [] : [index],
    );
    const footnotes = originalContent.findIndex((node) => node.type === 'footnotes');
    const toOriginal = (index: number) => kept[index] ?? (footnotes === -1 ? Math.max(0, originalContent.length - 1) : footnotes);

    // A pure insertion (the round trip split one block into two) changes the block before it.
    const first = Math.min(prefix, Math.max(0, expected.length - suffix - 1));
    const last = Math.max(first, expected.length - suffix - 1);
    const from = toOriginal(first);
    const to = Math.max(from, toOriginal(last)) + 1;
    return { from, to };
  }

  private topLevel(doc: JSONContent): ProseMirrorNode[] {
    const node = this.editor.schema.nodeFromJSON(doc);
    const children: ProseMirrorNode[] = [];
    node.forEach((child) => {
      children.push(child);
    });
    return children;
  }
}

function sameNode(a: ProseMirrorNode | undefined, b: ProseMirrorNode | undefined): boolean {
  return a !== undefined && b !== undefined && a.eq(b);
}
