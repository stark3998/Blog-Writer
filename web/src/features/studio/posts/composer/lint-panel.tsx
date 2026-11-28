import { CircleX, Info, TriangleAlert, type LucideIcon } from 'lucide-react';
import { useId } from 'react';

import { Button } from '@/components/ui/button';
import { ProblemAlert } from '@/features/data/problem-alert';
import { cn } from '@/lib/cn';

import { findingField, findingInText, findingSlide, type Finding } from '../model/types';

export interface LintPanelProps {
  findings: readonly Finding[];
  /** Findings the server left out of a stored result (`truncated`). */
  truncated?: number;
  /** True while the server's lint for the text on screen is on its way. */
  checking: boolean;
  /** The live lint failed (the stored result is shown instead). */
  error?: unknown;
  onRetry?: () => void;
  /** Selects a finding's span in the text. */
  onShowInText: (finding: Finding) => void;
  /** Brings a slide into view. */
  onShowSlide: (index: number) => void;
}

const GROUPS: readonly { severity: Finding['severity']; title: string; icon: LucideIcon; tone: string }[] = [
  { severity: 'block', title: 'Blocks approval', icon: CircleX, tone: 'text-danger' },
  { severity: 'warn', title: 'Worth a look', icon: TriangleAlert, tone: 'text-warn' },
  { severity: 'info', title: 'Notes', icon: Info, tone: 'text-ink-subtle' },
];

/** Where a finding points, beyond the post text itself. */
const FIELD_PLACES: Readonly<Record<string, string>> = {
  documentTitle: 'Document title',
  carouselSpec: 'The slides',
  poll: 'The poll',
};

function where(finding: Finding): string | undefined {
  const slide = findingSlide(finding);
  if (slide !== undefined) return `Slide ${String(slide + 1)}`;
  const field = findingField(finding);
  return field === undefined ? undefined : FIELD_PLACES[field];
}

function summary(blocking: number, others: number, checking: boolean): string {
  if (checking && blocking === 0 && others === 0) return 'Checking the text…';
  const lead =
    blocking === 0
      ? 'Nothing blocks approval.'
      : `${String(blocking)} ${blocking === 1 ? 'problem blocks' : 'problems block'} approval.`;
  const rest = others === 0 ? '' : ` ${String(others)} more worth a look.`;
  return `${lead}${rest}${checking ? ' Checking your latest changes…' : ''}`;
}

/**
 * The post lint (plan: "LinkedIn Studio / Lint"), live as you type: blocking findings first
 * (they stop approval), then warnings. Each finding says where it is and jumps there. The
 * summary is the only live region, so a screen reader hears one short sentence per change.
 */
export function LintPanel({ findings, truncated = 0, checking, error, onRetry, onShowInText, onShowSlide }: LintPanelProps) {
  const headingPrefix = useId();
  const blocking = findings.filter((finding) => finding.severity === 'block').length;
  const others = findings.length - blocking;

  return (
    <div className="grid gap-4" data-slot="lint-panel">
      <p role="status" className={cn('text-sm font-medium', blocking > 0 ? 'text-danger' : 'text-ink')}>
        {summary(blocking, others, checking)}
      </p>
      {error ? (
        <ProblemAlert error={error} title="Couldn’t check the latest text." onRetry={onRetry} retryLabel="Check the text again" />
      ) : null}
      {GROUPS.map(({ severity, title, icon: Icon, tone }) => {
        const group = findings.filter((finding) => finding.severity === severity);
        if (group.length === 0) return null;
        const headingId = `${headingPrefix}-${severity}`;
        return (
          <section key={severity} aria-labelledby={headingId}>
            <h3 id={headingId} className="text-sm font-semibold text-ink">
              {`${title} (${String(group.length)})`}
            </h3>
            <ul className="mt-2 divide-y divide-line border-y border-line">
              {group.map((finding, index) => {
                const place = where(finding);
                const slide = findingSlide(finding);
                const inText = findingInText(finding) && typeof finding.start === 'number' && typeof finding.end === 'number';
                return (
                  <li
                    key={`${finding.code}-${String(finding.start ?? '')}-${String(index)}`}
                    data-code={finding.code}
                    className="flex items-start gap-3 py-3"
                  >
                    <Icon aria-hidden="true" className={cn('mt-0.5 size-4 shrink-0', tone)} />
                    <div className="grid min-w-0 flex-1 gap-1">
                      <p className="text-sm text-ink">{finding.message}</p>
                      {place || inText ? (
                        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-ink-muted">
                          {place ? <span>{place}</span> : null}
                          {inText ? (
                            <Button
                              variant="link"
                              size="sm"
                              onClick={() => {
                                onShowInText(finding);
                              }}
                            >
                              Show in the text
                            </Button>
                          ) : null}
                          {slide !== undefined ? (
                            <Button
                              variant="link"
                              size="sm"
                              onClick={() => {
                                onShowSlide(slide);
                              }}
                            >
                              {`Go to slide ${String(slide + 1)}`}
                            </Button>
                          ) : null}
                        </div>
                      ) : null}
                    </div>
                  </li>
                );
              })}
            </ul>
          </section>
        );
      })}
      {truncated > 0 ? <p className="text-sm text-ink-muted">{`And ${String(truncated)} more not shown.`}</p> : null}
    </div>
  );
}
