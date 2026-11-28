/**
 * The article editor's Markdown layer (`README.md` in this folder): the
 * dialect codec, the TipTap extension set and the normalize worker client.
 * No UI lives here yet.
 */
export {
  type Article,
  type ArticleCheck,
  ArticleCodec,
  type ContentLossCode,
  type ContentLossWarning,
} from './dialect/codec';
export {
  BODY_HEADING_LEVELS,
  CALLOUT_TYPES,
  type CalloutType,
  CITATION_ID_MAX_LENGTH,
  CITATION_ID_PATTERN,
  isCalloutType,
  MERMAID_LANGUAGE,
  SLUG_PATTERN,
} from './dialect/constants';
export { DialectError, type DialectErrorCode, FrontmatterError, type FrontmatterIssue } from './dialect/errors';
export { escapeInlineText } from './dialect/escape';
export { Callout } from './dialect/extensions/callout';
export { citationLabel, Citation, FootnoteDefinition, Footnotes } from './dialect/extensions/citation';
export { Mermaid } from './dialect/extensions/mermaid';
export {
  type ArticleFrontmatter,
  type ArticleSeries,
  FRONTMATTER_KEYS,
  FRONTMATTER_LIMITS,
  parseFrontmatter,
  sameFrontmatter,
  serializeFrontmatter,
  splitFrontmatter,
  validateFrontmatter,
} from './dialect/frontmatter';
export { type ArticleExtensionsOptions, createArticleExtensions, type Lowlight } from './dialect/kit';
export {
  assertEditableSize,
  assertEditableSource,
  EDITOR_MAX_BYTES,
  estimateNestingDepth,
  MAX_NESTING_DEPTH,
  utf8ByteLength,
} from './dialect/limits';
export { DialectMarkdownManager } from './dialect/manager';
export { DialectMarkdown } from './dialect/markdown-extension';
export {
  DEFAULT_WORKER_TIMEOUT_MS,
  DialectWorkerClient,
  type DialectWorkerClientOptions,
  type DialectWorkerLike,
  disposeDialectWorker,
  normalizeInWorker,
  parseInWorker,
  type WorkerCallOptions,
} from './normalize-client';
