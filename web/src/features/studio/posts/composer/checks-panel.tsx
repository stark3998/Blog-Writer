import { Circle, CircleCheck, CircleX, ListChecks } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { cn } from '@/lib/cn';

import { CHECK_DESCRIPTIONS, CHECK_LABELS } from '../model/labels';
import { formatMoment } from '../model/time';
import { CHECK_NAMES, checkStateOf, truncatedCount, type CheckName, type Post } from '../model/types';

export interface ChecksPanelProps {
  post: Post;
  /** Checks the last approval attempt named as failing or out of date (`checks_failing`). */
  failing?: readonly string[];
  onRun?: () => void;
  running: boolean;
  disabledReason?: string;
}

function stateOf(post: Post, name: CheckName, failing: readonly string[]) {
  const state = checkStateOf(post, name);
  if (!state) return { label: 'Not run yet', icon: Circle, tone: 'text-ink-subtle' } as const;
  if (failing.includes(name) && state.passed) {
    return { label: 'Out of date: run again', icon: Circle, tone: 'text-warn' } as const;
  }
  if (!state.passed) return { label: 'Failed', icon: CircleX, tone: 'text-danger' } as const;
  return { label: 'Passed', icon: CircleCheck, tone: 'text-success' } as const;
}

/**
 * The three checks approval needs on exactly the current content (lint, claims against the
 * sources, near-duplicates). Saving re-runs the lint; the other two run with "Run the checks"
 * (`POST /api/posts/{id}/check`, a job).
 */
export function ChecksPanel({ post, failing = [], onRun, running, disabledReason }: ChecksPanelProps) {
  return (
    <div className="grid gap-3">
      <ul className="divide-y divide-line border-y border-line">
        {CHECK_NAMES.map((name) => {
          const state = checkStateOf(post, name);
          const { label, icon: Icon, tone } = stateOf(post, name, failing);
          const findings = state?.findings?.length ?? 0;
          const more = truncatedCount(state);
          return (
            <li key={name} className="flex items-start gap-3 py-3" data-check={name}>
              <Icon aria-hidden="true" className={cn('mt-0.5 size-4 shrink-0', tone)} />
              <div className="grid min-w-0 flex-1 gap-0.5">
                <p className="text-sm font-medium text-ink">
                  {CHECK_LABELS[name]}
                  <span className="sr-only">: </span>
                  <span className={cn('ml-2 font-normal', tone)}>{label}</span>
                </p>
                <p className="text-sm text-ink-muted">{CHECK_DESCRIPTIONS[name]}</p>
                {state ? (
                  <p className="text-xs text-ink-subtle">
                    {`Ran ${formatMoment(state.ranAt)}`}
                    {findings + more > 0 ? `, ${String(findings + more)} ${findings + more === 1 ? 'finding' : 'findings'}` : ''}
                  </p>
                ) : null}
              </div>
            </li>
          );
        })}
      </ul>
      {onRun ? (
        <div className="flex flex-wrap items-center gap-3">
          <Button variant="secondary" disabled={running || disabledReason !== undefined} onClick={onRun}>
            <ListChecks aria-hidden="true" />
            {running ? 'Running the checks…' : 'Run the checks'}
          </Button>
          {disabledReason ? <p className="text-sm text-ink-muted">{disabledReason}</p> : null}
        </div>
      ) : null}
    </div>
  );
}
