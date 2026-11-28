import { cn } from '@/lib/cn';

interface MeterProps {
  /** What it measures: "Relevance". */
  label: string;
  /** 0-1. */
  value: number;
  className?: string;
}

/**
 * A score out of 100 as a thin bar and a number (`role="meter"`: a measurement, not progress).
 * Ink, not a state colour: a score is information, not a verdict.
 */
export function Meter({ label, value, className }: MeterProps) {
  const percent = Math.round(Math.min(Math.max(value, 0), 1) * 100);
  return (
    <div className={cn('grid grid-cols-[5.5rem_minmax(0,1fr)_2.75rem] items-center gap-2 text-sm', className)}>
      <span className="text-ink-muted" aria-hidden="true">
        {label}
      </span>
      <div
        role="meter"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent}
        aria-valuetext={`${String(percent)}%`}
        className="h-1.5 overflow-hidden rounded-full bg-wash-strong"
      >
        <div className="h-full rounded-full bg-ink" style={{ width: `${String(percent)}%` }} />
      </div>
      <span aria-hidden="true" className="text-right tabular-nums text-ink">{`${String(percent)}%`}</span>
    </div>
  );
}
