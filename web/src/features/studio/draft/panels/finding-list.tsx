import { CircleX, Info, TriangleAlert, type LucideIcon } from 'lucide-react';
import { useId } from 'react';

import { Button } from '@/components/ui/button';
import { cn } from '@/lib/cn';

import { hasSpan, SEVERITY_LABELS } from '../model/gates';
import type { Finding, Severity } from '../model/types';

const SEVERITY_ICONS: Readonly<Record<Severity, { icon: LucideIcon; className: string }>> = {
  block: { icon: CircleX, className: 'text-danger' },
  warn: { icon: TriangleAlert, className: 'text-warn' },
  info: { icon: Info, className: 'text-ink-subtle' },
};

function FindingRow({ finding, onShow }: { finding: Finding; onShow: ((finding: Finding) => void) | undefined }) {
  const messageId = useId();
  const { icon: Icon, className } = SEVERITY_ICONS[finding.severity];
  return (
    <li className="flex gap-2.5 py-2.5">
      <Icon aria-hidden="true" className={cn('mt-0.5 size-4 shrink-0', className)} />
      <div className="min-w-0 flex-1">
        <p id={messageId} className="text-sm text-ink">
          {finding.message}
        </p>
        <p className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-ink-subtle">
          <span className={cn('font-medium', className)}>{SEVERITY_LABELS[finding.severity]}</span>
          <code className="font-mono">{finding.code}</code>
          {finding.severity === 'block' && finding.overridable ? <span>Can be overridden</span> : null}
          {onShow && hasSpan(finding) ? (
            <Button
              variant="link"
              size="sm"
              className="text-xs"
              aria-describedby={messageId}
              onClick={() => {
                onShow(finding);
              }}
            >
              Show in text
            </Button>
          ) : null}
        </p>
      </div>
    </li>
  );
}

export interface FindingListProps {
  findings: readonly Finding[];
  /** Jumps to the finding's span in the text; left out where there is no text to jump to. */
  onShow?: (finding: Finding) => void;
  /** Findings the server did not store (`GateResult.truncated`). */
  truncated?: number;
  className?: string;
}

/** Findings with their severity in an icon and in words, and a way to the text they point at. */
export function FindingList({ findings, onShow, truncated = 0, className }: FindingListProps) {
  return (
    <div className={className}>
      <ul className="divide-y divide-line">
        {findings.map((finding, index) => (
          <FindingRow key={`${finding.code}-${String(finding.start ?? 'x')}-${String(index)}`} finding={finding} onShow={onShow} />
        ))}
      </ul>
      {truncated > 0 ? (
        <p className="mt-2 text-xs text-ink-subtle">
          {`And ${String(truncated)} more not kept with the report. The live checks list every one.`}
        </p>
      ) : null}
    </div>
  );
}
