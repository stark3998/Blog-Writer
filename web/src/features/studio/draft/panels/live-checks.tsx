import { RotateCw } from 'lucide-react';
import { useId } from 'react';

import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { ProblemAlert } from '@/features/data/problem-alert';
import type { ContentLossWarning } from '@/features/editor';

import { plural } from '../model/format';
import type { Finding } from '../model/types';

import { FindingList } from './finding-list';
import type { LiveLint } from './use-live-lint';

function countLine(findings: readonly Finding[]): string {
  const blocking = findings.filter((finding) => finding.severity === 'block').length;
  const warnings = findings.filter((finding) => finding.severity === 'warn').length;
  const notes = findings.length - blocking - warnings;
  const parts = [
    blocking > 0 ? plural(blocking, 'blocker', 'blockers') : undefined,
    warnings > 0 ? plural(warnings, 'warning', 'warnings') : undefined,
    notes > 0 ? plural(notes, 'note', 'notes') : undefined,
  ].filter((part) => part !== undefined);
  return parts.length === 0 ? 'Nothing to fix.' : `${parts.join(', ')}.`;
}

export interface LiveChecksProps {
  lint: LiveLint;
  /** Findings from the last save's normalizer. */
  saveFindings: readonly Finding[];
  /** What the last save could not hold exactly. */
  lossWarnings: readonly ContentLossWarning[];
  onShowLint: (finding: Finding) => void;
  onShowBlock: (warning: ContentLossWarning) => void;
}

/**
 * The quick checks while writing: the gates' deterministic part on the text as it is now, plus
 * what the last save reported. The same findings are underlined in the text.
 */
export function LiveChecks({ lint, saveFindings, lossWarnings, onShowLint, onShowBlock }: LiveChecksProps) {
  const headingId = useId();
  const findings = lint.findings;
  return (
    <section aria-labelledby={headingId} className="grid gap-3">
      <div className="flex items-center justify-between gap-3">
        <h2 id={headingId} className="font-serif text-xl font-medium text-ink">
          Live checks
        </h2>
        <Button size="sm" variant="ghost" disabled={lint.checking} onClick={lint.recheck}>
          <RotateCw aria-hidden="true" />
          {lint.checking ? 'Checking…' : 'Check now'}
        </Button>
      </div>
      <p className="-mt-1 text-sm text-ink-muted">
        Formatting, links, your signal, the blocklist and slop patterns, a moment after you stop typing.
      </p>
      {lossWarnings.length > 0 ? (
        <div className="grid gap-2 rounded-md border border-warn bg-warn-soft px-3 py-2 text-sm">
          <p className="font-medium text-ink">Markdown can’t hold everything exactly:</p>
          <ul className="grid gap-1">
            {lossWarnings.map((warning) => (
              <li key={`${warning.code}-${String(warning.blocks?.from ?? 0)}`} className="flex flex-wrap items-baseline gap-x-2">
                <span className="text-ink-muted">{warning.message}</span>
                {warning.blocks ? (
                  <Button
                    variant="link"
                    size="sm"
                    className="text-xs"
                    onClick={() => {
                      onShowBlock(warning);
                    }}
                  >
                    Show in text
                  </Button>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {lint.error ? (
        <ProblemAlert error={lint.error} title="The live checks didn’t run." onRetry={lint.recheck} retryLabel="Run the live checks again" />
      ) : null}
      {findings === undefined ? (
        lint.checking ? (
          <div aria-busy="true" className="grid gap-2">
            <p className="sr-only">Checking the text</p>
            <Skeleton className="h-10" />
            <Skeleton className="h-10" />
          </div>
        ) : (
          <p className="text-sm text-ink-muted">The checks run once the draft has loaded.</p>
        )
      ) : (
        <>
          <p role="status" className="text-sm font-medium text-ink">
            {countLine(findings)}
          </p>
          {findings.length > 0 ? <FindingList findings={findings} onShow={onShowLint} /> : null}
        </>
      )}
      {saveFindings.length > 0 ? (
        <div className="grid gap-1">
          <h3 className="text-sm font-semibold text-ink">From the last save</h3>
          <FindingList findings={saveFindings} />
        </div>
      ) : null}
    </section>
  );
}
