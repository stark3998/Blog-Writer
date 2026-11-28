import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { contrast, parseOklch, toSrgb } from '@/test/color';
import { readProjectFile } from '@/test/files';

import { Alert, AlertActions, AlertDescription, AlertTitle } from './alert';
import { Button } from './button';
import { describeDiff, diffBlocks, DiffView, splitBlocks, summarizeDiff } from './diff-view';
import { Progress } from './progress';
import { formatStatValue, StatTile } from './stat-tile';
import { Table, TableBody, TableCaption, TableCell, TableHead, TableHeader, TableRow } from './table';

describe('Alert', () => {
  it('announces an error as it appears, with its icon decorative', () => {
    render(
      <Alert variant="error">
        <AlertTitle>Couldn’t save the pillars.</AlertTitle>
        <AlertDescription>The server had a problem. Try again in a moment.</AlertDescription>
        <AlertActions>
          <Button size="sm">Try again</Button>
        </AlertActions>
      </Alert>,
    );
    const alert = screen.getByRole('alert');
    expect(alert).toHaveTextContent('Couldn’t save the pillars.The server had a problem. Try again in a moment.');
    expect(alert).toHaveClass('bg-danger-soft');
    expect(alert.querySelector('svg')).toHaveAttribute('aria-hidden', 'true');
    expect(within(alert).getByRole('button', { name: 'Try again' })).toBeInTheDocument();
  });

  it('keeps the other variants as plain content unless asked to announce', () => {
    render(
      <>
        <Alert variant="warn" data-testid="warn">
          <AlertTitle>LinkedIn needs reconnecting in 7 days.</AlertTitle>
        </Alert>
        <Alert variant="success" role="status">
          <AlertTitle>Published.</AlertTitle>
        </Alert>
        <Alert variant="info" icon={null} data-testid="info">
          <AlertDescription>Insights moves to its own area later.</AlertDescription>
        </Alert>
        <Alert variant="error" role={undefined} data-testid="standing">
          <AlertTitle>Publishing to the portfolio isn’t set up yet.</AlertTitle>
        </Alert>
      </>,
    );
    expect(screen.getByTestId('warn')).not.toHaveAttribute('role');
    expect(screen.getByTestId('warn')).toHaveClass('bg-warn-soft');
    expect(screen.getByRole('status')).toHaveTextContent('Published.');
    expect(screen.getByTestId('info').querySelector('svg')).toBeNull();
    expect(screen.getByTestId('standing')).not.toHaveAttribute('role');
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });
});

describe('Progress', () => {
  it('is a named progress bar with its value', () => {
    render(<Progress value={3} max={12} aria-label="Sources read" getValueLabel={(value, max) => `${String(value)} of ${String(max)}`} />);
    const bar = screen.getByRole('progressbar', { name: 'Sources read' });
    expect(bar).toHaveAttribute('aria-valuenow', '3');
    expect(bar).toHaveAttribute('aria-valuemax', '12');
    expect(bar).toHaveAttribute('aria-valuetext', '3 of 12');
    expect(bar.firstElementChild).toHaveStyle({ transform: 'translateX(-75%)' });
  });

  it('is indeterminate without a value, and clamps values out of range', () => {
    render(
      <>
        <Progress aria-label="Rendering" />
        <Progress aria-label="Upload" value={140} tone="warn" />
      </>,
    );
    const rendering = screen.getByRole('progressbar', { name: 'Rendering' });
    expect(rendering).not.toHaveAttribute('aria-valuenow');
    expect(rendering).toHaveAttribute('data-state', 'indeterminate');
    expect(rendering.firstElementChild).toHaveClass('animate-progress');
    const upload = screen.getByRole('progressbar', { name: 'Upload' });
    expect(upload).toHaveAttribute('aria-valuenow', '100');
    expect(upload).toHaveAttribute('data-state', 'complete');
    expect(upload.firstElementChild).toHaveClass('bg-warn');
  });
});

describe('Table', () => {
  it('is named by its caption, with column headers and aligned numbers', () => {
    render(
      <Table>
        <TableCaption>Snapshots of the last three posts</TableCaption>
        <TableHeader>
          <TableRow>
            <TableHead>Post</TableHead>
            <TableHead numeric>Impressions</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          <TableRow data-state="selected">
            <TableCell>Agent identity</TableCell>
            <TableCell numeric>12,940</TableCell>
          </TableRow>
        </TableBody>
      </Table>,
    );
    const table = screen.getByRole('table', { name: 'Snapshots of the last three posts' });
    const header = within(table).getByRole('columnheader', { name: 'Impressions' });
    expect(header).toHaveAttribute('scope', 'col');
    expect(header).toHaveClass('text-right');
    expect(within(table).getByRole('cell', { name: '12,940' })).toHaveClass('tabular-nums', 'text-right');
    expect(within(table).getAllByRole('row')[1]).toHaveClass('data-[state=selected]:bg-wash-strong');
    expect(table.parentElement).toHaveClass('overflow-x-auto');
  });
});

describe('StatTile', () => {
  it('formats values for reading: full below 10,000, compact above', () => {
    expect(formatStatValue(1284)).toBe('1,284');
    expect(formatStatValue(12_940)).toBe('12.9K');
    expect(formatStatValue(4_200_000)).toBe('4.2M');
    expect(formatStatValue(3.14159)).toBe('3.1');
  });

  it('pairs the label with the value, and the change with a sign, an arrow and a comparison', () => {
    render(
      <StatTile
        label="Impressions per post"
        value={12_940}
        unit="per post"
        delta={{ value: 0.12, label: 'vs your median' }}
        caption="Last 8 weeks"
      />,
    );
    const term = screen.getByRole('term');
    expect(term).toHaveTextContent('Impressions per post');
    const [value, change, caption] = screen.getAllByRole('definition');
    expect(value).toHaveTextContent('12.9Kper post');
    expect(value).not.toHaveClass('tabular-nums');
    expect(change).toHaveTextContent('+12%vs your median');
    expect(change).toHaveClass('text-success');
    expect(change?.querySelector('svg')).toHaveAttribute('aria-hidden', 'true');
    expect(caption).toHaveTextContent('Last 8 weeks');
  });

  it('colours a change by which way is good, and shows a missing value as no data', () => {
    render(
      <>
        <StatTile label="Spend" value={41.5} delta={{ value: 0.3, label: 'vs last week', good: 'down' }} data-testid="spend" />
        <StatTile label="Posts" value={3} delta={{ value: 0, label: 'vs plan', format: 'number' }} data-testid="posts" />
        <StatTile label="Saves per 1K" value={null} delta={{ value: 0.5, label: 'vs median' }} data-testid="saves" />
      </>,
    );
    const spend = within(screen.getByTestId('spend')).getAllByRole('definition')[1];
    expect(spend).toHaveClass('text-danger');
    expect(spend).toHaveTextContent('+30%');
    const posts = within(screen.getByTestId('posts')).getAllByRole('definition')[1];
    expect(posts).toHaveClass('text-ink-muted');
    expect(posts).toHaveTextContent('0vs plan');
    const saves = screen.getByTestId('saves');
    expect(within(saves).getAllByRole('definition')).toHaveLength(1);
    expect(within(saves).getByText('No data yet')).toHaveClass('sr-only');
  });
});

describe('diffBlocks', () => {
  it('splits Markdown at blank lines but keeps a fenced code block whole', () => {
    expect(splitBlocks('# Title\n\nOne\ntwo\n\n\n```ts\nconst a = 1;\n\nconst b = 2;\n```\n\nEnd\r\n')).toEqual([
      '# Title',
      'One\ntwo',
      '```ts\nconst a = 1;\n\nconst b = 2;\n```',
      'End',
    ]);
  });

  it('finds kept, added and removed paragraphs, and word edits inside a changed one', () => {
    const before = 'Intro.\n\nAgents need identity.\n\nOld closing.';
    const after = 'Intro.\n\nAgents need a real identity.\n\nNew section.\n\nOld closing.';
    const blocks = diffBlocks(before, after);
    expect(blocks.map((block) => block.kind)).toEqual(['same', 'changed', 'added', 'same']);
    const changed = blocks[1];
    if (changed?.kind !== 'changed') throw new Error('expected a changed paragraph');
    expect(changed.segments.filter((segment) => segment.kind === 'added').map((segment) => segment.text).join('')).toBe(
      'a real ',
    );
    expect(summarizeDiff(blocks)).toEqual({ changed: 1, added: 1, removed: 0 });
    expect(describeDiff(summarizeDiff(blocks))).toBe('1 paragraph changed, 1 added.');
    expect(describeDiff(summarizeDiff(diffBlocks('a', 'a')))).toBe('No changes.');
    expect(describeDiff({ changed: 0, added: 0, removed: 2 })).toBe('2 paragraphs removed.');
  });

  it('folds long unchanged runs, keeping context around each change', () => {
    const paragraphs = Array.from({ length: 10 }, (_, index) => `Paragraph ${String(index)}.`);
    const before = paragraphs.join('\n\n');
    const after = paragraphs.map((text, index) => (index === 5 ? 'Paragraph five, rewritten.' : text)).join('\n\n');
    const blocks = diffBlocks(before, after, { context: 1 });
    expect(blocks.map((block) => block.kind)).toEqual(['skipped', 'same', 'changed', 'same', 'skipped']);
    expect(blocks[0]).toEqual({ kind: 'skipped', count: 4 });
    expect(blocks[4]).toEqual({ kind: 'skipped', count: 3 });
  });
});

describe('DiffView', () => {
  it('marks removals and additions with more than colour, and says so to screen readers', () => {
    render(
      <DiffView
        label="Proposed changes to the selection"
        before={'Agents need identity.\n\nCut this.'}
        after={'Agents need a real identity.\n\nA new point.'}
      />,
    );
    const view = screen.getByRole('group', { name: 'Proposed changes to the selection' });
    expect(within(view).getByText('2 paragraphs changed.')).toBeInTheDocument();
    const inserted = view.querySelectorAll('ins');
    expect(inserted.length).toBeGreaterThan(0);
    for (const element of inserted) expect(element).toHaveClass('underline');
    const deleted = view.querySelectorAll('del');
    expect(deleted.length).toBeGreaterThan(0);
    for (const element of deleted) expect(element).toHaveClass('line-through');
    expect(within(view).getAllByText('Changed paragraph:')).toHaveLength(2);
    expect(within(view).getAllByText(/^(added|removed)$/).every((label) => label.classList.contains('sr-only'))).toBe(
      true,
    );
  });

  it('shows whole added and removed paragraphs, and can hide the summary', () => {
    render(<DiffView before={'Keep.\n\nGone.'} after={'Keep.'} showSummary={false} />);
    const view = screen.getByRole('group', { name: 'Changes' });
    expect(within(view).queryByText(/removed\.$/)).not.toBeInTheDocument();
    const removed = view.querySelector('del');
    expect(removed).toHaveTextContent('Removed paragraph: Gone.');
  });
});

describe('state colours', () => {
  const css = readProjectFile('src/styles/index.css');
  const tokensIn = (selector: string) => {
    const start = css.indexOf(`${selector} {`);
    const body = css.slice(start, css.indexOf('\n  }', css.indexOf('--color-', start)));
    return new Map([...body.matchAll(/--color-([\w-]+):\s*(oklch\([^)]*\));/g)].map(([, name = '', value = '']) => [name, value]));
  };
  const themes = { light: tokensIn('@theme'), dark: tokensIn('.dark') };

  it.each(['light', 'dark'] as const)('keep alert and diff text readable on the soft backgrounds (%s)', (theme) => {
    const palette = themes[theme];
    const colour = (name: string) => {
      const value = palette.get(name);
      if (!value) throw new Error(`--color-${name} missing in the ${theme} theme`);
      return toSrgb(parseOklch(value));
    };
    for (const soft of ['success-soft', 'warn-soft', 'danger-soft']) {
      for (const text of ['ink', 'ink-muted', 'ink-subtle']) {
        const ratio = contrast(colour(text), colour(soft));
        expect(ratio, `${text} on ${soft}: ${ratio.toFixed(2)}`).toBeGreaterThanOrEqual(4.5);
      }
    }
  });
});
