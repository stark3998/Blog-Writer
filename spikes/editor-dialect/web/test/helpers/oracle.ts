/**
 * markdown-it oracle: an independent reading of the dialect, configured like
 * the server renderer (markdown-it-py is a port of markdown-it 14.1.0, pinned
 * here): `commonmark` preset, `html: false`, GFM tables, task lists,
 * footnotes without inline footnotes, strict `:::note|tip|warning` containers.
 *
 * `compareMarkdown(a, b)` decides whether two Markdown sources mean the same
 * thing by comparing markdown-it token streams field by field. Differences in
 * *syntax only* (delimiter characters, fence style, list looseness, ...) are
 * reported as normalization categories; anything else is a semantic change.
 */
import MarkdownIt, { type PluginSimple, type PluginWithParams, type Token } from 'markdown-it'
import container from 'markdown-it-container'
import footnote from 'markdown-it-footnote'

import { CALLOUT_TYPES } from '../../src/dialect/constants.js'
import { splitFrontmatter } from '../../src/dialect/frontmatter.js'

export function createOracle(options: { keepTextSpecial?: boolean } = {}): MarkdownIt {
  const md = new MarkdownIt('commonmark', { html: false, linkify: false, typographer: false })
  md.enable('table')
  // The plugin typings target the CommonJS build of @types/markdown-it; the runtime objects are the same.
  md.use(footnote as unknown as PluginSimple)
  md.inline.ruler.disable('footnote_inline')
  for (const type of CALLOUT_TYPES) {
    md.use(container as unknown as PluginWithParams, type, { validate: (params: string) => params.trim() === type })
  }
  md.core.ruler.after('inline', 'github-tasklists', taskListsRule)
  if (options.keepTextSpecial) {
    // Keep escapes and entities as separate text_special tokens so the
    // comparison can tell an escape change from a text change. (The renderer
    // has no rule for text_special, so this instance is for tokens only.)
    md.core.ruler.disable('text_join')
  }
  return md
}

/** Port of mdit_py_plugins.tasklists (disabled checkboxes, no labels). */
function taskListsRule(state: { tokens: Token[]; Token: new (type: string, tag: string, nesting: -1 | 0 | 1) => Token }): void {
  const tokens = state.tokens
  for (let i = 2; i < tokens.length - 1; i += 1) {
    const inline = tokens[i]
    if (
      inline.type === 'inline' &&
      tokens[i - 1].type === 'paragraph_open' &&
      tokens[i - 2].type === 'list_item_open' &&
      /^\[[ xX]][ \t\n\v\f\r]+/.test(inline.content)
    ) {
      const checked = !inline.content.startsWith('[ ] ')
      const checkbox = new state.Token('html_inline', '', 0)
      checkbox.content = `<input class="task-list-item-checkbox"${checked ? ' checked="checked"' : ''} disabled="disabled" type="checkbox">`
      const children = inline.children ?? []
      children.unshift(checkbox)
      children[1].content = children[1].content.slice(3)
      inline.content = inline.content.slice(3)
      tokens[i - 2].attrSet('class', 'task-list-item')
      const parent = findParent(tokens, i - 2)
      if (parent >= 0) {
        tokens[parent].attrSet('class', 'contains-task-list')
      }
    }
  }
}

function findParent(tokens: Token[], index: number): number {
  const level = tokens[index].level - 1
  for (let i = index - 1; i >= 0; i -= 1) {
    if (tokens[i].level === level) {
      return i
    }
  }
  return -1
}

export interface Comparison {
  equivalent: boolean
  /** Syntax-only differences, e.g. "emphasis-delimiter", "list-looseness". */
  categories: string[]
  /** Semantic differences (empty when equivalent). */
  differences: string[]
}

const tokenOracle = createOracle({ keepTextSpecial: true })
const renderOracle = createOracle()

/** Tokens with escapes/entities kept as separate `text_special` tokens. */
export function tokenize(markdown: string): Token[] {
  return tokenOracle.parse(markdown, {})
}

export function render(markdown: string): string {
  return renderOracle.render(markdown)
}

/** Compare two article sources (frontmatter compared as data, body via tokens). */
export function compareMarkdown(before: string, after: string): Comparison {
  const a = splitFrontmatter(before)
  const b = splitFrontmatter(after)
  const categories = new Set<string>()
  const differences: string[] = []

  if ((a.frontmatter === null) !== (b.frontmatter === null)) {
    differences.push('frontmatter presence differs')
  } else if (a.frontmatter !== null && b.frontmatter !== null && a.frontmatter !== b.frontmatter) {
    categories.add('frontmatter-style')
  }

  compareStreams(tokenize(a.body), tokenize(b.body), 'body', categories, differences)
  return { equivalent: differences.length === 0, categories: [...categories].sort(), differences }
}

interface FlatToken {
  type: string
  tag: string
  attrs: [string, string][]
  nesting: number
  hidden: boolean
  markup: string
  info: string
  content: string
  meta: unknown
  children: FlatToken[]
  /** Merged text only: markup of the escapes/entities it was built from. */
  specials: string[]
  /** Merged text only: a soft line break was folded into it. */
  softbreak: boolean
}

function flatten(token: Token): FlatToken {
  return {
    type: token.type,
    tag: token.tag,
    attrs: [...(token.attrs ?? [])].sort(([x], [y]) => x.localeCompare(y)),
    nesting: token.nesting,
    hidden: token.hidden,
    markup: token.markup,
    info: token.info,
    content: token.content,
    meta: token.meta,
    children: normalizeInline(token.children ?? []),
    specials: token.type === 'text_special' ? [token.markup] : [],
    softbreak: false,
  }
}

/**
 * Merge adjacent text (including escapes/entities), turn soft breaks into
 * spaces and collapse whitespace runs: all of these render the same.
 */
function normalizeInline(children: Token[]): FlatToken[] {
  const result: FlatToken[] = []
  for (const child of children) {
    const flat = flatten(child)
    if (flat.type === 'softbreak') {
      flat.content = ' '
      flat.softbreak = true
    }
    if (flat.type === 'softbreak' || flat.type === 'text_special') {
      flat.type = 'text'
      flat.tag = ''
      flat.markup = ''
      flat.info = ''
    }
    const previous = result[result.length - 1]
    if (flat.type === 'text' && previous?.type === 'text') {
      previous.content += flat.content
      previous.specials.push(...flat.specials)
      previous.softbreak ||= flat.softbreak
    } else {
      result.push(flat)
    }
  }
  for (const token of result) {
    if (token.type === 'text') {
      // Only the whitespace HTML collapses. JS `\s` would also fold NBSP, U+3000 and other
      // Unicode spaces into ' ', hiding a normalization that changes them (reviewer fix).
      token.content = token.content.replace(/[ \t\n]+/g, ' ')
    }
  }
  return result
}

function compareStreams(
  a: Token[] | FlatToken[],
  b: Token[] | FlatToken[],
  path: string,
  categories: Set<string>,
  differences: string[],
): void {
  const left = (a as (Token | FlatToken)[]).map(token => ('attrGet' in token ? flatten(token) : token))
  const right = (b as (Token | FlatToken)[]).map(token => ('attrGet' in token ? flatten(token) : token))

  if (left.length !== right.length) {
    differences.push(`${path}: ${left.length} tokens vs ${right.length} (${summarize(left)} | ${summarize(right)})`)
    return
  }

  left.forEach((x, index) => {
    const y = right[index]
    const where = `${path}[${index}]<${x.type}>`
    compareToken(x, y, where, categories, differences)
  })
}

function compareToken(x: FlatToken, y: FlatToken, where: string, categories: Set<string>, differences: string[]): void {
  if (x.type !== y.type) {
    const pair = [x.type, y.type].sort().join('/')
    if (pair === 'code_block/fence' && x.content === y.content) {
      categories.add('code-fence-style')
      return
    }
    differences.push(`${where}: type ${x.type} -> ${y.type}`)
    return
  }

  if (x.tag !== y.tag) {
    differences.push(`${where}: tag ${x.tag} -> ${y.tag}`)
  }
  if (JSON.stringify(x.attrs) !== JSON.stringify(y.attrs)) {
    differences.push(`${where}: attrs ${JSON.stringify(x.attrs)} -> ${JSON.stringify(y.attrs)}`)
  }
  if (x.nesting !== y.nesting) {
    differences.push(`${where}: nesting`)
  }
  if (x.hidden !== y.hidden) {
    categories.add('list-looseness')
  }

  if (x.type === 'text') {
    // Escapes and entities render as plain text; only the resulting text matters.
    if (JSON.stringify(x.specials) !== JSON.stringify(y.specials)) {
      categories.add('escape-or-entity')
    }
    if (x.softbreak !== y.softbreak) {
      categories.add('soft-break')
    }
  } else {
    if (x.markup !== y.markup) {
      categories.add(markupCategory(x))
    }
    compareInfo(x, y, where, categories, differences)
  }

  if (x.type === 'image') {
    // The alt text is the plain text of the children; `content` is its raw source.
    if (plain(x.children) !== plain(y.children)) {
      differences.push(`${where}: alt ${JSON.stringify(plain(x.children))} -> ${JSON.stringify(plain(y.children))}`)
    } else if (x.content !== y.content) {
      categories.add('image-alt-markup')
    }
    return
  }

  if (x.type !== 'inline' && x.content !== y.content) {
    differences.push(`${where}: content ${JSON.stringify(x.content)} -> ${JSON.stringify(y.content)}`)
  }

  if (x.type.startsWith('footnote') && JSON.stringify(x.meta) !== JSON.stringify(y.meta)) {
    differences.push(`${where}: meta ${JSON.stringify(x.meta)} -> ${JSON.stringify(y.meta)}`)
  }

  if (x.children.length > 0 || y.children.length > 0) {
    compareStreams(x.children, y.children, where, categories, differences)
  }
}

function compareInfo(x: FlatToken, y: FlatToken, where: string, categories: Set<string>, differences: string[]): void {
  if (x.info === y.info) {
    return
  }

  if (x.type === 'list_item_open') {
    categories.add('ordered-numbering')
    return
  }

  if (x.type === 'fence') {
    const language = (info: string) => info.trim().split(/\s+/)[0]?.toLowerCase() ?? ''
    if (language(x.info) === 'mermaid' && language(y.info) === 'mermaid') {
      categories.add('mermaid-info')
      return
    }
  }

  if (x.type.startsWith('container_') && x.info.trim() === y.info.trim()) {
    categories.add('callout-marker')
    return
  }

  if ((x.type === 'link_open' || x.type === 'link_close') && [x.info, y.info].sort().join('/') === '/auto') {
    categories.add('autolink')
    return
  }

  differences.push(`${where}: info ${JSON.stringify(x.info)} -> ${JSON.stringify(y.info)}`)
}

function markupCategory(token: FlatToken): string {
  switch (token.type) {
    case 'em_open':
    case 'em_close':
    case 'strong_open':
    case 'strong_close':
      return 'emphasis-delimiter'
    case 'bullet_list_open':
    case 'bullet_list_close':
      return 'bullet-marker'
    case 'ordered_list_open':
    case 'ordered_list_close':
      return 'ordered-delimiter'
    case 'list_item_open':
    case 'list_item_close':
      return token.markup === '.' || token.markup === ')' ? 'ordered-delimiter' : 'bullet-marker'
    case 'heading_open':
    case 'heading_close':
      return 'heading-style'
    case 'fence':
      return 'code-fence-style'
    case 'hr':
      return 'thematic-break'
    case 'link_open':
    case 'link_close':
      return 'autolink'
    case 'code_inline':
      return 'code-span-delimiter'
    default:
      if (token.type.startsWith('container_')) {
        return 'callout-marker'
      }
      return `markup:${token.type}`
  }
}

function plain(tokens: FlatToken[]): string {
  return tokens.map(token => (token.children.length > 0 ? plain(token.children) : token.content)).join('')
}

function summarize(tokens: FlatToken[]): string {
  return tokens
    .slice(0, 12)
    .map(token => token.type)
    .join(',')
}
