import { RotateCw } from 'lucide-react';

import { Alert, AlertActions, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { ApiError } from '@/lib/api/errors';
import { isSessionError, isSilentError, problemMessage } from '@/lib/api/problems';

export interface ProblemAlertProps {
  /** The failed query's or write's error. */
  error: unknown;
  /** What failed, in the screen's words: "Couldn’t load the ideas." */
  title?: string;
  /** Offers "Try again" (a query's `refetch`). */
  onRetry?: () => void;
  /** Names the retry for screen readers when several are on one page ("Try loading the ideas again"). */
  retryLabel?: string;
  className?: string;
}

/**
 * An inline error for a query or write that failed: the owner-facing sentence, the request id
 * for support, and "Try again" when it can help. Renders nothing for errors the session handling
 * already shows (sign-in, "Not authorised") or for a cancelled request.
 */
export function ProblemAlert({ error, title, onRetry, retryLabel, className }: ProblemAlertProps) {
  if (isSessionError(error) || isSilentError(error)) return null;
  const requestId = error instanceof ApiError ? error.requestId : undefined;
  return (
    <Alert variant="error" className={className}>
      {title ? <AlertTitle>{title}</AlertTitle> : null}
      <AlertDescription>
        <p>{problemMessage(error)}</p>
        {requestId ? (
          <p className="text-xs">
            Request ID <code className="font-mono">{requestId}</code>
          </p>
        ) : null}
      </AlertDescription>
      {onRetry ? (
        <AlertActions>
          <Button size="sm" variant="secondary" aria-label={retryLabel} onClick={onRetry}>
            <RotateCw aria-hidden="true" />
            Try again
          </Button>
        </AlertActions>
      ) : null}
    </Alert>
  );
}
