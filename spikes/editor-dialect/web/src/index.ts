export { type Article, ArticleCodec } from './article.js'
export { CALLOUT_TYPES, type CalloutType, CITATION_ID_PATTERN, MERMAID_LANGUAGE } from './dialect/constants.js'
export { escapeInlineText } from './dialect/escape.js'
export { DialectMarkdown } from './dialect/extension.js'
export {
  type ArticleFrontmatter,
  type ArticleSeries,
  FrontmatterError,
  parseFrontmatter,
  serializeFrontmatter,
  splitFrontmatter,
} from './dialect/frontmatter.js'
export { DialectMarkdownManager } from './dialect/manager.js'
export { Callout } from './extensions/callout.js'
export { Citation, FootnoteDefinition, Footnotes } from './extensions/citation.js'
export { Mermaid } from './extensions/mermaid.js'
export { articleExtensions } from './kit.js'
