import { Editor } from '@tiptap/core';
import { afterEach, beforeAll, describe, expect, it } from 'vitest';

import { DialectError } from '@/features/editor';

import { briefFixture, SAMPLE_MARKDOWN } from '../testing/fixtures';
import { installEditorLayout, testDialect } from '../testing/environment';

import { BLOCK_COMMANDS, filterCommands } from './commands';
import { createDraftExtensions, looksLikeMarkdown } from './extensions';
import { setFindingMarks } from './finding-marks';
import { captureSelection, citationDefinition, definitionsMarkdown, insertCitation, insertFragment, parseFragment } from './fragments';
import { linkProblem } from './link-popover';
import { slashMatch } from './slash';

let editor: Editor | undefined;

beforeAll(() => {
  installEditorLayout();
});

afterEach(() => {
  editor?.destroy();
  editor = undefined;
});

async function editorWith(markdown: string): Promise<Editor> {
  const created = new Editor({
    element: document.createElement('div'),
    extensions: createDraftExtensions({ bus: null, slashKeys: null, dialect: null, parseTimeoutMs: 1000, renderDiagrams: false }),
    content: { type: 'doc', content: [{ type: 'paragraph' }] },
    injectCSS: false,
    enableContentCheck: true,
  });
  editor = created;
  const parsed = await testDialect().parse(markdown);
  created.commands.setContent(parsed.doc, { emitUpdate: false });
  return created;
}

function positionOf(target: Editor, text: string): number {
  let found = -1;
  target.state.doc.descendants((node, pos) => {
    if (found !== -1) return false;
    if (node.isText && node.text?.includes(text)) found = pos + (node.text.indexOf(text));
    return true;
  });
  if (found === -1) throw new Error(`"${text}" is not in the document`);
  return found;
}

describe('selection capture', () => {
  it('sends a selection inside one paragraph as it is, inline', async () => {
    const target = await editorWith(SAMPLE_MARKDOWN);
    const from = positionOf(target, 'an inventory');
    target.commands.setTextSelection({ from, to: from + 'an inventory'.length });
    expect(captureSelection(target)).toEqual({ range: { from, to: from + 12 }, markdown: 'an inventory', inline: true });
  });

  it('widens a selection across blocks to whole top-level blocks', async () => {
    const target = await editorWith(SAMPLE_MARKDOWN);
    const from = positionOf(target, 'Least privilege');
    const to = positionOf(target, 'What to do') + 4;
    target.commands.setTextSelection({ from, to });
    const captured = captureSelection(target);
    if (typeof captured === 'string') throw new Error(captured);
    expect(captured.inline).toBe(false);
    expect(captured.markdown).toBe('- Least privilege per tool call\n- Short-lived tokens\n\n## What to do first');
  });

  it('refuses empty selections, code and the sources list', async () => {
    const target = await editorWith('Text\n\n```ts\nconst a = 1\n```\n');
    expect(captureSelection(target)).toBe('empty');
    const from = positionOf(target, 'const');
    target.commands.setTextSelection({ from, to: from + 5 });
    expect(captureSelection(target)).toBe('code');
  });
});

describe('fragments from outside the editor', () => {
  it('keeps citations by parsing with the article’s definitions, and adds new ones', async () => {
    const target = await editorWith(SAMPLE_MARKDOWN);
    const definitions = definitionsMarkdown(target);
    expect(definitions).toContain('[^nist-ai-rmf]: NIST.');
    const fragment = await parseFragment(
      testDialect(),
      'Every call needs an owner.[^nist-ai-rmf] See OWASP.[^owasp]\n\n[^owasp]: OWASP. *Top 10*. [owasp.org](https://owasp.org/)',
      definitions,
      1000,
    );
    expect(fragment.blocks).toHaveLength(1);
    expect(JSON.stringify(fragment.blocks)).toContain('"type":"citation"');
    const from = positionOf(target, 'Start with');
    const sentence = 'Start with an inventory of the agents you already run.';
    insertFragment(target, { from, to: from + sentence.length }, fragment, true);
    const markdown = target.getMarkdown();
    expect(markdown).toContain('Every call needs an owner.[^nist-ai-rmf] See OWASP.[^owasp]');
    expect(markdown).toContain('[^owasp]: OWASP. *Top 10*. [owasp.org](https://owasp.org/)');
    expect(markdown).toContain('## What to do first\n\nEvery call');
  });

  it('reads a leading rule as Markdown, not as frontmatter', async () => {
    const fragment = await parseFragment(testDialect(), '---\n\nAfter a rule.', '', 1000);
    expect(fragment.blocks.map((block) => block.type)).toEqual(['horizontalRule', 'paragraph']);
  });

  it('rejects with the worker’s error', async () => {
    await expect(parseFragment(testDialect(new DialectError('timeout', 'slow')), 'x', '', 1000)).rejects.toThrow('slow');
  });

  it('inserts a brief citation with its definition in the dialect’s form', async () => {
    const target = await editorWith('Agents need owners.\n');
    target.commands.setTextSelection(positionOf(target, 'owners') + 'owners.'.length);
    const [, owasp] = briefFixture().citations ?? [];
    if (!owasp) throw new Error('fixture');
    insertCitation(target, owasp);
    insertCitation(target, owasp);
    expect(target.getMarkdown()).toBe(
      'Agents need owners.[^owasp-llm-top-10][^owasp-llm-top-10]\n\n[^owasp-llm-top-10]: OWASP. *OWASP Top 10 for LLM Applications*. [owasp.org](https://owasp.org/www-project-top-10-for-large-language-model-applications/)\n',
    );
    expect(citationDefinition(owasp).attrs).toEqual({ id: 'owasp-llm-top-10' });
  });
});

describe('the slash trigger', () => {
  it('opens on a slash at the start of a line or after a space, not inside words or code', async () => {
    const target = await editorWith('Hello\n');
    target.chain().setTextSelection(positionOf(target, 'Hello') + 5).insertContent(' /tab').run();
    expect(slashMatch(target.state)).toMatchObject({ active: true, query: 'tab' });
    target.commands.insertContent('le');
    expect(slashMatch(target.state).query).toBe('table');
    target.commands.insertContent(' ');
    expect(slashMatch(target.state).active).toBe(false);
    target.commands.insertContent('a/b');
    expect(slashMatch(target.state).active).toBe(false);
  });

  it('matches commands by label first, then keyword', () => {
    expect(filterCommands(BLOCK_COMMANDS, 'tab')[0]?.id).toBe('table');
    expect(filterCommands(BLOCK_COMMANDS, 'mermaid').map((command) => command.id)).toEqual(['diagram']);
    expect(filterCommands(BLOCK_COMMANDS, 'zzz')).toEqual([]);
    expect(filterCommands(BLOCK_COMMANDS, '')).toHaveLength(BLOCK_COMMANDS.length);
  });
});

describe('proof marks', () => {
  it('underlines findings and moves them with edits', async () => {
    const target = await editorWith('One two three.\n');
    const from = positionOf(target, 'two');
    setFindingMarks(target, [{ from, to: from + 3, severity: 'warn', id: 'lint-0' }]);
    const marked = target.view.dom.querySelector('[data-finding="lint-0"]');
    expect(marked?.textContent).toBe('two');
    expect(marked?.className).toContain('decoration-wavy');
    target.chain().setTextSelection(1).insertContent('Zero ').run();
    expect(target.view.dom.querySelector('[data-finding="lint-0"]')?.textContent).toBe('two');
    setFindingMarks(target, []);
    expect(target.view.dom.querySelector('[data-finding]')).toBeNull();
  });
});

describe('small rules', () => {
  it('accepts the link targets the dialect allows', () => {
    expect(linkProblem('https://example.com/a')).toBeUndefined();
    expect(linkProblem('mailto:me@example.com')).toBeUndefined();
    expect(linkProblem('#section')).toBeUndefined();
    expect(linkProblem('/blog/post')).toBeUndefined();
    expect(linkProblem('http://example.com')).toMatch(/secure web address/);
    expect(linkProblem('//evil.example')).toMatch(/site path/);
    expect(linkProblem('javascript:alert(1)')).toMatch(/secure web address/);
    expect(linkProblem('')).toBe('Enter an address.');
  });

  it('treats only Markdown-looking pastes as Markdown', () => {
    expect(looksLikeMarkdown('## Heading\n\nText')).toBe(true);
    expect(looksLikeMarkdown('- one\n- two')).toBe(true);
    expect(looksLikeMarkdown('Some **bold** words')).toBe(true);
    expect(looksLikeMarkdown('A claim.[^src]')).toBe(true);
    expect(looksLikeMarkdown('Just a plain sentence.')).toBe(false);
  });
});
