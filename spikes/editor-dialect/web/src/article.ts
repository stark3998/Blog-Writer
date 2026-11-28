/**
 * Article-level codec: frontmatter + body, through a real (headless) TipTap
 * editor so that schema validation and ProseMirror normalization run exactly
 * as they do in the browser.
 */
import { Editor, type JSONContent } from '@tiptap/core'

import {
  type ArticleFrontmatter,
  parseFrontmatter,
  serializeFrontmatter,
  splitFrontmatter,
} from './dialect/frontmatter.js'
import { normalizeSource } from './dialect/manager.js'
import { prepareForSerialization } from './dialect/transforms.js'
import { articleExtensions } from './kit.js'

export interface Article {
  /** Null when the source has no frontmatter block (fragments, tests). */
  frontmatter: ArticleFrontmatter | null
  /** TipTap/ProseMirror JSON of the body. */
  doc: JSONContent
}

export class ArticleCodec {
  private readonly editor: Editor

  constructor() {
    this.editor = new Editor({
      element: null,
      extensions: articleExtensions(),
      // Throw instead of silently dropping content the schema cannot hold.
      enableContentCheck: true,
    })
  }

  /** Parse an article source. Throws FrontmatterError or a ProseMirror RangeError on invalid input. */
  parse(source: string): Article {
    const { frontmatter, body } = splitFrontmatter(normalizeSource(source))
    const parsedFrontmatter = frontmatter === null ? null : parseFrontmatter(frontmatter)
    this.editor.commands.setContent(body, { contentType: 'markdown', emitUpdate: false, errorOnInvalidContent: true })
    return { frontmatter: parsedFrontmatter, doc: this.editor.getJSON() }
  }

  /** Canonical Markdown for an article. */
  serialize(article: Article): string {
    this.editor.commands.setContent(article.doc, { emitUpdate: false, errorOnInvalidContent: true })
    const body = this.editor.getMarkdown()
    if (!article.frontmatter) {
      return body
    }

    const head = serializeFrontmatter(article.frontmatter)
    return body === '' ? head : `${head}\n${body}`
  }

  /**
   * Serialize and verify: re-parse the Markdown and compare documents. When
   * `lossless` is false, saving would change the document (for example two
   * adjacent lists of the same kind, which Markdown cannot keep apart); the
   * editor should warn before saving.
   */
  check(article: Article): { markdown: string; lossless: boolean; reparsed: Article } {
    const markdown = this.serialize(article)
    const reparsed = this.parse(markdown)
    const expected = this.editor.schema.nodeFromJSON(stripEmptyParagraphs(prepareForSerialization(article.doc)))
    const actual = this.editor.schema.nodeFromJSON(stripEmptyParagraphs(reparsed.doc))
    const sameFrontmatter = JSON.stringify(article.frontmatter) === JSON.stringify(reparsed.frontmatter)
    return { markdown, lossless: sameFrontmatter && expected.eq(actual), reparsed }
  }

  /** parse -> serialize: the dialect's canonical form of `source`. */
  normalize(source: string): string {
    return this.serialize(this.parse(source))
  }

  destroy(): void {
    this.editor.destroy()
  }
}

function stripEmptyParagraphs(doc: JSONContent): JSONContent {
  return {
    ...doc,
    content: (doc.content ?? []).filter(node => !(node.type === 'paragraph' && (node.content ?? []).length === 0)),
  }
}
