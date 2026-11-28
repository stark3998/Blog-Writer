import { Check, CircleAlert, CloudOff, LoaderCircle, PencilLine } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { cn } from '@/lib/cn';

import { formatTime } from '../model/format';

import type { SaveStatus } from './use-draft-session';

interface Line {
  readonly icon: typeof Check;
  readonly text: string;
  readonly tone: 'muted' | 'warn' | 'danger';
  readonly spin?: boolean;
}

function lineFor(status: SaveStatus, dirty: boolean): Line {
  switch (status.kind) {
    case 'idle':
      return dirty ? { icon: PencilLine, text: 'Unsaved changes', tone: 'muted' } : { icon: Check, text: 'Saved', tone: 'muted' };
    case 'pending':
      return { icon: PencilLine, text: 'Unsaved changes', tone: 'muted' };
    case 'saving':
      return { icon: LoaderCircle, text: 'Saving…', tone: 'muted', spin: true };
    case 'saved':
      return dirty
        ? { icon: PencilLine, text: 'Unsaved changes', tone: 'muted' }
        : { icon: Check, text: `Saved at ${formatTime(status.at)}`, tone: 'muted' };
    case 'offline':
      return { icon: CloudOff, text: 'Offline: saves when you’re back', tone: 'warn' };
    case 'retrying':
      return { icon: LoaderCircle, text: 'Couldn’t save: trying again', tone: 'warn', spin: true };
    case 'failed':
      return { icon: CircleAlert, text: 'Not saved', tone: 'danger' };
    case 'conflict':
      return { icon: CircleAlert, text: 'Not saved: changed elsewhere', tone: 'danger' };
    case 'read-only':
      return { icon: CircleAlert, text: 'Not saved: now read-only', tone: 'danger' };
    case 'too-large':
      return { icon: CircleAlert, text: 'Not saved: over 128 KB', tone: 'danger' };
    case 'invalid':
      return { icon: CircleAlert, text: 'Not saved: check the details', tone: 'danger' };
  }
}

export interface SaveStatusLineProps {
  status: SaveStatus;
  dirty: boolean;
  onRetry: () => void;
  onResolve: () => void;
  className?: string;
}

/**
 * The save state, beside the status: quiet while all is well (it changes every few seconds, so
 * it is not a live region); problems are explained in an alert in the page as well.
 */
export function SaveStatusLine({ status, dirty, onRetry, onResolve, className }: SaveStatusLineProps) {
  const line = lineFor(status, dirty);
  const Icon = line.icon;
  return (
    <span
      data-save-state={status.kind}
      className={cn(
        'inline-flex items-center gap-1.5 text-sm',
        line.tone === 'muted' && 'text-ink-subtle',
        line.tone === 'warn' && 'text-warn',
        line.tone === 'danger' && 'font-medium text-danger',
        className,
      )}
    >
      <Icon aria-hidden="true" className={cn('size-4 shrink-0', line.spin && 'animate-pulse-soft')} />
      {line.text}
      {status.kind === 'failed' ? (
        <Button variant="link" size="sm" className="ml-1" onClick={onRetry}>
          Try again
        </Button>
      ) : null}
      {status.kind === 'conflict' ? (
        <Button variant="link" size="sm" className="ml-1" onClick={onResolve}>
          Compare
        </Button>
      ) : null}
    </span>
  );
}
