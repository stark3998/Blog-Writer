import { X } from 'lucide-react';
import type { ReactNode } from 'react';

import { Button } from '@/components/ui/button';
import { JobTimeline } from '@/features/jobs/job-timeline';
import { useJobStatus } from '@/features/jobs/use-job';
import { cn } from '@/lib/cn';
import { isLiveJobStatus } from '@/lib/events/events';

export interface JobPanelProps {
  jobId: string;
  /** The timeline's heading: "Building your guide". */
  title: string;
  labels?: Readonly<Record<string, string>>;
  /** Shown under the steps once the job succeeded ("6 entries are waiting for your review."). */
  done?: ReactNode;
  onDismiss: () => void;
  className?: string;
}

/**
 * A job the owner started from this page (a 202 `JobAccepted`): its steps live from the event
 * stream, then what came of it. It can be hidden once it has finished; the job itself keeps
 * running if the owner leaves.
 */
export function JobPanel({ jobId, title, labels, done, onDismiss, className }: JobPanelProps) {
  const status = useJobStatus(jobId);
  const finished = !isLiveJobStatus(status);
  return (
    <div className={cn('relative rounded-lg border border-line bg-sunken p-4 pr-12', className)}>
      <JobTimeline jobId={jobId} title={title} labels={labels} />
      {status === 'succeeded' && done ? <div className="mt-3 text-sm text-ink">{done}</div> : null}
      <Button
        variant="ghost"
        size="icon-sm"
        className="absolute right-2 top-2"
        aria-label={finished ? `Hide: ${title}` : `Hide the progress of: ${title}`}
        onClick={onDismiss}
      >
        <X aria-hidden="true" />
      </Button>
    </div>
  );
}
