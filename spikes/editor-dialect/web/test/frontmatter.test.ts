import fc from 'fast-check'
import { describe, expect, test } from 'vitest'
import { parse as parseYaml } from 'yaml'

import {
  type ArticleFrontmatter,
  FrontmatterError,
  parseFrontmatter,
  quoteYamlString,
  serializeFrontmatter,
  splitFrontmatter,
} from '../src/dialect/frontmatter.js'

const VALID = `title: "Least-privilege tool calls"
slug: least-privilege-tool-calls
excerpt: "How to scope one tool call."
pillar: agents
format: deep-explainer
tags: [agents, identity]
`

function issuesOf(yaml: string): string[] {
  try {
    parseFrontmatter(yaml)
  } catch (error) {
    if (error instanceof FrontmatterError) {
      return error.issues.map(issue => `${issue.path}: ${issue.message}`)
    }
    throw error
  }
  return []
}

describe('splitFrontmatter', () => {
  test('splits fenced YAML from the body', () => {
    expect(splitFrontmatter('---\na: b\n---\n\nBody\n')).toEqual({ frontmatter: 'a: b\n', body: 'Body\n' })
  })

  test('accepts a body directly after the closing fence', () => {
    expect(splitFrontmatter('---\na: b\n---\nBody\n').body).toBe('Body\n')
  })

  test('no frontmatter', () => {
    expect(splitFrontmatter('Body\n')).toEqual({ frontmatter: null, body: 'Body\n' })
  })

  test('an unclosed fence is an error', () => {
    expect(() => splitFrontmatter('---\na: b\n')).toThrow(FrontmatterError)
  })
})

describe('parseFrontmatter', () => {
  test('valid frontmatter', () => {
    expect(parseFrontmatter(VALID)).toEqual({
      title: 'Least-privilege tool calls',
      slug: 'least-privilege-tool-calls',
      excerpt: 'How to scope one tool call.',
      pillar: 'agents',
      format: 'deep-explainer',
      tags: ['agents', 'identity'],
    })
  })

  test('series with and without total', () => {
    const withTotal = parseFrontmatter(`${VALID}series:\n  id: agent-identity\n  title: "Agent identity"\n  part: 2\n  total: 4\n`)
    expect(withTotal.series).toEqual({ id: 'agent-identity', title: 'Agent identity', part: 2, total: 4 })
    const open = parseFrontmatter(`${VALID}series: {id: agent-identity, title: Agent identity, part: 1}\n`)
    expect(open.series).toEqual({ id: 'agent-identity', title: 'Agent identity', part: 1 })
  })

  test('explicit tags stay strings, like PyYAML BaseLoader on the server', () => {
    // Reviewer: `yaml` resolved !!binary and !!timestamp even with the failsafe schema, so
    // the editor rejected frontmatter the server accepted (test_frontmatter.py has the twin).
    const fm = parseFrontmatter(VALID.replace('title: "Least-privilege tool calls"', 'title: !!binary aGk=').replace('pillar: agents', 'pillar: !!timestamp 2024-01-01'))
    expect(fm.title).toBe('aGk=')
    expect(fm.pillar).toBe('2024-01-01')
    expect(issuesOf(VALID.replace('[agents, identity]', '!!set {agents, identity}'))).toContain('tags: must be a list')
  })

  test('YAML 1.1 lookalikes stay strings (failsafe schema, like the Python twin)', () => {
    const fm = parseFrontmatter(VALID.replace('title: "Least-privilege tool calls"', 'title: no').replace('[agents, identity]', '[yes, on, null]'))
    expect(fm.title).toBe('no')
    expect(fm.tags).toEqual(['yes', 'on', 'null'])
  })

  test.each([
    ['unknown key', `${VALID}author: me\n`, 'author: unknown key'],
    ['missing key', VALID.replace(/^pillar:.*\n/m, ''), 'pillar: required'],
    ['html in title', VALID.replace('Least-privilege tool calls', '<b>bold</b>'), 'title: must not contain "<" (plain text only)'],
    ['bad slug', VALID.replace('least-privilege-tool-calls', 'Least_Privilege'), 'slug: must be lowercase kebab-case ([a-z0-9-])'],
    ['duplicate tag', VALID.replace('[agents, identity]', '[agents, agents]'), 'tags[1]: duplicate tag'],
    ['tags not a list', VALID.replace('[agents, identity]', 'agents'), 'tags: must be a list'],
    ['part not a number', `${VALID}series: {id: a, title: T, part: two}\n`, 'series.part: must be a positive integer (1-9999)'],
    ['total before part', `${VALID}series: {id: a, title: T, part: 3, total: 2}\n`, 'series.total: must be greater than or equal to series.part'],
    ['empty excerpt', VALID.replace('"How to scope one tool call."', '""'), 'excerpt: must not be empty'],
    ['padded title', VALID.replace('"Least-privilege tool calls"', '" padded"'), 'title: must not start or end with whitespace'],
    ['line break in title', VALID.replace('"Least-privilege tool calls"', '"a\\nb"'), 'title: must not contain control characters or line breaks'],
  ])('%s', (_name, yaml, issue) => {
    expect(issuesOf(yaml)).toContain(issue)
  })

  test('duplicate keys are rejected', () => {
    expect(issuesOf(`${VALID}slug: other\n`)[0]).toMatch(/YAML error/)
  })

  test('aliases are rejected', () => {
    expect(issuesOf(`${VALID.replace('pillar: agents', 'pillar: &p agents')}format2: *p\n`)[0]).toMatch(/YAML error|unknown key/)
  })

  test('oversized frontmatter is rejected', () => {
    expect(issuesOf(`${VALID}# ${'x'.repeat(9000)}\n`)[0]).toMatch(/larger than/)
  })
})

describe('serializeFrontmatter', () => {
  const fm: ArticleFrontmatter = {
    title: 'Migrating SSO: "the boring way"',
    slug: 'okta-to-entra',
    excerpt: 'Back\\slash and unicode → ✓',
    pillar: 'identity',
    format: 'case-study',
    tags: [],
    series: { id: 'okta-entra', title: 'Okta → Entra', part: 2 },
  }

  test('canonical layout and key order', () => {
    expect(serializeFrontmatter(fm)).toBe(
      [
        '---',
        'title: "Migrating SSO: \\"the boring way\\""',
        'slug: okta-to-entra',
        'excerpt: "Back\\\\slash and unicode → ✓"',
        'pillar: identity',
        'format: case-study',
        'tags: []',
        'series:',
        '  id: okta-entra',
        '  title: "Okta → Entra"',
        '  part: 2',
        '---',
        '',
      ].join('\n'),
    )
  })

  test('serialize -> parse is the identity', () => {
    const yaml = serializeFrontmatter(fm).replace(/^---\n|---\n$/g, '')
    expect(parseFrontmatter(yaml)).toEqual(fm)
  })

  test('quoteYamlString round-trips arbitrary strings through a YAML parser', () => {
    fc.assert(
      fc.property(fc.string({ unit: 'binary', maxLength: 40 }), value => {
        expect(parseYaml(`v: ${quoteYamlString(value)}`, { schema: 'failsafe' })).toEqual({ v: value })
      }),
      { numRuns: 2000, seed: 20261125 },
    )
  })
})
