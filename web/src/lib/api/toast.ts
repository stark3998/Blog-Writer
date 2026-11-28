import { toast } from 'sonner';

import { ApiError } from './errors';
import { isSessionError, isSilentError, problemMessage } from './problems';

export interface ErrorToastOptions {
  /**
   * What failed, as a short sentence in the action's own words ("Couldn’t save the pillars.").
   * The problem's sentence then follows as the description. Without it the problem's sentence
   * is the title.
   */
  readonly title?: string;
  /** One follow-up, usually "Try again". */
  readonly action?: { readonly label: string; readonly onClick: () => void };
  /** Replaces an earlier toast with the same id instead of stacking another. */
  readonly id?: string | number;
}

/** False for errors another part of the app already shows (sign-in, "Not authorised") or that mean nothing (a cancelled request). */
export function shouldToastError(error: unknown): boolean {
  return !isSessionError(error) && !isSilentError(error);
}

/** "Request ID <id>." for support, from the `X-Request-Id` header (contract section 10). */
export function requestIdLine(error: unknown): string | undefined {
  return error instanceof ApiError && error.requestId ? `Request ID ${error.requestId}.` : undefined;
}

/**
 * Shows an error toast with the owner-facing sentence and the request id. Does nothing for errors
 * the session handling already shows or for a cancelled request. "No connection" toasts replace
 * each other, so a burst of failed calls shows one.
 */
export function toastError(error: unknown, { title, action, id }: ErrorToastOptions = {}): void {
  if (!shouldToastError(error)) return;
  const message = problemMessage(error);
  const description = [title === undefined ? undefined : message, requestIdLine(error)].filter(Boolean).join(' ');
  const offline = error instanceof ApiError && error.kind === 'network';
  toast.error(title ?? message, {
    description: description === '' ? undefined : description,
    id: id ?? (offline ? 'api-network-error' : undefined),
    action,
  });
}

/** A success toast in the action's own words: the button said "Save", the toast says "Saved." */
export function toastSuccess(message: string, options: { readonly id?: string | number } = {}): void {
  toast.success(message, options);
}
