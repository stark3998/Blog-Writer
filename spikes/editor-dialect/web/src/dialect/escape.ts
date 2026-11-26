/**
 * Context-aware backslash escaping for plain text in the article dialect.
 *
 * Goal: a text node serialized with `escapeInlineText` must parse back to the
 * exact same characters in both TipTap (marked) and markdown-it, while staying
 * readable (no blanket escaping of every punctuation character).
 *
 * The rules are documented in docs/v2/article-dialect.md ("Escaping") and are
 * exercised by property-based tests in test/escape.test.ts.
 */

export interface TextEscapeContext {
  /**
   * The text begins a Markdown line: it is the first inline of a block
   * (paragraph in the body, a list item, a blockquote, a callout) or it follows
   * a hard break. Block-level syntax (`#`, `>`, `-`, `1.`, `:::`, ...) must be
   * neutralised there, and leading spaces/tabs are dropped (parsers strip them).
   */
  lineStart: boolean
  /** The text is the last inline of an ATX heading (a trailing `#` run would be eaten). */
  headingEnd: boolean
  /**
   * The text starts / ends a paragraph or heading. Parsers trim *Unicode*
   * whitespace there (markdown-it and marked use JS `trim`, markdown-it-py
   * `str.strip`), so it is dropped rather than written and lost on parse.
   */
  blockStart?: boolean
  blockEnd?: boolean
}

/** Something that CommonMark would read as an entity or numeric character reference. */
const ENTITY_LIKE = /^&(?:#[0-9]{1,7}|#[xX][0-9a-fA-F]{1,6}|[A-Za-z][A-Za-z0-9]{0,31});/
/** A character that would turn `<` into an autolink or (in other renderers) an HTML tag. */
const TAG_START = /^[A-Za-z/!?]/
/** Letters and digits in any script; `_` between two of them cannot delimit emphasis. */
const WORD_CHAR = /^[\p{L}\p{N}]$/u

/** Characters that are always backslash-escaped in text. */
const ALWAYS_ESCAPED = new Set(['\\', '`', '*', '[', ']'])
/** A run of CommonMark line endings (LF, CR or CRLF) with the spaces and tabs around them. */
export const LINE_ENDING_RUN = /[ \t]*(?:(?:\r\n?|\n)[ \t]*)+/g

/**
 * Escape one text run. `ctx` describes where the run sits; see TextEscapeContext.
 */
export function escapeInlineText(text: string, ctx: TextEscapeContext): string {
  // A line ending inside a text node (insertText, pasted or generated text)
  // would start a new Markdown line, where `# `, `- `, a fence or `[^x]:`
  // become blocks. It renders as a space, like the soft break a parser folds
  // into one, so it is written as one. A tab in prose renders as a space too;
  // marked also expands tabs inside list items.
  let source = text.replace(LINE_ENDING_RUN, ' ').replace(/\t/g, ' ')
  if (ctx.blockStart) {
    source = source.trimStart()
  } else if (ctx.lineStart) {
    source = source.replace(/^[ \t]+/, '')
  }
  if (ctx.blockEnd) {
    source = source.trimEnd()
  }
  const chars = Array.from(source)
  let out = ''

  for (let i = 0; i < chars.length; i += 1) {
    const ch = chars[i]

    if (ALWAYS_ESCAPED.has(ch)) {
      out += `\\${ch}`
      continue
    }

    if (ch === '_') {
      const intraword = WORD_CHAR.test(chars[i - 1] ?? '') && WORD_CHAR.test(chars[i + 1] ?? '')
      out += intraword ? '_' : '\\_'
      continue
    }

    if (ch === '<') {
      out += TAG_START.test(chars[i + 1] ?? '') ? '\\<' : '<'
      continue
    }

    if (ch === '&') {
      out += ENTITY_LIKE.test(chars.slice(i, i + 40).join('')) ? '\\&' : '&'
      continue
    }

    out += ch
  }

  if (ctx.lineStart) {
    out = escapeLineStart(out)
  }

  if (ctx.headingEnd) {
    out = escapeHeadingEnd(out)
  }

  return out
}

/**
 * Neutralise block syntax at the start of a line. `line` is already
 * inline-escaped, so `*`, `` ` ``, `[` and `\` never appear unescaped here.
 */
export function escapeLineStart(line: string): string {
  // ATX heading: 1-6 `#` followed by whitespace or end of line. CommonMark
  // only counts space/tab, but marked also accepts Unicode whitespace (`\s`).
  if (/^#{1,6}(?=\s|$)/.test(line)) {
    return `\\${line}`
  }

  // Blockquote marker.
  if (line.startsWith('>')) {
    return `\\${line}`
  }

  // Bullet list marker, thematic break / setext underline (`---`), or a GFM
  // table delimiter row (`-|`, `-:`) after a hard break.
  if (/^-(?=[ \t|:-]|$)/.test(line) || /^\+(?=[ \t]|$)/.test(line)) {
    return `\\${line}`
  }

  // Setext heading underline after a hard break (`===`).
  if (line.startsWith('=')) {
    return `\\${line}`
  }

  // Ordered list marker: up to 9 digits then `.` or `)` and whitespace/end.
  const ordered = /^(\d{1,9})([.)])(?=[ \t]|$)/.exec(line)
  if (ordered) {
    return `${ordered[1]}\\${line.slice(ordered[1].length)}`
  }

  // Callout fence (`:::note`) or a table delimiter cell (`:--`).
  if (/^:(?=[:-])/.test(line)) {
    return `\\${line}`
  }

  // Table row after a hard break.
  if (line.startsWith('|')) {
    return `\\${line}`
  }

  // Tilde code fence.
  if (/^~(?=~)/.test(line)) {
    return `\\${line}`
  }

  return line
}

/**
 * A trailing run of `#` preceded by whitespace (or making up the whole
 * heading) is an optional ATX closing sequence and would be dropped by the
 * parser. Escaping its first `#` keeps it as text.
 */
export function escapeHeadingEnd(text: string): string {
  const match = /(^|[ \t])(#+)[ \t]*$/.exec(text)
  if (!match) {
    return text
  }

  const runStart = match.index + match[1].length
  return `${text.slice(0, runStart)}\\${text.slice(runStart)}`
}

/**
 * Inside link text and image descriptions, write brackets as numeric
 * character references instead of backslash escapes. marked unescapes `\[`
 * and `\]` in link text *before* tokenizing it, so an escaped `[^x]` or
 * `[a](b)` there would turn back into a citation or a nested link; CommonMark
 * parsers (markdown-it) keep them as text either way.
 */
export function bracketsAsReferences(escaped: string): string {
  return escaped.replace(/\\(.)/g, (whole, ch: string) => {
    if (ch === '[') {
      return '&#91;'
    }
    if (ch === ']') {
      return '&#93;'
    }
    return whole
  })
}

/** Length of the shortest backtick run that does not occur in `text` (at least 1). */
export function shortestUnusedBacktickRun(text: string): number {
  const runs = new Set<number>()
  for (const match of text.matchAll(/`+/g)) {
    runs.add(match[0].length)
  }

  let length = 1
  while (runs.has(length)) {
    length += 1
  }

  return length
}

/** Fence for a fenced code block: backticks, longer than any backtick run that could close it. */
export function codeFenceFor(text: string): string {
  let longest = 0
  for (const match of text.matchAll(/^ {0,3}(`+)/gm)) {
    longest = Math.max(longest, match[1].length)
  }

  return '`'.repeat(Math.max(3, longest + 1))
}

/**
 * Info string for a backtick fence. It cannot hold a backtick (the line would
 * not be a fence, and the code would be read as Markdown) or a line break, but
 * a tilde fence (`~~~a`b`) or a pasted `language-a`b` class can carry one.
 * Keep the words before the first word with a backtick: the server only reads
 * the first word, and a first word with a backtick names no language, so the
 * rendered code block is unchanged.
 */
export function fenceInfo(info: string): string {
  if (!/[`\r\n]/.test(info)) {
    return info
  }

  const words = info.trim().split(/\s+/)
  const cut = words.findIndex(word => word.includes('`'))
  return (cut === -1 ? words : words.slice(0, cut)).join(' ')
}

/**
 * Format a link or image destination. Bare when that parses back unchanged,
 * otherwise in pointy brackets with `<`, `>` and `\` escaped.
 */
export function formatLinkDestination(href: string): string {
  if (href === '') {
    return '<>'
  }

  const needsBrackets = /[\s<>\\\u0000-\u001f\u007f]/.test(href) || !parenthesesBalanced(href)
  if (!needsBrackets) {
    return href
  }

  // No line endings inside `<...>`: a raw CR or LF would end the link (and the line).
  return `<${href.replace(/[<>\\]/g, '\\$&').replace(/\n/g, '%0A').replace(/\r/g, '%0D')}>`
}

/** Format a link title, always double-quoted and on one line (a blank line would end the paragraph). */
export function formatLinkTitle(title: string): string {
  return `"${title.replace(LINE_ENDING_RUN, ' ').replace(/["\\]/g, '\\$&')}"`
}

function parenthesesBalanced(value: string): boolean {
  let depth = 0
  for (const ch of value) {
    if (ch === '(') {
      depth += 1
    } else if (ch === ')') {
      depth -= 1
      if (depth < 0) {
        return false
      }
    }
  }

  return depth === 0
}
