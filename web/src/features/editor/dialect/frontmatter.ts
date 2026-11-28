/**
 * Article frontmatter: split, parse (strict schema) and canonical serialization.
 *
 * Parsing uses the YAML *failsafe* schema (every scalar is a string), then
 * converts and validates each key explicitly. That keeps behaviour identical
 * to the server's twin (`yaml.BaseLoader`) and avoids YAML 1.1/1.2 typing
 * drift (`no` -> false, `2024-01-01` -> date, ...).
 */
import { parse as parseYaml, YAMLParseError } from 'yaml';

import { SLUG_PATTERN } from './constants';
import { FrontmatterError, type FrontmatterIssue } from './errors';

export interface ArticleSeries {
  id: string;
  title: string;
  part: number;
  /** Omitted while the series is open-ended. */
  total?: number;
}

export interface ArticleFrontmatter {
  title: string;
  slug: string;
  excerpt: string;
  pillar: string;
  format: string;
  tags: string[];
  series?: ArticleSeries;
}

/** Canonical key order. Also the complete set of allowed keys. */
export const FRONTMATTER_KEYS = ['title', 'slug', 'excerpt', 'pillar', 'format', 'tags', 'series'] as const;
const REQUIRED_KEYS = ['title', 'slug', 'excerpt', 'pillar', 'format', 'tags'] as const;
const SERIES_KEYS = ['id', 'title', 'part', 'total'] as const;

export const FRONTMATTER_LIMITS = {
  frontmatterBytes: 8 * 1024,
  title: 150,
  excerpt: 400,
  slug: 80,
  tag: 40,
  tags: 10,
  seriesTitle: 150,
} as const;

const FENCE = '---';

export interface SplitArticle {
  /** Raw YAML between the fences, or null when the article has no frontmatter. */
  frontmatter: string | null;
  body: string;
}

/** Split `---` fenced frontmatter from the body. Input must already use `\n` line endings. */
export function splitFrontmatter(source: string): SplitArticle {
  if (!source.startsWith(`${FENCE}\n`)) {
    return { frontmatter: null, body: source };
  }

  const closing = /^---[ \t]*$/m;
  const rest = source.slice(FENCE.length + 1);
  const match = closing.exec(rest);
  if (!match) {
    throw new FrontmatterError([{ path: '(frontmatter)', message: 'missing closing --- fence' }]);
  }

  const frontmatter = rest.slice(0, match.index);
  // Leading blank lines are insignificant in Markdown; the canonical form has one.
  const body = rest.slice(match.index + match[0].length).replace(/^\n+/, '');

  return { frontmatter, body };
}

/** Parse and validate frontmatter YAML. Throws FrontmatterError listing every problem found. */
export function parseFrontmatter(yamlSource: string): ArticleFrontmatter {
  if (new TextEncoder().encode(yamlSource).length > FRONTMATTER_LIMITS.frontmatterBytes) {
    throw new FrontmatterError([
      { path: '(frontmatter)', message: `larger than ${FRONTMATTER_LIMITS.frontmatterBytes} bytes` },
    ]);
  }

  let data: unknown;
  try {
    // `resolveKnownTags: false`: even with the failsafe schema, `yaml` turns
    // explicit `!!binary` / `!!timestamp` / `!!set` values into non-strings,
    // while PyYAML's BaseLoader keeps them strings. Unknown tags resolve as
    // plain strings on both sides. `logLevel: 'error'` still throws errors but
    // never prints warnings, which quote the source.
    data = parseYaml(yamlSource, {
      schema: 'failsafe',
      uniqueKeys: true,
      maxAliasCount: 0,
      resolveKnownTags: false,
      logLevel: 'error',
    });
  } catch (error) {
    if (error instanceof RangeError || !(error instanceof Error)) {
      // A stack overflow on absurdly nested flow collections is still a frontmatter problem.
      throw new FrontmatterError([{ path: '(frontmatter)', message: 'YAML error: nested too deeply' }]);
    }
    const message = error instanceof YAMLParseError ? (error.message.split('\n')[0] ?? error.message) : error.message;
    throw new FrontmatterError([{ path: '(frontmatter)', message: `YAML error: ${message}` }]);
  }

  return validateFrontmatter(data);
}

/** Validate parsed YAML data (strings only, as the failsafe schema produces). */
export function validateFrontmatter(data: unknown): ArticleFrontmatter {
  const issues: FrontmatterIssue[] = [];

  if (!isPlainObject(data)) {
    throw new FrontmatterError([{ path: '(frontmatter)', message: 'must be a mapping' }]);
  }

  for (const key of Object.keys(data)) {
    if (!(FRONTMATTER_KEYS as readonly string[]).includes(key)) {
      issues.push({ path: key, message: 'unknown key' });
    }
  }

  for (const key of REQUIRED_KEYS) {
    if (!(key in data)) {
      issues.push({ path: key, message: 'required' });
    }
  }

  const title = plainText(data.title, 'title', FRONTMATTER_LIMITS.title, issues);
  const slug = slugValue(data.slug, 'slug', FRONTMATTER_LIMITS.slug, issues);
  const excerpt = plainText(data.excerpt, 'excerpt', FRONTMATTER_LIMITS.excerpt, issues);
  const pillar = slugValue(data.pillar, 'pillar', FRONTMATTER_LIMITS.slug, issues);
  const format = slugValue(data.format, 'format', FRONTMATTER_LIMITS.slug, issues);
  const tags = tagList(data.tags, issues);
  const series = data.series === undefined ? undefined : seriesValue(data.series, issues);

  if (issues.length > 0) {
    throw new FrontmatterError(issues);
  }

  const result: ArticleFrontmatter = { title, slug, excerpt, pillar, format, tags };
  if (series) {
    result.series = series;
  }

  return result;
}

/** Canonical YAML for the frontmatter, including both `---` fences and a trailing newline. */
export function serializeFrontmatter(fm: ArticleFrontmatter): string {
  const lines = [
    FENCE,
    `title: ${quoteYamlString(fm.title)}`,
    `slug: ${fm.slug}`,
    `excerpt: ${quoteYamlString(fm.excerpt)}`,
    `pillar: ${fm.pillar}`,
    `format: ${fm.format}`,
    `tags: [${fm.tags.join(', ')}]`,
  ];

  if (fm.series) {
    lines.push('series:');
    lines.push(`  id: ${fm.series.id}`);
    lines.push(`  title: ${quoteYamlString(fm.series.title)}`);
    lines.push(`  part: ${fm.series.part}`);
    if (fm.series.total !== undefined) {
      lines.push(`  total: ${fm.series.total}`);
    }
  }

  lines.push(FENCE);
  return `${lines.join('\n')}\n`;
}

/** True when two frontmatter values hold the same data (key order does not matter). */
export function sameFrontmatter(a: ArticleFrontmatter | null, b: ArticleFrontmatter | null): boolean {
  if (a === null || b === null) {
    return a === b;
  }

  return (
    a.title === b.title &&
    a.slug === b.slug &&
    a.excerpt === b.excerpt &&
    a.pillar === b.pillar &&
    a.format === b.format &&
    a.tags.length === b.tags.length &&
    a.tags.every((tag, index) => tag === b.tags[index]) &&
    a.series?.id === b.series?.id &&
    a.series?.title === b.series?.title &&
    a.series?.part === b.series?.part &&
    a.series?.total === b.series?.total
  );
}

/**
 * Double-quoted YAML scalar. JSON-style escapes are valid YAML 1.1 and 1.2;
 * characters YAML treats as line breaks or forbids are escaped as `\uXXXX`.
 */
export function quoteYamlString(value: string): string {
  let out = '"';
  for (const ch of value) {
    const cp = ch.codePointAt(0) ?? 0;
    if (ch === '"') {
      out += '\\"';
    } else if (ch === '\\') {
      out += '\\\\';
    } else if (ch === '\n') {
      out += '\\n';
    } else if (ch === '\t') {
      out += '\\t';
    } else if (ch === '\r') {
      out += '\\r';
    } else if (needsUnicodeEscape(cp)) {
      out += `\\u${cp.toString(16).toUpperCase().padStart(4, '0')}`;
    } else {
      out += ch;
    }
  }

  return `${out}"`;
}

function needsUnicodeEscape(cp: number): boolean {
  return (
    cp < 0x20 ||
    cp === 0x7f ||
    (cp >= 0x80 && cp <= 0x9f) ||
    cp === 0x2028 ||
    cp === 0x2029 ||
    cp === 0xfeff ||
    cp === 0xfffe ||
    cp === 0xffff ||
    (cp >= 0xd800 && cp <= 0xdfff)
  );
}

/** Control characters, lone surrogates and line separators are not plain text. */
const NOT_PLAIN_TEXT =
  // eslint-disable-next-line no-control-regex -- control characters are exactly what this detects.
  /[\u0000-\u001f\u007f-\u009f\u2028\u2029\uFEFF\uFFFE\uFFFF]|[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/;

function plainText(value: unknown, path: string, max: number, issues: FrontmatterIssue[]): string {
  if (typeof value !== 'string') {
    if (value !== undefined) {
      issues.push({ path, message: 'must be a string' });
    }
    return '';
  }

  if (value.trim() === '') {
    issues.push({ path, message: 'must not be empty' });
  } else if (value !== value.trim()) {
    issues.push({ path, message: 'must not start or end with whitespace' });
  }

  if (value.includes('<')) {
    issues.push({ path, message: 'must not contain "<" (plain text only)' });
  }

  if (NOT_PLAIN_TEXT.test(value)) {
    issues.push({ path, message: 'must not contain control characters or line breaks' });
  }

  if (Array.from(value).length > max) {
    issues.push({ path, message: `must be at most ${max} characters` });
  }

  return value;
}

function slugValue(value: unknown, path: string, max: number, issues: FrontmatterIssue[]): string {
  if (typeof value !== 'string') {
    if (value !== undefined) {
      issues.push({ path, message: 'must be a string' });
    }
    return '';
  }

  if (!SLUG_PATTERN.test(value)) {
    issues.push({ path, message: 'must be lowercase kebab-case ([a-z0-9-])' });
  } else if (value.length > max) {
    issues.push({ path, message: `must be at most ${max} characters` });
  }

  return value;
}

function tagList(value: unknown, issues: FrontmatterIssue[]): string[] {
  if (value === undefined) {
    return [];
  }

  if (!Array.isArray(value)) {
    issues.push({ path: 'tags', message: 'must be a list' });
    return [];
  }

  const tags: unknown[] = value;
  if (tags.length > FRONTMATTER_LIMITS.tags) {
    issues.push({ path: 'tags', message: `must have at most ${FRONTMATTER_LIMITS.tags} entries` });
  }

  const seen = new Set<string>();
  tags.forEach((tag, index) => {
    const tagValue = slugValue(tag, `tags[${index}]`, FRONTMATTER_LIMITS.tag, issues);
    if (seen.has(tagValue)) {
      issues.push({ path: `tags[${index}]`, message: 'duplicate tag' });
    }
    seen.add(tagValue);
  });

  return tags.filter((tag): tag is string => typeof tag === 'string');
}

function seriesValue(value: unknown, issues: FrontmatterIssue[]): ArticleSeries | undefined {
  if (!isPlainObject(value)) {
    issues.push({ path: 'series', message: 'must be a mapping' });
    return undefined;
  }

  for (const key of Object.keys(value)) {
    if (!(SERIES_KEYS as readonly string[]).includes(key)) {
      issues.push({ path: `series.${key}`, message: 'unknown key' });
    }
  }

  for (const key of ['id', 'title', 'part'] as const) {
    if (!(key in value)) {
      issues.push({ path: `series.${key}`, message: 'required' });
    }
  }

  const id = slugValue(value.id, 'series.id', FRONTMATTER_LIMITS.slug, issues);
  const title = plainText(value.title, 'series.title', FRONTMATTER_LIMITS.seriesTitle, issues);
  const part = positiveInt(value.part, 'series.part', issues);
  const total = value.total === undefined ? undefined : positiveInt(value.total, 'series.total', issues);

  if (part !== undefined && total !== undefined && total < part) {
    issues.push({ path: 'series.total', message: 'must be greater than or equal to series.part' });
  }

  const series: ArticleSeries = { id, title, part: part ?? 1 };
  if (total !== undefined) {
    series.total = total;
  }

  return series;
}

function positiveInt(value: unknown, path: string, issues: FrontmatterIssue[]): number | undefined {
  if (typeof value !== 'string' || !/^[1-9][0-9]{0,3}$/.test(value)) {
    if (value !== undefined) {
      issues.push({ path, message: 'must be a positive integer (1-9999)' });
    }
    return undefined;
  }

  return Number.parseInt(value, 10);
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}
